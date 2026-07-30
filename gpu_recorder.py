"""GPU-native replay buffer — the SteelSeries / ShadowPlay approach.

Drives a small native helper (bin/wgcrec.exe, built in Rust with the
windows-capture crate) that captures the foreground window (or the monitor when
there's no game in front) via **Windows Graphics Capture** and encodes it on the
GPU's **hardware encoder** through Media Foundation — with no CPU readback. That
combination is what keeps a full 60fps even when a game pins the GPU at 100%,
where Desktop Duplication (ddagrab) and the CPU-readback WGC path both collapse
to ~12fps.

The helper writes a rolling ring of short .mp4 segments; a clip trims the last N
seconds out of that ring and muxes in the app's mixed audio.

Falls back cleanly: if the helper can't produce segments, `available` stays
False and the app keeps using the old CPU capture path (ScreenCapture).
"""
import os
import glob
import time
import shutil
import threading
import subprocess

from config import (WGCREC_PATH, FFMPEG_PATH, APP_DIR, SUBPROCESS_FLAGS,
                    GPU_CODEC_FLAGS, diag)


class GpuRecorder(threading.Thread):
    SEGMENT_SECONDS = 2           # ring granularity (also the max clip-tail loss)
    RING_MARGIN = 6               # keep a few seconds beyond clip_length
    SEG_DIR = os.path.join(APP_DIR, 'replay_buffer')

    def __init__(self, main):
        super().__init__(daemon=True)
        self.main = main
        self.proc = None
        self._running = False
        self.available = False
        self.backend = 'gpu-wgc'

    def _cfg_int(self, key, default):
        try:
            return int(self.main.read_from_file(key))
        except (TypeError, ValueError):
            return default

    def _clip_length(self):
        return max(self._cfg_int('clip_length', 60), 1)

    def _fps(self):
        return max(self._cfg_int('fps', 60), 1)

    def _fallback_monitor(self):
        # app config is 0-based (monitor=1 -> 2nd display); windows-capture
        # Monitor::from_index is 1-based, so add one.
        return self._cfg_int('monitor', 0) + 1

    def _uses_hw(self):
        # WGC + Media Foundation hardware encode is available on any modern
        # Windows GPU; we always prefer it and fall back only if it fails to run.
        return True

    def _current_args(self):
        """The settings the recorder depends on (monitor, fps, clip length). Read
        live so changing any of them restarts the capture with the new value —
        monitor/fps change what's captured, clip length changes the ring size."""
        return (self._fallback_monitor(), self._fps(), self._clip_length())

    def _launch(self):
        """(Re)start wgcrec for the current monitor/fps. Clears the ring first
        (a new monitor may be a different size). Returns True once it's producing
        segments."""
        for f in glob.glob(os.path.join(self.SEG_DIR, 'seg*.mp4')):
            try:
                os.remove(f)
            except Exception:
                pass
        mon, fps, clip_len = self._current_args()
        ring = int((clip_len + self.RING_MARGIN) / self.SEGMENT_SECONDS) + 2
        cmd = [WGCREC_PATH, self.SEG_DIR, str(mon),
               str(self.SEGMENT_SECONDS), str(ring), str(fps)]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE,
                                         creationflags=SUBPROCESS_FLAGS)
        except Exception as e:
            diag(f'gpu recorder: wgcrec launch failed: {e}')
            return False
        for _ in range(20):  # ~10s to produce the first segment
            if self.proc.poll() is not None:
                diag('gpu recorder: wgcrec exited early')
                return False
            if glob.glob(os.path.join(self.SEG_DIR, 'seg*.mp4')):
                self.available = True
                diag(f'gpu replay buffer live (wgc monitor {mon}, {fps}fps)')
                return True
            time.sleep(0.5)
        diag('gpu recorder: no segments after 10s')
        self._kill()
        return False

    def run(self):
        self._running = True
        try:
            os.makedirs(self.SEG_DIR, exist_ok=True)
        except Exception as e:
            diag(f'gpu recorder: cannot make buffer dir: {e}')
            self.available = False
            return

        if not self._launch():
            self.available = False   # app falls back to the CPU path
            return
        launched = self._current_args()

        # Supervise: restart wgcrec if it dies OR the monitor/fps setting changes
        # (so picking a different monitor in settings takes effect immediately).
        while self._running:
            time.sleep(0.5)
            if self.proc is None or self.proc.poll() is not None:
                diag('gpu recorder: wgcrec exited, restarting')
                self.available = False
                self._launch()
                launched = self._current_args()
            elif self._current_args() != launched:
                diag(f'gpu recorder: settings changed {launched} -> '
                     f'{self._current_args()}, restarting on new monitor')
                self.available = False
                self._kill()
                self._launch()
                launched = self._current_args()
        self._kill()

    def _kill(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def stop(self):
        self._running = False
        self._kill()

    def save_clip(self, out_path, audio_getter=None):
        """Trim the last clip_length seconds out of the segment ring, join it into
        one continuous stream, and re-encode to a constant frame rate with audio
        muxed in — producing a smooth, in-sync clip.

        `audio_getter(t0, t1)` returns a wav path for wall-clock window [t0, t1]
        (or None). We anchor that window to the newest segment's finish time so
        the audio lines up with the video's actual end instead of 'now'."""
        clip_length = self._clip_length()
        tmp = []  # temp files to clean up
        try:
            segs = sorted(glob.glob(os.path.join(self.SEG_DIR, 'seg*.mp4')),
                          key=os.path.getmtime)
        except Exception:
            segs = []
        if len(segs) >= 2:
            segs = segs[:-1]   # drop the segment currently being written
        if not segs:
            diag('gpu recorder: no segments to clip')
            return False

        need = int((clip_length + self.SEGMENT_SECONDS) / self.SEGMENT_SECONDS) + 1
        use = segs[-need:]
        video_end = max(os.path.getmtime(s) for s in use)  # ~wall-clock end of video

        try:
            # 1) remux each segment mp4 -> annexb .ts (copy)
            ts_parts = []
            for i, s in enumerate(use):
                t = os.path.join(self.SEG_DIR, f'_t{i}.ts')
                r = subprocess.run(
                    [FFMPEG_PATH, '-y', '-hide_banner', '-loglevel', 'error',
                     '-i', s, '-c', 'copy', '-bsf:v', 'h264_mp4toannexb', t],
                    creationflags=SUBPROCESS_FLAGS, timeout=30)
                if r.returncode == 0 and os.path.exists(t):
                    ts_parts.append(t)
                    tmp.append(t)
            if not ts_parts:
                diag('gpu recorder: no ts parts')
                return False

            # 2) join the ts parts with the concat DEMUXER (a list file), which
            #    offsets each part's timestamps so the joined stream has one
            #    continuous timeline (the concat protocol left PTS jumps at every
            #    segment boundary -> a freeze per boundary).
            list_path = os.path.join(self.SEG_DIR, '_list.txt')
            tmp.append(list_path)
            with open(list_path, 'w', encoding='utf-8') as f:
                for t in ts_parts:
                    f.write("file '%s'\n" % t.replace('\\', '/'))
            full = os.path.join(self.SEG_DIR, '_full.ts')
            tmp.append(full)
            r = subprocess.run(
                [FFMPEG_PATH, '-y', '-hide_banner', '-loglevel', 'error',
                 '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', full],
                creationflags=SUBPROCESS_FLAGS, timeout=60)
            if r.returncode != 0 or not os.path.exists(full):
                diag('gpu recorder: concat failed')
                return False

            # 3) audio for the exact video window
            audio_path = None
            if audio_getter:
                try:
                    audio_path = audio_getter(video_end - clip_length, video_end)
                except Exception as e:
                    diag(f'gpu recorder: audio getter error: {e}')
                if audio_path:
                    tmp.append(audio_path)

            # 4) take the last clip_length seconds and RE-ENCODE to a constant
            #    frame rate. Copying preserved the segments' variable pacing and
            #    boundary glitches; a CFR re-encode (on the GPU hardware encoder)
            #    lays down an even timeline so playback is smooth, and muxes audio.
            fps = self._fps()
            cmd = [FFMPEG_PATH, '-y', '-hide_banner', '-loglevel', 'error',
                   '-sseof', f'-{clip_length}', '-i', full]
            if audio_path and os.path.exists(audio_path):
                cmd += ['-i', audio_path]
            cmd += ['-vf', f'fps={fps}', *GPU_CODEC_FLAGS, '-pix_fmt', 'yuv420p']
            if audio_path and os.path.exists(audio_path):
                cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest']
            cmd += ['-movflags', '+faststart', out_path]
            r = subprocess.run(cmd, creationflags=SUBPROCESS_FLAGS, timeout=180)
            ok = r.returncode == 0 and os.path.exists(out_path) \
                and os.path.getsize(out_path) > 1024
            if not ok:
                diag('gpu recorder: final encode failed')
            return ok
        except Exception as e:
            diag(f'gpu recorder: save_clip error: {e}')
            return False
        finally:
            for p in tmp:
                try:
                    os.remove(p)
                except Exception:
                    pass
