import os
import keyboard
import customtkinter
import numpy
import tkinter as tk
import sounddevice as sd
import soundcard as sc
import ffmpeg
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

# Gets the gpu brand and returns the appropriate flags
def get_gpu():
    try:
        cmd = 'PowerShell -Command "Get-CimInstance Win32_VideoController | Select-Object Name | ConvertTo-Json'
        output = subprocess.check_output(cmd, shell=True, text=True).strip()
        gpu_name_lower = output.lower()

        print('GPU: ' + output)

        if not output:
            print("No GPU data returned from system query. Defaulting to CPU.")
            return ['-c:v', 'libx264', '-preset', 'ultrafast']

        # Parse the output (handles single or multiple GPUs safely)
        gpu_data = json.loads(output)
        gpu_names = ""

        if isinstance(gpu_data, list):
            gpu_names = " ".join([gpu['Name'] for gpu in gpu_data if 'Name' in gpu]).lower()
        elif isinstance(gpu_data, dict) and 'Name' in gpu_data:
            gpu_names = gpu_data['Name'].lower()

        if 'nvidia' in gpu_names:
            return ['-c:v', 'h264_nvenc', '-preset', 'p1']

        elif 'amd' in gpu_names:
            return ['-c:v', 'h264_amf', '-quality', 'speed']
        elif 'inter' in gpu_names:
            return ['-c:v', 'h264_qsv', '-preset', 'veryfast']
    except:
        print('Detection Failed! Defaulting to CPU encoding')

    return ['-c:v', 'libx264', '-preset', 'ultrafast']

GPU_CODEC_FLAGS = get_gpu()

# Absolute path to the bundled ffmpeg so it resolves no matter the launch directory
FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg.exe')


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
                print(f"Audio source not found: {self.device_name!r} (loopback={self.loopback})")
                return
            self._running = True
            with mic.recorder(samplerate=SAMPLE_RATE, channels=AUDIO_CHANNELS) as rec:
                while self._running:
                    data = rec.record(numframes=AUDIO_CHUNK)
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

    def get_last_n_seconds(self, n):
        cutoff = time.time() - n
        with self.lock:
            chunks = [d for ts, d in self.buffer if ts >= cutoff]
        if not chunks:
            return None
        return numpy.concatenate(chunks, axis=0)

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

    def get_clip_audio(self, seconds):
        """Returns the last `seconds` of mixed audio (float32 stereo) or None."""
        arrays = []
        for s in self.sources:
            a = s.get_last_n_seconds(seconds)
            if a is not None and len(a):
                arrays.append(a)
        if not arrays:
            return None
        if len(arrays) == 1:
            return arrays[0]

        # Align to the shortest source (take the most-recent samples of each) and sum
        n = min(len(a) for a in arrays)
        mixed = numpy.sum([a[-n:] for a in arrays], axis=0)

        # Soft protection against clipping when several loud sources overlap
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


class ScreenCapture(threading.Thread):
    """Grabs frames on a background thread into a rolling JPEG ring buffer.

    Moving the grab + encode off the Tkinter main thread keeps the GUI
    responsive; the main thread only reads settings and snapshots the buffer."""

    def __init__(self, main):
        super().__init__(daemon=True)
        self.main = main
        self.frames = deque()
        self.lock = threading.Lock()
        self._running = False
        self.monitor = None
        self.width = 0
        self.height = 0

    def run(self):
        self._running = True
        try:
            sct = mss.MSS()
        except Exception as e:
            print(f'Screen capture init error: {e}')
            return
        monitors = sct.monitors
        next_frame = time.time()

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
                # Sleep in small slices so fps/monitor changes are picked up quickly
                time.sleep(min(next_frame - now, 0.004))
                continue

            # Resolve the monitor to capture (UI index is 0-based)
            try:
                monitor = monitors[bc.monitor + 1]
            except Exception:
                monitor = monitors[1] if len(monitors) > 1 else monitors[0]
            self.monitor = monitor
            self.width = monitor['width']
            self.height = monitor['height']
            self.main.monitor = monitor  # compile_clip reads dimensions from here

            try:
                img = sct.grab(monitor)
                frame = numpy.array(img)  # BGRA, writable copy
                if getattr(bc, 'mouse_enabled', 0):
                    draw_cursor(frame, monitor)
                ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                if ok:
                    ts = time.time()
                    with self.lock:
                        self.frames.append((ts, buf.tobytes()))
                        # Cull frames older than the clip length (O(1) from the left)
                        cutoff = ts - clip_length
                        while self.frames and self.frames[0][0] < cutoff:
                            self.frames.popleft()
            except Exception:
                print('Grab Monitor Error!!!')

            next_frame += frame_interval
            # Drift correction if we fell far behind (heavy load / slow monitor)
            if now - next_frame > frame_interval * 3:
                next_frame = time.time() + frame_interval

    def snapshot(self):
        """Returns (ordered_jpeg_frames, duration_seconds) and clears the buffer."""
        with self.lock:
            items = list(self.frames)
            self.frames.clear()
        if not items:
            return [], 0.0
        duration = items[-1][0] - items[0][0]
        return [f for ts, f in items], duration

    def clear(self):
        with self.lock:
            self.frames.clear()

    def stop(self):
        self._running = False