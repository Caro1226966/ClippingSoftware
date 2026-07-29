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

from config import WGCREC_PATH, FFMPEG_PATH, APP_DIR, SUBPROCESS_FLAGS, diag


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

    def run(self):
        self._running = True
        try:
            shutil.rmtree(self.SEG_DIR, ignore_errors=True)
            os.makedirs(self.SEG_DIR, exist_ok=True)
        except Exception as e:
            diag(f'gpu recorder: cannot make buffer dir: {e}')
            self.available = False
            return

        ring = int((self._clip_length() + self.RING_MARGIN) / self.SEGMENT_SECONDS) + 2
        cmd = [WGCREC_PATH, self.SEG_DIR, str(self._fallback_monitor()),
               str(self.SEGMENT_SECONDS), str(ring), str(self._fps())]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE,
                                         creationflags=SUBPROCESS_FLAGS)
        except Exception as e:
            diag(f'gpu recorder: wgcrec launch failed: {e}')
            self.available = False
            return

        # Wait for it to actually start producing segments; if it can't (no WGC /
        # no hardware encoder), give up so the app falls back to the CPU path.
        for _ in range(20):  # ~10s
            if self.proc.poll() is not None:
                err = b''
                try:
                    err = self.proc.stderr.read() or b''
                except Exception:
                    pass
                diag(f'gpu recorder: wgcrec exited early: '
                     f'{err.decode("utf-8", "replace")[:200]}')
                self.available = False
                return
            if glob.glob(os.path.join(self.SEG_DIR, 'seg*.mp4')):
                self.available = True
                diag('gpu replay buffer live (wgc + hardware encode)')
                break
            time.sleep(0.5)
        else:
            diag('gpu recorder: no segments after 10s, falling back')
            self._kill()
            self.available = False
            return

        # Keep the process alive; if it dies, restart it.
        while self._running:
            if self.proc.poll() is not None:
                diag('gpu recorder: wgcrec exited, restarting')
                self.available = False
                try:
                    self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.PIPE,
                                                 creationflags=SUBPROCESS_FLAGS)
                except Exception:
                    return
                for _ in range(20):
                    if glob.glob(os.path.join(self.SEG_DIR, 'seg*.mp4')):
                        self.available = True
                        break
                    time.sleep(0.5)
            time.sleep(0.5)
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

    def save_clip(self, out_path, audio_path=None):
        """Trim the last clip_length seconds out of the segment ring and mux in
        audio. Video is copied (no re-encode) so there's no quality loss."""
        clip_length = self._clip_length()
        try:
            segs = sorted(glob.glob(os.path.join(self.SEG_DIR, 'seg*.mp4')),
                          key=os.path.getmtime)
        except Exception:
            segs = []
        # Drop the segment currently being written (no moov atom yet)
        if len(segs) >= 2:
            segs = segs[:-1]
        if not segs:
            diag('gpu recorder: no segments to clip')
            return False

        need = int((clip_length + self.SEGMENT_SECONDS) / self.SEGMENT_SECONDS) + 1
        use = segs[-need:]

        list_path = os.path.join(self.SEG_DIR, '_concat.txt')
        full_path = os.path.join(self.SEG_DIR, '_full.mp4')
        try:
            with open(list_path, 'w', encoding='utf-8') as f:
                for s in use:
                    f.write("file '%s'\n" % s.replace('\\', '/'))
            r = subprocess.run(
                [FFMPEG_PATH, '-y', '-hide_banner', '-loglevel', 'error',
                 '-f', 'concat', '-safe', '0', '-i', list_path,
                 '-c', 'copy', full_path],
                creationflags=SUBPROCESS_FLAGS, timeout=60)
            if r.returncode != 0 or not os.path.exists(full_path):
                diag('gpu recorder: concat failed')
                return False

            cmd = [FFMPEG_PATH, '-y', '-hide_banner', '-loglevel', 'error',
                   '-sseof', f'-{clip_length}', '-i', full_path]
            if audio_path and os.path.exists(audio_path):
                cmd += ['-i', audio_path, '-c:v', 'copy',
                        '-c:a', 'aac', '-b:a', '192k', '-shortest']
            else:
                cmd += ['-c:v', 'copy']
            cmd += ['-movflags', '+faststart', out_path]
            r = subprocess.run(cmd, creationflags=SUBPROCESS_FLAGS, timeout=120)
            ok = r.returncode == 0 and os.path.exists(out_path) \
                and os.path.getsize(out_path) > 1024
            if not ok:
                diag('gpu recorder: final mux failed')
            return ok
        except Exception as e:
            diag(f'gpu recorder: save_clip error: {e}')
            return False
        finally:
            for p in (list_path, full_path):
                try:
                    os.remove(p)
                except Exception:
                    pass
