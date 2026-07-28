import os
import sys
import shutil
import keyboard
import customtkinter
import numpy
import tkinter as tk
import sounddevice as sd
import soundcard as sc
from soundcard import SoundcardRuntimeWarning
import warnings
# "data discontinuity in recording" fires whenever WASAPI loopback resumes after
# silence — it's expected and harmless, so keep it out of the console.
warnings.filterwarnings('ignore', category=SoundcardRuntimeWarning)
from PIL import Image, ImageDraw
import pystray
import threading
import csv
import mss
import time
import subprocess
from pathlib import Path
import cv2
import json
import wave
import tempfile
import ctypes
from collections import deque

# ── Paths (work both from source and from the packaged .exe) ────────────────
IS_FROZEN = getattr(sys, 'frozen', False)

# Stop ffmpeg / PowerShell subprocesses from flashing up a console window when
# the app runs as a windowed .exe. (Without this the window not only gets in the
# way, but closing it kills the child process — truncating a clip mid-encode.)
SUBPROCESS_FLAGS = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW

# Output resolutions the user can pick — value is the target height in pixels
# (width is derived to keep the aspect ratio).
RESOLUTION_MAP = {'320p': 320, '480p': 480, '720p': 720, '1080p': 1080, '4K': 2160}
RESOLUTION_OPTIONS = ['320p', '480p', '720p', '1080p', '4K']


def resource_path(*parts):
    """Path to a file bundled with the app (PyInstaller unpacks to _MEIPASS)."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


# Settings must live somewhere writable — the exe's own folder may be read-only
# and a startup-launched app runs with its working directory set to System32.
APP_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'),
                       'ClippingSoftware')
os.makedirs(APP_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(APP_DIR, 'defaults.csv')
LOG_PATH = os.path.join(APP_DIR, 'log.txt')

# First launch on this machine: seed the settings file from the bundled defaults
FIRST_RUN = not os.path.exists(CONFIG_PATH)
if FIRST_RUN:
    try:
        shutil.copyfile(resource_path('defaults.csv'), CONFIG_PATH)
    except Exception as e:
        print(f'Could not create settings file: {e}')


def _ensure_config_keys():
    """Add any settings a newer version introduced to an older user's file, so
    upgrades don't lose new options (write_to_file only updates existing keys)."""
    try:
        defaults, order = {}, []
        with open(resource_path('defaults.csv'), newline='') as f:
            for row in csv.reader(f):
                if len(row) >= 2:
                    defaults[row[0]] = row[1]
                    order.append(row[0])
        rows, have = [], set()
        with open(CONFIG_PATH, newline='') as f:
            for row in csv.reader(f):
                if row:
                    rows.append(row)
                    have.add(row[0])
        missing = [k for k in order if k not in have]
        if missing:
            for k in missing:
                rows.append([k, defaults[k]])
            with open(CONFIG_PATH, 'w', newline='') as f:
                csv.writer(f).writerows(rows)
    except Exception as e:
        print(f'Config migration error: {e}')


_ensure_config_keys()

root = tk.Tk()
SCREEN_WIDTH = root.winfo_screenwidth()
SCREEN_HEIGHT = root.winfo_screenheight()
root.destroy()

def create_icon_image():
    """Generates a simple 64x64 blue square icon image for the tray."""
    img = Image.new('RGB', (64, 64), color='#1f538d')
    # Optional: Draw a tiny white dot or design inside it
    d = ImageDraw.Draw(img)
    d.rectangle([(16, 16), (48, 48)], fill='white')
    return img

TRAY_ICON = create_icon_image() # Stores the icon image for if it is minimized to tray

# Encoder settings, per GPU brand. Each uses constant-quality rate control (the
# lower the number, the better it looks) rather than the encoders' low default
# bitrate — that default was making 1080p clips look soft/blocky. Encoding
# happens after the clip is captured, so a slower/higher-quality preset is fine.
CQ = '20'   # constant-quality target — visually clean, ~10-20 Mbps at 1080p60

CPU_CODEC = ['-c:v', 'libx264', '-preset', 'fast', '-crf', CQ]

# Gets the gpu brand and returns the appropriate flags
def get_gpu():
    try:
        cmd = 'PowerShell -Command "Get-CimInstance Win32_VideoController | Select-Object Name | ConvertTo-Json'
        output = subprocess.check_output(cmd, shell=True, text=True,
                                         creationflags=SUBPROCESS_FLAGS).strip()

        print('GPU: ' + output)

        if not output:
            print("No GPU data returned from system query. Defaulting to CPU.")
            return CPU_CODEC

        # Parse the output (handles single or multiple GPUs safely)
        gpu_data = json.loads(output)
        gpu_names = ""

        if isinstance(gpu_data, list):
            gpu_names = " ".join([gpu['Name'] for gpu in gpu_data if 'Name' in gpu]).lower()
        elif isinstance(gpu_data, dict) and 'Name' in gpu_data:
            gpu_names = gpu_data['Name'].lower()

        if 'nvidia' in gpu_names:
            return ['-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'vbr', '-cq', CQ, '-b:v', '0']
        elif 'amd' in gpu_names:
            return ['-c:v', 'h264_amf', '-rc', 'cqp', '-qp_i', CQ, '-qp_p', CQ, '-qp_b', CQ, '-quality', 'quality']
        elif 'intel' in gpu_names or 'inter' in gpu_names:
            return ['-c:v', 'h264_qsv', '-preset', 'medium', '-global_quality', CQ]
    except Exception:
        print('Detection Failed! Defaulting to CPU encoding')

    return CPU_CODEC


def _verify_encoder(flags):
    """Make sure the chosen GPU encoder actually works on this machine — some
    drivers advertise it but fail. Falls back to CPU (libx264) if it doesn't."""
    if flags is CPU_CODEC:
        return flags
    try:
        test = [FFMPEG_PATH, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=256x144:d=1:r=30',
                *flags, '-pix_fmt', 'yuv420p', '-f', 'null', '-']
        r = subprocess.run(test, capture_output=True, creationflags=SUBPROCESS_FLAGS, timeout=25)
        if r.returncode == 0:
            return flags
        print(f'GPU encoder {flags[1]} failed a test encode, using CPU (libx264).')
    except Exception as e:
        print(f'GPU encoder test error ({e}), using CPU (libx264).')
    return CPU_CODEC

# Absolute path to the bundled ffmpeg so it resolves no matter the launch directory
FFMPEG_PATH = resource_path('bin', 'ffmpeg.exe')

# Pick the encoder now (verifying the GPU one actually works), so the cost is
# paid once at startup rather than on every clip.
GPU_CODEC_FLAGS = _verify_encoder(get_gpu())


# Handles the location to save the clip (always in the user's pictures directory and in a folder called clipping)
SAVE_LOCATION = str(Path.home() / "Videos") + '\clipping'

# Checks if location exists. If not makes it. (avoids broken pipes)
if not os.path.exists(SAVE_LOCATION):
    os.mkdir(SAVE_LOCATION)

# Adds the clip part for easier searching
SAVE_LOCATION = str(SAVE_LOCATION +'\clip')
# print(SAVE_LOCATION)

# The microphone's block size
BLOCK_SIZE = 1024

# Every audio source is captured/mixed at this common format
SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2
# How many frames to pull per record() call (~46ms chunks)
AUDIO_CHUNK = 2048
# WASAPI capture buffer (~100ms) — bigger buffer tolerates the audio thread
# briefly stalling under load without dropping samples
AUDIO_BUFFER_BLOCK = SAMPLE_RATE // 10

# Quality of the JPEG frames held in the rolling buffer. This is the source the
# final clip is encoded from, so a low value makes even a high-bitrate clip look
# soft. 85 keeps the captured detail while using less RAM (and staying lighter
# on the system) than a near-lossless value.
JPEG_QUALITY = 85

# Hard cap on the video ring buffer so a long clip length can't eat all the RAM.
# Frames past this budget are dropped oldest-first (the clip just gets shorter).
MAX_BUFFER_BYTES = 1200 * 1024 * 1024


def write_wav(path, data, samplerate=SAMPLE_RATE):
    """Writes a float32 [-1, 1] numpy array (n_samples, channels) to a 16-bit WAV."""
    audio_int = numpy.clip(data, -1.0, 1.0)
    audio_int = (audio_int * 32767).astype(numpy.int16)
    channels = audio_int.shape[1] if audio_int.ndim > 1 else 1
    with wave.open(path, 'w') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(audio_int.tobytes())


class AudioSource(threading.Thread):
    """Continuously records one device (a real mic OR a speaker loopback) into a
    rolling ring buffer using the `soundcard` WASAPI backend."""

    def __init__(self, device_name, loopback, max_seconds):
        super().__init__(daemon=True)
        self.device_name = device_name
        self.loopback = loopback
        self.max_seconds = max_seconds
        self.buffer = deque()
        self.lock = threading.Lock()
        self._running = False

    # Finds the soundcard device whose name matches what the UI stored
    def _resolve(self):
        mics = sc.all_microphones(include_loopback=True)
        # Exact name match first, with the correct loopback flavour
        for m in mics:
            if m.name == self.device_name and m.isloopback == self.loopback:
                return m
        # Fall back to a forgiving substring match (names occasionally get truncated)
        for m in mics:
            if self.device_name and self.device_name in m.name and m.isloopback == self.loopback:
                return m
        # Saved device isn't on this machine (fresh install on someone else's PC)
        # — use whatever Windows has set as the default so audio works out of the box
        try:
            if self.loopback:
                return sc.get_microphone(str(sc.default_speaker().name), include_loopback=True)
            return sc.default_microphone()
        except Exception:
            print(f'Audio source not found: {self.device_name!r} (loopback={self.loopback})')
            return None

    def run(self):
        # soundcard's WASAPI/MediaFoundation backend needs COM initialised per thread
        try:
            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass
        try:
            mic = self._resolve()
            if mic is None:
                return
            self._running = True
            with mic.recorder(samplerate=SAMPLE_RATE, channels=AUDIO_CHANNELS,
                              blocksize=AUDIO_BUFFER_BLOCK) as rec:
                while self._running:
                    data = rec.record(numframes=AUDIO_CHUNK)
                    # Timestamp is the moment this chunk finished recording, so
                    # the chunk covers [ts - len/sr, ts] on the wall clock
                    ts = time.time()
                    with self.lock:
                        self.buffer.append((ts, data))
                        # Trim anything older than the buffer window
                        cutoff = ts - self.max_seconds
                        while self.buffer and self.buffer[0][0] < cutoff:
                            self.buffer.popleft()
        except Exception as e:
            print(f"Audio capture error ({self.device_name}): {e}")
        finally:
            self._running = False
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    def get_window(self, t_start, t_end):
        """Returns smooth audio of exactly (t_end - t_start) seconds for the clip.

        Consecutive recorded chunks are contiguous samples, so we simply
        concatenate them (click-free) and keep the most recent `target` samples
        — this lines the audio's end up with the end of the clip. Placing chunks
        individually by timestamp would introduce a tiny gap/overlap at every
        chunk boundary, which is exactly what makes the audio crackle."""
        target = int(round((t_end - t_start) * SAMPLE_RATE))
        if target <= 0:
            return None
        with self.lock:
            chunks = [d for _, d in self.buffer]
        if not chunks:
            return None
        audio = numpy.concatenate(chunks, axis=0)
        if audio.ndim == 1:
            audio = audio.reshape(-1, AUDIO_CHANNELS)
        if len(audio) >= target:
            return audio[-target:].copy()
        # Audio started a touch late — pad the beginning so the end still aligns
        pad = numpy.zeros((target - len(audio), AUDIO_CHANNELS), dtype=numpy.float32)
        return numpy.concatenate([pad, audio], axis=0)

    def clear(self):
        with self.lock:
            self.buffer.clear()

    def stop(self):
        self._running = False


class AudioManager:
    """Owns the enabled audio sources and mixes them for a clip.

    Reads the same config keys the UI writes (mic / mic_enabled /
    internal_mic / internal_mic_enabled) so the existing dropdowns and
    tickboxes keep working untouched."""

    def __init__(self, main):
        self.main = main
        self.sources = []

    def _read_int(self, pointer, default=0):
        try:
            return int(self.main.read_from_file(pointer))
        except (TypeError, ValueError):
            return default

    def _read_float(self, pointer, default):
        try:
            return float(self.main.read_from_file(pointer))
        except (TypeError, ValueError):
            return default

    def start(self):
        self.stop()
        # Keep a little headroom over the clip length so a full clip is always covered
        max_seconds = max(self._read_int('clip_length', 60) + 5, 15)

        sources = []
        # Microphone (real input device)
        if self._read_int('mic_enabled') == 1:
            name = self.main.read_from_file('mic')
            if name and name != 'None':
                sources.append(AudioSource(name, loopback=False, max_seconds=max_seconds))

        # Computer audio (speaker captured via WASAPI loopback)
        if self._read_int('internal_mic_enabled') == 1:
            name = self.main.read_from_file('internal_mic')
            if name and name != 'None':
                sources.append(AudioSource(name, loopback=True, max_seconds=max_seconds))

        self.sources = sources
        for s in self.sources:
            s.start()

    def restart(self):
        self.start()

    def clear(self):
        for s in self.sources:
            s.clear()

    def stop(self):
        for s in self.sources:
            s.stop()
        for s in self.sources:
            s.join(timeout=0.5)
        self.sources = []

    def get_clip_audio(self, t_start, t_end):
        """Returns the mixed audio (float32 stereo) for the wall-clock window
        [t_start, t_end], aligned to the video timeline, or None.

        Each source is scaled by its volume slider (0..1, where 0.5 = unchanged,
        1 = doubled). Scaling float samples is lossless, so turning a source up
        or down never degrades quality — the only cap is that the final mix is
        gently normalised if it would exceed full scale, which prevents clipping
        distortion."""
        mic_gain = 2.0 * self._read_float('mic_volume', 0.5)
        int_gain = 2.0 * self._read_float('internal_volume', 0.5)

        arrays = []
        for s in self.sources:
            a = s.get_window(t_start, t_end)
            if a is not None and len(a):
                gain = int_gain if s.loopback else mic_gain
                arrays.append(a * gain if gain != 1.0 else a)
        if not arrays:
            return None

        # Every source window is the same length, so they line up sample-for-sample
        n = min(len(a) for a in arrays)
        mixed = numpy.sum([a[:n] for a in arrays], axis=0) if len(arrays) > 1 else arrays[0]

        # Soft protection against clipping (loud sources or a boosted volume)
        peak = numpy.abs(mixed).max()
        if peak > 1.0:
            mixed = mixed / peak
        return mixed


# ── Mouse cursor overlay ─────────────────────────────────────────────────────
class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


# Classic arrow-pointer silhouette (relative pixels). Roughly matches a real
# Windows pointer size; bump _CURSOR_SCALE if you want it bigger.
_CURSOR_SCALE = 1
_CURSOR_SHAPE = numpy.array(
    [(0, 0), (0, 16), (4, 13), (7, 18), (9, 17), (6, 12), (11, 12)],
    dtype=numpy.int32,
) * _CURSOR_SCALE


def _cursor_pos():
    p = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def draw_cursor(frame, monitor):
    """Paints a pointer at the current mouse position onto a BGRA frame.

    mss does not capture the hardware cursor, so we draw it ourselves relative
    to the monitor being recorded."""
    x, y = _cursor_pos()
    x -= monitor['left']
    y -= monitor['top']
    if not (0 <= x < monitor['width'] and 0 <= y < monitor['height']):
        return
    pts = _CURSOR_SHAPE + (x, y)
    cv2.fillPoly(frame, [pts], (255, 255, 255, 255))
    cv2.polylines(frame, [pts], True, (0, 0, 0, 255), 1, cv2.LINE_AA)


def _monitor_hmon(mon):
    """HMONITOR handle for an mss monitor dict (its centre point)."""
    class _POINT(ctypes.Structure):
        _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
    cx = mon['left'] + mon['width'] // 2
    cy = mon['top'] + mon['height'] // 2
    return ctypes.windll.user32.MonitorFromPoint(_POINT(cx, cy), 2)  # NEAREST


class ScreenCapture(threading.Thread):
    """Grabs frames on a background thread into a rolling JPEG ring buffer.

    Prefers the GPU-based Desktop Duplication API (dxcam) — it reads a frame in
    ~1ms vs ~17ms for GDI BitBlt, and only delivers a frame when the screen
    actually changes, so an idle screen costs almost nothing. Falls back to mss
    (BitBlt) if dxcam is unavailable or fails, so capture always works.

    Either way the grab + encode stays off the Tkinter thread; the GUI only
    reads settings and snapshots the buffer."""

    HEARTBEAT = 0.5  # during a static screen, restamp the last frame this often

    def __init__(self, main):
        super().__init__(daemon=True)
        self.main = main
        self.frames = deque()
        self.lock = threading.Lock()
        self._running = False
        self._bytes = 0
        self.monitor = None
        self.width = 0
        self.height = 0
        self.backend = None
        self._fps_count = 0
        self._fps_since = 0.0
        self.capture_fps = 0.0        # last measured real capture rate (for diagnostics)

    # Frame storage (shared by both backends) --------------------------------
    def _append(self, ts, jpeg, clip_length):
        with self.lock:
            self.frames.append((ts, jpeg))
            self._bytes += len(jpeg)
            cutoff = ts - clip_length
            # Drop frames older than the clip length, or over the memory budget
            while self.frames and (self.frames[0][0] < cutoff or self._bytes > MAX_BUFFER_BYTES):
                _, old = self.frames.popleft()
                self._bytes -= len(old)

    def _count_fps(self, now, real):
        """Track and periodically log the actual capture rate. `real` is False
        for heartbeat/duplicate frames so the number reflects true new frames."""
        if real:
            self._fps_count += 1
        if self._fps_since == 0.0:
            self._fps_since = now
        elapsed = now - self._fps_since
        if elapsed >= 3.0:
            self.capture_fps = self._fps_count / elapsed
            print(f'Capture: {self.capture_fps:.1f} fps ({self.backend})')
            self._fps_count = 0
            self._fps_since = now

    def _set_monitor(self, mon):
        self.monitor = mon
        self.width = mon['width']
        self.height = mon['height']
        self.main.monitor = mon  # compile_clip reads dimensions from here

    def _resolve_monitor(self, monitors, bc):
        try:
            return monitors[bc.monitor + 1]
        except Exception:
            return monitors[1] if len(monitors) > 1 else monitors[0]

    def _safe_fps(self, bc):
        try:
            return max(int(bc.fps), 1)
        except (TypeError, ValueError):
            return 60

    def run(self):
        self._running = True
        # Windows Graphics Capture first — it's the only one of these that grabs
        # fullscreen games at their real framerate (dxcam/mss capture the
        # composited desktop, which fullscreen games bypass). Fall back through
        # dxcam (Desktop Duplication) then mss (GDI) if it's unavailable.
        for backend in (self._run_wgc, self._run_dxcam, self._run_mss):
            if not self._running:
                return
            try:
                if backend() is not False:
                    return
            except Exception as e:
                print(f'{backend.__name__} unavailable: {e}')

    # Best path: Windows Graphics Capture (captures fullscreen games) ---------
    def _run_wgc(self):
        import windows_capture  # optional; ImportError -> dxcam fallback

        with mss.MSS() as probe:
            monitors = probe.monitors

        while self._running:
            bc = self.main.button_callback
            # Re-create the session whenever the monitor, cursor toggle or fps
            # changes (these are fixed when the capture session is created)
            cur = (bc.monitor, bool(getattr(bc, 'mouse_enabled', 0)), self._safe_fps(bc))
            self._set_monitor(self._resolve_monitor(monitors, bc))
            st = {'last_store': 0.0, 'last_jpeg': None, 'clip_length': 60.0}

            try:
                cap = windows_capture.WindowsCapture(
                    cursor_capture=cur[1], draw_border=False,
                    minimum_update_interval=max(int(1000 / cur[2]), 1),
                    monitor_index=cur[0] + 1)
            except Exception as e:
                print(f'WGC create failed: {e}')
                return False

            def _changed():
                return (bc.monitor, bool(getattr(bc, 'mouse_enabled', 0)),
                        self._safe_fps(bc)) != cur

            def on_frame_arrived(frame, capture_control):
                # Runs on the capture's own thread. WGC delivers a frame each
                # time the screen changes (paced to the fps cap above), so a
                # fullscreen game is captured at its real rate.
                if not self._running or _changed():
                    try:
                        capture_control.stop()
                    except Exception:
                        pass
                    return
                now = time.time()
                try:
                    st['clip_length'] = float(bc.clip_length)
                except (TypeError, ValueError):
                    st['clip_length'] = 60.0
                try:
                    ok, buf = cv2.imencode('.jpg', frame.frame_buffer,
                                           [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                    if ok:
                        st['last_jpeg'] = buf.tobytes()
                        self._append(now, st['last_jpeg'], st['clip_length'])
                        st['last_store'] = now
                        self._count_fps(now, True)
                except Exception as e:
                    print(f'WGC encode error: {e}')

            def on_closed():
                pass

            cap.event(on_frame_arrived)
            cap.event(on_closed)
            self.backend = 'wgc'
            try:
                ctrl = cap.start_free_threaded()
            except Exception as e:
                print(f'WGC start failed: {e}')
                return False

            # Supervise on this thread: keep the timeline alive during static
            # screens (WGC only fires on change) and watch for setting changes
            while self._running and not _changed():
                time.sleep(0.1)
                now = time.time()
                if st['last_jpeg'] is not None and now - st['last_store'] >= self.HEARTBEAT:
                    self._append(now, st['last_jpeg'], st['clip_length'])
                    st['last_store'] = now
                    self._count_fps(now, False)

            try:
                ctrl.stop()
            except Exception:
                pass
            if not self._running:
                return True
            # a setting changed — loop round and rebuild the session

        return True

    # Fallback: Desktop Duplication via dxcam --------------------------------
    def _run_dxcam(self):
        import dxcam  # optional dependency; ImportError -> mss fallback

        # Map each dxcam output to its HMONITOR so we can follow the UI's
        # monitor selection regardless of GPU/output ordering. getattr avoids
        # Python name-mangling of the module's dunder `__factory` inside a class.
        factory = getattr(dxcam, '__factory')
        out_by_hmon = {}
        for di, dev in enumerate(factory.outputs):
            for oi, o in enumerate(dev):
                out_by_hmon[getattr(o, 'hmonitor', None)] = (di, oi)

        with mss.MSS() as probe:
            monitors = probe.monitors

        cam = None
        cur_key = None
        last_jpeg = None
        last_raw = None
        last_cursor = None
        last_store = 0.0
        next_frame = time.time()
        grab_errors = 0

        try:
            while self._running:
                bc = self.main.button_callback
                try:
                    fps = max(int(bc.fps), 1)
                except (TypeError, ValueError):
                    fps = 60
                frame_interval = 1.0 / fps
                try:
                    clip_length = float(bc.clip_length)
                except (TypeError, ValueError):
                    clip_length = 60.0

                mon = self._resolve_monitor(monitors, bc)
                key = out_by_hmon.get(_monitor_hmon(mon))
                if key is None:
                    return False  # can't map this monitor -> let mss handle it
                if key != cur_key:
                    self._release(cam)
                    cam = dxcam.create(device_idx=key[0], output_idx=key[1], output_color='BGR')
                    if cam is None:
                        return False
                    cur_key = key
                    self.backend = 'dxcam'
                    self._set_monitor(mon)
                    last_jpeg = last_raw = None

                now = time.time()
                if now < next_frame:
                    time.sleep(min(next_frame - now, 0.004))
                    continue

                try:
                    frame = cam.grab()  # BGR ndarray, or None when nothing changed
                    grab_errors = 0
                except Exception as e:
                    grab_errors += 1
                    if grab_errors >= 10:
                        print(f'dxcam grab failing, switching to mss: {e}')
                        return False
                    frame = None

                now = time.time()
                mouse = getattr(bc, 'mouse_enabled', 0)
                to_encode = None

                if frame is not None:
                    last_raw = frame
                    if mouse:
                        f = frame.copy()           # don't scribble on dxcam's buffer
                        draw_cursor(f, mon)
                        last_cursor = _cursor_pos()
                        to_encode = f
                    else:
                        to_encode = frame
                elif mouse and last_raw is not None and _cursor_pos() != last_cursor:
                    # Screen static but the cursor moved — refresh it on the last frame
                    f = last_raw.copy()
                    draw_cursor(f, mon)
                    last_cursor = _cursor_pos()
                    to_encode = f

                if to_encode is not None:
                    ok, buf = cv2.imencode('.jpg', to_encode, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                    if ok:
                        last_jpeg = buf.tobytes()
                        self._append(now, last_jpeg, clip_length)
                        last_store = now
                    self._count_fps(now, True)
                elif last_jpeg is not None and now - last_store >= self.HEARTBEAT:
                    # Keep the timeline current cheaply — reuse the same bytes
                    self._append(now, last_jpeg, clip_length)
                    last_store = now
                    self._count_fps(now, False)

                next_frame += frame_interval
                if now - next_frame > frame_interval * 3:
                    next_frame = time.time() + frame_interval

            return True
        finally:
            self._release(cam)

    @staticmethod
    def _release(cam):
        if cam is not None:
            try:
                cam.release()
            except Exception:
                pass

    # Fallback path: GDI BitBlt via mss --------------------------------------
    def _run_mss(self):
        self.backend = 'mss'
        try:
            sct = mss.MSS()
        except Exception as e:
            print(f'Screen capture init error: {e}')
            return
        # `with` releases the GDI device contexts/bitmaps when the thread stops
        with sct:
            monitors = sct.monitors
            next_frame = time.time()
            grab_errors = 0

            while self._running:
                bc = self.main.button_callback
                try:
                    fps = max(int(bc.fps), 1)
                except (TypeError, ValueError):
                    fps = 60
                frame_interval = 1.0 / fps
                try:
                    clip_length = float(bc.clip_length)
                except (TypeError, ValueError):
                    clip_length = 60.0

                now = time.time()
                if now < next_frame:
                    time.sleep(min(next_frame - now, 0.004))
                    continue

                mon = self._resolve_monitor(monitors, bc)
                self._set_monitor(mon)

                try:
                    frame = numpy.array(sct.grab(mon))  # BGRA
                    if getattr(bc, 'mouse_enabled', 0):
                        draw_cursor(frame, mon)
                    ts = time.time()
                    ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                    if ok:
                        self._append(ts, buf.tobytes(), clip_length)
                    self._count_fps(ts, True)
                    grab_errors = 0
                except Exception as e:
                    grab_errors += 1
                    if grab_errors == 1 or grab_errors % 120 == 0:
                        print(f'Grab Monitor Error ({grab_errors}): {e}')

                next_frame += frame_interval
                if now - next_frame > frame_interval * 3:
                    next_frame = time.time() + frame_interval

    def snapshot(self):
        """Returns (timestamped_frames, t_first, t_last) and clears the buffer.
        Frames keep their timestamps so the clip can be re-timed to constant fps."""
        with self.lock:
            items = list(self.frames)
            self.frames.clear()
            self._bytes = 0
        if not items:
            return [], 0.0, 0.0
        return items, items[0][0], items[-1][0]

    def clear(self):
        with self.lock:
            self.frames.clear()
            self._bytes = 0

    def stop(self):
        self._running = False


def build_cfr_frames(items, t_first, t_last):
    """Re-times variably-spaced captured frames to a constant frame rate.

    Screen-capture rate dips under load, so encoding the raw frames at one
    average fps makes laggy stretches play too fast. Instead we lay down an
    even output timeline and, for each tick, emit the frame that was actually
    on screen at that moment — real time maps 1:1 to playback time.

    Returns (ordered_jpeg_bytes, fps)."""
    if not items:
        return [], 30.0
    duration = t_last - t_first
    if duration <= 0:
        return [f for _, f in items], 30.0

    # Target the average capture rate: enough to show every frame, without
    # inflating the file by duplicating frames to some higher nominal fps
    fps = max(min(len(items) / duration, 120.0), 1.0)
    n_out = max(int(round(duration * fps)), 1)

    out = []
    j = 0
    for k in range(n_out):
        tk = t_first + k / fps
        # Advance to the most recent frame captured at or before this tick
        while j + 1 < len(items) and items[j + 1][0] <= tk:
            j += 1
        out.append(items[j][1])
    return out, fps