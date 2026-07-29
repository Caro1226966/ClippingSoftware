"""GPU-native replay buffer — the SteelSeries / ShadowPlay approach.

Instead of pulling each frame back to the CPU and JPEG-encoding it (which fights
a demanding game for the GPU and collapses to ~10fps), this captures with
ffmpeg's `ddagrab` (Desktop Duplication, frames stay on the GPU) and encodes on
the GPU's *hardware* encoder (NVENC/AMF/QSV) into a rolling ring of short .ts
segments. A clip just trims the last N seconds out of that ring and muxes in the
app's mixed audio. Capture + encode never touch the CPU or the graphics cores
rendering the game, so it holds a full 60fps even under a GPU-maxing title.

Falls back cleanly: if ddagrab or a hardware encoder isn't available, `start()`
reports `available = False` and the app keeps using the old CPU capture path.
"""
import os
import glob
import time
import shutil
import threading
import subprocess

from config import (FFMPEG_PATH, APP_DIR, SUBPROCESS_FLAGS, GPU_CODEC_FLAGS,
                    CQ, diag)


class GpuRecorder(threading.Thread):
    SEGMENT_SECONDS = 1            # granularity of the ring (also the clip tail loss)
    SEG_DIR = os.path.join(APP_DIR, 'replay_buffer')

    def __init__(self, main):
        super().__init__(daemon=True)
        self.main = main
        self.proc = None
        self._running = False
        self.available = False        # set True once ffmpeg is actually capturing
        self.backend = 'gpu'

    # --- settings helpers (read the same config the UI writes) --------------
    def _cfg_int(self, key, default):
        try:
            return int(self.main.read_from_file(key))
        except (TypeError, ValueError):
            return default

    def _monitor_idx(self):
        return max(self._cfg_int('monitor', 0), 0)   # ddagrab output_idx is 0-based

    def _fps(self):
        return max(self._cfg_int('fps', 60), 1)

    def _clip_length(self):
        return max(self._cfg_int('clip_length', 60), 1)

    def _encoder_flags(self):
        """Hardware encoder flags for the continuous encode. Kept minimal to
        match the proven ddagrab pipeline — the frames stay as on-GPU D3D11
        textures, so the encoder handles the colour conversion itself (forcing a
        CPU pixel format here breaks the hardware path)."""
        if 'h264_nvenc' in GPU_CODEC_FLAGS:
            return ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', CQ]
        if 'h264_amf' in GPU_CODEC_FLAGS:
            return ['-c:v', 'h264_amf', '-rc', 'cqp', '-qp_i', CQ, '-qp_p', CQ]
        if 'h264_qsv' in GPU_CODEC_FLAGS:
            return ['-c:v', 'h264_qsv', '-preset', 'medium', '-global_quality', CQ]
        return []   # no hardware encoder — GpuRecorder shouldn't be used

    def _uses_hw(self):
        joined = ' '.join(GPU_CODEC_FLAGS)
        return 'nvenc' in joined or 'amf' in joined or 'qsv' in joined

    # --- lifecycle ----------------------------------------------------------
    def run(self):
        self._running = True
        try:
            shutil.rmtree(self.SEG_DIR, ignore_errors=True)
            os.makedirs(self.SEG_DIR, exist_ok=True)
        except Exception as e:
            diag(f'gpu recorder: cannot make buffer dir: {e}')
            self.available = False
            return

        while self._running:
            idx, fps = self._monitor_idx(), self._fps()
            wrap = int((self._clip_length() + 8) / self.SEGMENT_SECONDS) + 2
            cmd = [FFMPEG_PATH, '-y', '-hide_banner', '-loglevel', 'error',
                   '-f', 'lavfi', '-i', f'ddagrab=output_idx={idx}:framerate={fps}',
                   *self._encoder_flags(), '-g', str(fps),
                   '-f', 'segment', '-segment_time', str(self.SEGMENT_SECONDS),
                   '-segment_format', 'mpegts', '-segment_wrap', str(wrap),
                   '-reset_timestamps', '1',
                   os.path.join(self.SEG_DIR, 'seg%04d.ts')]
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE,
                                             creationflags=SUBPROCESS_FLAGS)
            except Exception as e:
                diag(f'gpu recorder: ffmpeg launch failed: {e}')
                self.available = False
                return

            # Confirm it actually starts producing frames; if ffmpeg dies almost
            # immediately (ddagrab/encoder unsupported), give up so the app can
            # fall back to the CPU path instead of silently recording nothing.
            time.sleep(2.0)
            if self.proc.poll() is not None:
                err = b''
                try:
                    err = self.proc.stderr.read() or b''
                except Exception:
                    pass
                diag(f'gpu recorder: ddagrab/encoder unavailable: '
                     f'{err.decode("utf-8", "replace")[:200]}')
                self.available = False
                return

            self.available = True
            diag(f'gpu replay buffer live: monitor_idx={idx} fps={fps} '
                 f'enc={self._encoder_flags()[1]}')

            # Supervise: restart the encode if the monitor or fps setting changes,
            # or if ffmpeg exits unexpectedly.
            watched = (idx, fps)
            while self._running:
                if self.proc.poll() is not None:
                    diag('gpu recorder: ffmpeg exited, restarting')
                    break
                if (self._monitor_idx(), self._fps()) != watched:
                    self._kill()
                    break
                time.sleep(0.3)
            self._kill()
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

    # --- producing a clip ---------------------------------------------------
    def save_clip(self, out_path, audio_path=None):
        """Trim the last clip_length seconds out of the ring and mux in audio.
        Returns True on success. Video is copied (no re-encode) so there's no
        quality loss and it's near-instant."""
        clip_length = self._clip_length()
        try:
            segs = sorted(glob.glob(os.path.join(self.SEG_DIR, 'seg*.ts')),
                          key=os.path.getmtime)
        except Exception:
            segs = []
        # Drop the segment ffmpeg is currently writing (it's incomplete)
        if len(segs) >= 2:
            segs = segs[:-1]
        if not segs:
            diag('gpu recorder: no segments to clip')
            return False

        need = int((clip_length + 3) / self.SEGMENT_SECONDS) + 1
        use = segs[-need:]

        list_path = os.path.join(self.SEG_DIR, '_concat.txt')
        full_path = os.path.join(self.SEG_DIR, '_full.ts')
        try:
            with open(list_path, 'w', encoding='utf-8') as f:
                for s in use:
                    f.write("file '%s'\n" % s.replace('\\', '/'))
            # Concatenate the recent segments without re-encoding
            r = subprocess.run(
                [FFMPEG_PATH, '-y', '-hide_banner', '-loglevel', 'error',
                 '-f', 'concat', '-safe', '0', '-i', list_path,
                 '-c', 'copy', full_path],
                creationflags=SUBPROCESS_FLAGS, timeout=60)
            if r.returncode != 0 or not os.path.exists(full_path):
                diag('gpu recorder: concat failed')
                return False

            # Take exactly the last clip_length seconds and mux in the audio
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
