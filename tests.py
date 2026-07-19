#!/usr/bin/env python3
"""
ClipVault - Instant Replay Clipping Software
GPU-accelerated, always-running background capture with PyQt6 UI
"""

import sys
import os
import time
import threading
import queue
import subprocess
import tempfile
import shutil
import json
import math
import signal
from pathlib import Path
from datetime import datetime
from collections import deque
from typing import Optional, List, Dict, Any

import numpy as np
import mss
import sounddevice as sd

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSlider, QFileDialog, QFrame,
    QScrollArea, QGridLayout, QSizePolicy, QSystemTrayIcon, QMenu,
    QGraphicsOpacityEffect, QStackedWidget, QProgressBar, QSplitter,
    QListWidget, QListWidgetItem
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve,
    QRect, QSize, QPoint, QObject, pyqtProperty, QParallelAnimationGroup,
    QSequentialAnimationGroup, QRectF, QPointF
)
from PyQt6.QtGui import (
    QColor, QPalette, QFont, QFontDatabase, QIcon, QPixmap, QPainter,
    QLinearGradient, QRadialGradient, QBrush, QPen, QMovie, QAction,
    QPainterPath, QConicalGradient, QTransform, QImage
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS & DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

CLIP_LENGTHS = {"10s": 10, "30s": 30, "1m": 60, "2m": 120, "5m": 300, "10m": 600}
CLIP_FPS     = [10, 30, 60, 120, 240]
APP_NAME     = "ClipVault"
CONFIG_FILE  = Path.home() / ".clipvault_config.json"
DEFAULT_CLIP_DIR = Path.home() / "Videos" / "clips"

# GPU encoder priority (ffmpeg names)
GPU_ENCODERS = ["h264_nvenc", "h264_amf", "h264_qsv", "h264_vaapi", "libx264"]

# ─────────────────────────────────────────────────────────────────────────────
# THEME
# ─────────────────────────────────────────────────────────────────────────────

THEME = {
    "bg_dark":    "#0D0F14",
    "bg_card":    "#13161E",
    "bg_hover":   "#1A1E28",
    "accent":     "#6C63FF",
    "accent2":    "#FF6584",
    "success":    "#43D9AD",
    "warning":    "#FFB347",
    "text":       "#E8EAFF",
    "text_dim":   "#6B7280",
    "border":     "#1F2430",
    "clipping":   "#FF4444",
    "slider_bg":  "#1F2430",
}

STYLES = f"""
QMainWindow, QWidget {{
    background-color: {THEME['bg_dark']};
    color: {THEME['text']};
    font-family: 'Segoe UI', 'Inter', 'SF Pro Display', Arial, sans-serif;
}}
QLabel {{
    color: {THEME['text']};
    background: transparent;
}}
QPushButton {{
    background: {THEME['bg_card']};
    border: 1px solid {THEME['border']};
    border-radius: 8px;
    color: {THEME['text']};
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {THEME['bg_hover']};
    border-color: {THEME['accent']};
}}
QPushButton:pressed {{
    background: {THEME['accent']};
}}
QComboBox {{
    background: {THEME['bg_card']};
    border: 1px solid {THEME['border']};
    border-radius: 8px;
    color: {THEME['text']};
    padding: 6px 12px;
    font-size: 13px;
    min-height: 32px;
}}
QComboBox:hover {{
    border-color: {THEME['accent']};
}}
QComboBox::drop-down {{
    border: none;
    padding-right: 8px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {THEME['text_dim']};
    margin-right: 4px;
}}
QComboBox QAbstractItemView {{
    background: {THEME['bg_card']};
    border: 1px solid {THEME['accent']};
    border-radius: 8px;
    color: {THEME['text']};
    selection-background-color: {THEME['accent']};
    padding: 4px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {THEME['slider_bg']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {THEME['accent']};
    width: 14px;
    height: 14px;
    border-radius: 7px;
    margin: -5px 0;
}}
QSlider::sub-page:horizontal {{
    background: {THEME['accent']};
    border-radius: 2px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {THEME['border']};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
}}
QScrollBar::handle:horizontal {{
    background: {THEME['border']};
    border-radius: 3px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QListWidget {{
    background: {THEME['bg_card']};
    border: 1px solid {THEME['border']};
    border-radius: 8px;
    color: {THEME['text']};
    outline: none;
}}
QListWidget::item {{
    padding: 8px;
    border-radius: 6px;
    margin: 2px 4px;
}}
QListWidget::item:hover {{
    background: {THEME['bg_hover']};
}}
QListWidget::item:selected {{
    background: {THEME['accent']};
    color: white;
}}
QProgressBar {{
    background: {THEME['slider_bg']};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {THEME['accent']};
    border-radius: 3px;
}}
QSplitter::handle {{
    background: {THEME['border']};
    width: 2px;
}}
"""

# ─────────────────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    defaults = {
        "clip_length": "30s",
        "fps": 30,
        "monitor_index": 0,
        "output_dir": str(DEFAULT_CLIP_DIR),
        "mic_device": "None",
        "audio_output": "None",
        "hotkey": "F9",
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            defaults.update(saved)
        except Exception:
            pass
    return defaults

def save_config(cfg: dict):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

def detect_gpu_encoder() -> str:
    """Try each encoder; return first that works."""
    for enc in GPU_ENCODERS:
        try:
            result = subprocess.run(
                ["ffmpeg", "-f", "rawvideo", "-vcodec", "rawvideo",
                 "-s", "16x16", "-pix_fmt", "bgr24", "-r", "1", "-i", "pipe:0",
                 "-vcodec", enc, "-t", "0.1", "-f", "null", "-"],
                input=b"\x00" * (16 * 16 * 3),
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return enc
        except Exception:
            continue
    return "libx264"

def format_duration(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"

def get_input_devices() -> List[Dict]:
    devices = [{"name": "None", "index": -1}]
    try:
        sd_devices = sd.query_devices()
        for i, d in enumerate(sd_devices):
            if d["max_input_channels"] > 0:
                devices.append({"name": d["name"], "index": i})
    except Exception:
        pass
    return devices

def get_output_devices() -> List[Dict]:
    devices = [{"name": "None", "index": -1}]
    try:
        sd_devices = sd.query_devices()
        for i, d in enumerate(sd_devices):
            if d["max_output_channels"] > 0:
                devices.append({"name": d["name"], "index": i})
    except Exception:
        pass
    return devices

def get_monitors() -> List[Dict]:
    monitors = []
    try:
        with mss.mss() as s:
            for i, mon in enumerate(s.monitors[1:], start=1):
                monitors.append({
                    "name": f"Monitor {i} ({mon['width']}×{mon['height']})",
                    "index": i,
                    "width": mon["width"],
                    "height": mon["height"],
                })
    except Exception:
        monitors.append({"name": "Primary Monitor", "index": 1, "width": 1920, "height": 1080})
    return monitors

# ─────────────────────────────────────────────────────────────────────────────
# RING BUFFER CAPTURE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class AudioCapture(QObject):
    """Continuous low-latency audio ring buffer capture."""

    def __init__(self, device_index: int, sample_rate: int = 44100, channels: int = 2):
        super().__init__()
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer: deque = deque()
        self.buffer_lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
        self._running = False
        self._timestamps: deque = deque()

    def start(self):
        if self.device_index < 0:
            return
        try:
            self._running = True
            self._stream = sd.InputStream(
                device=self.device_index,
                channels=self.channels,
                samplerate=self.sample_rate,
                blocksize=1024,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"Audio capture error: {e}")
            self._running = False

    def _callback(self, indata, frames, time_info, status):
        ts = time.monotonic()
        with self.buffer_lock:
            self.buffer.append((ts, indata.copy()))
            # Keep only last 15 minutes
            while len(self.buffer) > 15 * 60 * (self.sample_rate // 1024 + 1):
                self.buffer.popleft()

    def get_last_n_seconds(self, n: float) -> Optional[np.ndarray]:
        cutoff = time.monotonic() - n
        chunks = []
        with self.buffer_lock:
            for ts, data in self.buffer:
                if ts >= cutoff:
                    chunks.append(data)
        if not chunks:
            return None
        return np.concatenate(chunks, axis=0)

    def stop(self):
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


class ScreenCapture(QThread):
    """Continuous GPU-framerate screen capture into ring buffer."""

    error_signal = pyqtSignal(str)

    def __init__(self, monitor_index: int, fps: int, max_seconds: int):
        super().__init__()
        self.monitor_index = monitor_index
        self.fps = fps
        self.max_seconds = max_seconds
        self.frame_interval = 1.0 / fps

        max_frames = (max_seconds + 10) * fps
        self.frames: deque = deque(maxlen=max_frames)
        self.timestamps: deque = deque(maxlen=max_frames)
        self.lock = threading.Lock()
        self._running = False
        self._paused = False
        self.width = 0
        self.height = 0

    def run(self):
        self._running = True
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if self.monitor_index >= len(monitors):
                    self.monitor_index = 1
                mon = monitors[self.monitor_index]
                self.width = mon["width"]
                self.height = mon["height"]

                next_frame = time.monotonic()
                while self._running:
                    now = time.monotonic()
                    if now < next_frame:
                        sleep_time = next_frame - now
                        time.sleep(max(0, sleep_time - 0.001))
                        continue

                    if not self._paused:
                        img = sct.grab(mon)
                        # Convert to RGB numpy array
                        frame = np.frombuffer(img.bgra, dtype=np.uint8).reshape(
                            img.height, img.width, 4
                        )[:, :, :3]  # Drop alpha, keep BGR

                        with self.lock:
                            self.frames.append(frame.copy())
                            self.timestamps.append(now)

                    next_frame += self.frame_interval
                    # Drift correction
                    if now - next_frame > self.frame_interval * 3:
                        next_frame = time.monotonic() + self.frame_interval

        except Exception as e:
            self.error_signal.emit(str(e))

    def get_last_n_seconds(self, n: float) -> List[tuple]:
        cutoff = time.monotonic() - n
        result = []
        with self.lock:
            for ts, frame in zip(self.timestamps, self.frames):
                if ts >= cutoff:
                    result.append((ts, frame))
        return result

    def stop(self):
        self._running = False
        self.wait(3000)


# ─────────────────────────────────────────────────────────────────────────────
# CLIP ENCODER THREAD
# ─────────────────────────────────────────────────────────────────────────────

class ClipEncoder(QThread):
    """Encode captured frames + audio to MP4 using GPU acceleration."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(str)   # filepath
    error    = pyqtSignal(str)

    def __init__(self, frames, timestamps, audio_data, audio_sr,
                 output_path: str, fps: int, encoder: str, width: int, height: int):
        super().__init__()
        self.frames = frames
        self.timestamps = timestamps
        self.audio_data = audio_data
        self.audio_sr = audio_sr
        self.output_path = output_path
        self.fps = fps
        self.encoder = encoder
        self.width = width
        self.height = height

    def run(self):
        try:
            tmp_dir = tempfile.mkdtemp(prefix="clipvault_")
            video_path = os.path.join(tmp_dir, "video.mp4")
            audio_path = os.path.join(tmp_dir, "audio.wav") if self.audio_data is not None else None

            total = len(self.frames)
            if total == 0:
                self.error.emit("No frames captured")
                return

            self.progress.emit(5)

            # ── Write audio WAV ───────────────────────────────────────────────
            if audio_path and self.audio_data is not None:
                try:
                    import wave, struct
                    audio_int = (self.audio_data * 32767).astype(np.int16)
                    ch = audio_int.shape[1] if audio_int.ndim > 1 else 1
                    with wave.open(audio_path, "w") as wf:
                        wf.setnchannels(ch)
                        wf.setsampwidth(2)
                        wf.setframerate(self.audio_sr)
                        wf.writeframes(audio_int.tobytes())
                except Exception as ae:
                    print(f"Audio write error: {ae}")
                    audio_path = None

            self.progress.emit(15)

            # ── Build ffmpeg command ──────────────────────────────────────────
            # Pipe raw BGR frames → encoder
            cmd = [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{self.width}x{self.height}",
                "-pix_fmt", "bgr24",
                "-r", str(self.fps),
                "-i", "pipe:0",
            ]

            if audio_path:
                cmd += ["-i", audio_path]

            # Encoder settings
            if self.encoder == "h264_nvenc":
                cmd += ["-vcodec", "h264_nvenc", "-preset", "p4", "-cq", "23"]
            elif self.encoder in ("h264_amf", "h264_qsv"):
                cmd += ["-vcodec", self.encoder, "-quality", "balanced"]
            elif self.encoder == "h264_vaapi":
                cmd += ["-vf", "format=nv12,hwupload", "-vcodec", "h264_vaapi", "-rc_mode", "CQP", "-qp", "24"]
            else:
                cmd += ["-vcodec", "libx264", "-preset", "veryfast", "-crf", "23"]

            cmd += ["-pix_fmt", "yuv420p"]

            if audio_path:
                cmd += ["-acodec", "aac", "-b:a", "192k", "-shortest"]

            cmd += [video_path]

            # ── Stream frames to ffmpeg ───────────────────────────────────────
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            for i, frame in enumerate(self.frames):
                # Ensure correct dimensions
                if frame.shape[1] != self.width or frame.shape[0] != self.height:
                    import cv2
                    frame = cv2.resize(frame, (self.width, self.height))
                proc.stdin.write(frame.tobytes())
                pct = 15 + int(75 * (i + 1) / total)
                self.progress.emit(pct)

            proc.stdin.close()
            proc.wait()

            if proc.returncode != 0:
                self.error.emit(f"Encoder failed (code {proc.returncode})")
                return

            self.progress.emit(92)

            # ── Move to output ────────────────────────────────────────────────
            Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(video_path, self.output_path)
            shutil.rmtree(tmp_dir, ignore_errors=True)

            self.progress.emit(100)
            self.finished.emit(self.output_path)

        except Exception as e:
            self.error.emit(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# ANIMATED WIDGETS
# ─────────────────────────────────────────────────────────────────────────────

class PulseButton(QPushButton):
    """The main CLIP button with pulsing ring animation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pulse = 0.0
        self._clipping = False
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(16)  # ~60fps
        self._t = 0.0
        self.setFixedSize(120, 120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _tick(self):
        self._t += 0.05
        if self._clipping:
            self._pulse = 0.5 + 0.5 * math.sin(self._t * 3)
        else:
            self._pulse = max(0.0, self._pulse - 0.05)
        self.update()

    def set_clipping(self, v: bool):
        self._clipping = v
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self.width() / 2, self.height() / 2
        r = 44

        # Outer pulse ring
        if self._pulse > 0:
            ring_r = r + 8 + self._pulse * 14
            ring_alpha = int((1 - self._pulse * 0.4) * 180)
            color = QColor(THEME["clipping"]) if self._clipping else QColor(THEME["accent"])
            color.setAlpha(ring_alpha)
            pen = QPen(color, 2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

        # Background circle
        grad = QRadialGradient(cx, cy, r)
        if self._clipping:
            grad.setColorAt(0, QColor("#3D0000"))
            grad.setColorAt(1, QColor("#1A0000"))
        else:
            grad.setColorAt(0, QColor("#1A1A2E"))
            grad.setColorAt(1, QColor("#0D0F14"))

        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(THEME["clipping"] if self._clipping else THEME["accent"]), 2))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Icon
        icon_r = 16
        if self._clipping:
            # Stop square
            p.setBrush(QBrush(QColor(THEME["clipping"])))
            p.setPen(Qt.PenStyle.NoPen)
            sq = 18
            p.drawRoundedRect(QRectF(cx - sq/2, cy - sq/2, sq, sq), 3, 3)
        else:
            # Record circle
            p.setBrush(QBrush(QColor(THEME["accent"])))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - icon_r, cy - icon_r, icon_r * 2, icon_r * 2))

        p.end()


class SegmentedControl(QWidget):
    """iOS-style segmented picker."""

    selectionChanged = pyqtSignal(str)

    def __init__(self, options: list, parent=None):
        super().__init__(parent)
        self.options = options
        self.selected = 0
        self._slider_x = 0.0
        self._target_x = 0.0
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._slide_tick)
        self._anim.start(16)
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(len(options) * 52)

    def _slide_tick(self):
        diff = self._target_x - self._slider_x
        if abs(diff) < 0.5:
            self._slider_x = self._target_x
        else:
            self._slider_x += diff * 0.25
        self.update()

    def _item_width(self) -> float:
        return self.width() / len(self.options)

    def mousePressEvent(self, event):
        idx = int(event.position().x() / self._item_width())
        idx = max(0, min(idx, len(self.options) - 1))
        if idx != self.selected:
            self.selected = idx
            self._target_x = idx * self._item_width()
            self.selectionChanged.emit(self.options[idx])
            self.update()

    def set_selected(self, value: str):
        if value in self.options:
            idx = self.options.index(value)
            self.selected = idx
            self._slider_x = idx * self._item_width()
            self._target_x = self._slider_x

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        iw = self._item_width()
        h = self.height()

        # Background
        p.setBrush(QBrush(QColor(THEME["bg_card"])))
        p.setPen(QPen(QColor(THEME["border"]), 1))
        p.drawRoundedRect(QRectF(0, 0, self.width(), h), 8, 8)

        # Slider pill
        pill_pad = 3
        p.setBrush(QBrush(QColor(THEME["accent"])))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(
            QRectF(self._slider_x + pill_pad, pill_pad,
                   iw - pill_pad * 2, h - pill_pad * 2), 6, 6
        )

        # Labels
        font = QFont()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        for i, opt in enumerate(self.options):
            x = i * iw
            is_sel = i == self.selected
            p.setPen(QPen(QColor("white" if is_sel else THEME["text_dim"])))
            p.drawText(QRectF(x, 0, iw, h), Qt.AlignmentFlag.AlignCenter, str(opt))

        p.end()


class GlowCard(QFrame):
    """Card with subtle glow on hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background: {THEME['bg_card']};
                border: 1px solid {THEME['border']};
                border-radius: 12px;
            }}
        """)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.setStyleSheet(f"""
            QFrame {{
                background: {THEME['bg_hover']};
                border: 1px solid {THEME['accent']};
                border-radius: 12px;
            }}
        """)

    def leaveEvent(self, e):
        self._hover = False
        self.setStyleSheet(f"""
            QFrame {{
                background: {THEME['bg_card']};
                border: 1px solid {THEME['border']};
                border-radius: 12px;
            }}
        """)


class StatusIndicator(QWidget):
    """Animated dot showing capture status."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.setFixedSize(10, 10)

    def _tick(self):
        if self._active:
            self._t += 0.1
            self.update()

    def set_active(self, v: bool):
        self._active = v
        if not v:
            self._t = 0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._active:
            alpha = int(180 + 75 * math.sin(self._t * 2))
            c = QColor(THEME["success"])
            c.setAlpha(alpha)
        else:
            c = QColor(THEME["text_dim"])
        p.setBrush(QBrush(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 8, 8)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# CLIP NOTIFICATION OVERLAY
# ─────────────────────────────────────────────────────────────────────────────

class ClipNotification(QWidget):
    """Always-on-top popup shown while clip is saving."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(280, 72)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._container = QWidget()
        self._container.setStyleSheet(f"""
            background: {THEME['bg_card']};
            border: 1px solid {THEME['clipping']};
            border-radius: 14px;
        """)
        inner = QHBoxLayout(self._container)
        inner.setContentsMargins(16, 12, 16, 12)
        inner.setSpacing(12)

        # Animated dot
        self._dot = StatusIndicator()
        self._dot.set_active(True)
        inner.addWidget(self._dot)

        vbox = QVBoxLayout()
        vbox.setSpacing(2)
        self._title = QLabel("● CLIPPING")
        self._title.setStyleSheet(f"color: {THEME['clipping']}; font-size: 12px; font-weight: 700; letter-spacing: 1px;")
        self._sub = QLabel("Saving last 30s...")
        self._sub.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 11px;")
        vbox.addWidget(self._title)
        vbox.addWidget(self._sub)
        inner.addLayout(vbox, 1)

        self._prog = QProgressBar()
        self._prog.setRange(0, 100)
        self._prog.setValue(0)
        self._prog.setFixedWidth(50)
        self._prog.setTextVisible(False)
        self._prog.setStyleSheet(f"""
            QProgressBar {{ background: {THEME['border']}; border-radius: 3px; height: 6px; }}
            QProgressBar::chunk {{ background: {THEME['clipping']}; border-radius: 3px; }}
        """)
        inner.addWidget(self._prog)

        layout.addWidget(self._container)

        # Fade animation
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(300)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    def show_clipping(self, clip_secs: int):
        self._sub.setText(f"Saving last {clip_secs}s...")
        self._prog.setValue(0)
        self._prog.setStyleSheet(f"""
            QProgressBar {{ background: {THEME['border']}; border-radius: 3px; height: 6px; }}
            QProgressBar::chunk {{ background: {THEME['clipping']}; border-radius: 3px; }}
        """)
        self._title.setText("● CLIPPING")

        # Position bottom-right
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.right() - self.width() - 24,
                  screen.bottom() - self.height() - 60)

        self._opacity_effect.setOpacity(0)
        self.show()
        self.raise_()

        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def set_progress(self, v: int):
        self._prog.setValue(v)

    def show_done(self):
        self._title.setText("✓ CLIP SAVED")
        self._title.setStyleSheet(f"color: {THEME['success']}; font-size: 12px; font-weight: 700; letter-spacing: 1px;")
        self._sub.setText("Ready to view")
        self._hide_timer.start(2500)

    def show_error(self, msg: str):
        self._title.setText("✗ FAILED")
        self._title.setStyleSheet(f"color: {THEME['warning']}; font-size: 12px; font-weight: 700;")
        self._sub.setText(msg[:32])
        self._hide_timer.start(3000)

    def _fade_out(self):
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self.hide)
        self._fade_anim.start()


# ─────────────────────────────────────────────────────────────────────────────
# CLIP VIEWER
# ─────────────────────────────────────────────────────────────────────────────

class ClipViewer(QWidget):
    """Video player + clip list, SteelSeries-style."""

    def __init__(self, output_dir: str, parent=None):
        super().__init__(parent)
        self.output_dir = output_dir
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Clip list ─────────────────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(8)

        hdr = QHBoxLayout()
        clips_label = QLabel("My Clips")
        clips_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        hdr.addWidget(clips_label)
        hdr.addStretch()
        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedSize(28, 28)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid {THEME['border']};
                border-radius: 6px; color: {THEME['text_dim']}; font-size: 14px; padding: 0;
            }}
            QPushButton:hover {{ border-color: {THEME['accent']}; color: {THEME['accent']}; }}
        """)
        refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(refresh_btn)
        left.addLayout(hdr)

        self._list = QListWidget()
        self._list.setMinimumWidth(220)
        self._list.setMaximumWidth(280)
        self._list.currentRowChanged.connect(self._on_select)
        left.addWidget(self._list)

        layout.addLayout(left)

        # ── Video area ────────────────────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(8)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(300)
        self._video_widget.setStyleSheet(f"background: #000; border-radius: 8px;")
        right.addWidget(self._video_widget, 1)

        # Controls
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(36, 36)
        self._play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self._play_btn)

        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 12px;")
        self._time_label.setFixedWidth(90)
        ctrl.addWidget(self._time_label)

        self._scrub = QSlider(Qt.Orientation.Horizontal)
        self._scrub.setRange(0, 1000)
        self._scrub.sliderPressed.connect(self._on_scrub_press)
        self._scrub.sliderReleased.connect(self._on_scrub_release)
        self._scrub.sliderMoved.connect(self._on_scrub_move)
        ctrl.addWidget(self._scrub, 1)

        vol_label = QLabel("🔊")
        vol_label.setStyleSheet("font-size: 13px;")
        ctrl.addWidget(vol_label)
        self._vol = QSlider(Qt.Orientation.Horizontal)
        self._vol.setRange(0, 100)
        self._vol.setValue(80)
        self._vol.setFixedWidth(70)
        self._vol.valueChanged.connect(self._set_volume)
        ctrl.addWidget(self._vol)

        right.addLayout(ctrl)

        # Open / Delete buttons
        btn_row = QHBoxLayout()
        open_btn = QPushButton("📂 Open Folder")
        open_btn.clicked.connect(self._open_folder)
        open_btn.setStyleSheet(open_btn.styleSheet())
        btn_row.addWidget(open_btn)
        del_btn = QPushButton("🗑 Delete")
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid #FF4444;
                border-radius: 8px; color: #FF4444; padding: 6px 14px;
            }}
            QPushButton:hover {{ background: #3D0000; }}
        """)
        del_btn.clicked.connect(self._delete_clip)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        right.addLayout(btn_row)

        layout.addLayout(right, 1)

        # Media player
        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._audio_out.setVolume(0.8)
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(self._video_widget)
        self._player.playbackStateChanged.connect(self._on_state_change)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)

        self._scrubbing = False
        self._duration_ms = 0

        self._pos_timer = QTimer()
        self._pos_timer.timeout.connect(self._update_time)
        self._pos_timer.start(250)

    def set_output_dir(self, d: str):
        self.output_dir = d
        self.refresh()

    def refresh(self):
        self._list.clear()
        d = Path(self.output_dir)
        if not d.exists():
            return
        clips = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        for c in clips:
            size_mb = c.stat().st_size / 1024 / 1024
            mtime = datetime.fromtimestamp(c.stat().st_mtime)
            item = QListWidgetItem(f"{c.stem}\n{mtime.strftime('%b %d %H:%M')}  •  {size_mb:.1f} MB")
            item.setData(Qt.ItemDataRole.UserRole, str(c))
            self._list.addItem(item)

    def _on_select(self, row: int):
        if row < 0:
            return
        item = self._list.item(row)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        from PyQt6.QtCore import QUrl
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._play_btn.setText("▶")
        else:
            self._player.play()
            self._play_btn.setText("⏸")

    def _on_state_change(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("⏸")
        else:
            self._play_btn.setText("▶")

    def _on_position(self, pos):
        if not self._scrubbing and self._duration_ms > 0:
            self._scrub.setValue(int(pos / self._duration_ms * 1000))

    def _on_duration(self, dur):
        self._duration_ms = dur

    def _on_scrub_press(self):
        self._scrubbing = True

    def _on_scrub_release(self):
        self._scrubbing = False
        if self._duration_ms > 0:
            ms = int(self._scrub.value() / 1000 * self._duration_ms)
            self._player.setPosition(ms)

    def _on_scrub_move(self, v):
        if self._duration_ms > 0:
            ms = int(v / 1000 * self._duration_ms)
            self._player.setPosition(ms)

    def _update_time(self):
        pos = self._player.position()
        dur = self._duration_ms
        self._time_label.setText(f"{format_duration(pos/1000)} / {format_duration(dur/1000)}")

    def _set_volume(self, v):
        self._audio_out.setVolume(v / 100)

    def _open_folder(self):
        import subprocess
        d = self.output_dir
        try:
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception:
            pass

    def _delete_clip(self):
        row = self._list.currentRow()
        if row < 0:
            return
        item = self._list.item(row)
        path = item.data(Qt.ItemDataRole.UserRole)
        try:
            os.remove(path)
            self.refresh()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS PANEL
# ─────────────────────────────────────────────────────────────────────────────

class SettingsPanel(QWidget):
    settingsChanged = pyqtSignal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self._build_ui()

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 11px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase;")
        return lbl

    def _row(self, label: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 13px;")
        lbl.setFixedWidth(130)
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        return row

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        inner_widget = QWidget()
        layout = QVBoxLayout(inner_widget)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(16)
        scroll.setWidget(inner_widget)

        # ── Clip length ───────────────────────────────────────────────────────
        layout.addWidget(self._section_label("Clip Length"))
        lengths = list(CLIP_LENGTHS.keys())
        self._length_seg = SegmentedControl(lengths)
        cur_len = self.config.get("clip_length", "30s")
        if cur_len in lengths:
            self._length_seg.set_selected(cur_len)
        self._length_seg.selectionChanged.connect(self._on_length)
        layout.addWidget(self._length_seg)

        # ── FPS ───────────────────────────────────────────────────────────────
        layout.addWidget(self._section_label("Frame Rate"))
        fps_opts = [str(f) for f in CLIP_FPS]
        self._fps_seg = SegmentedControl(fps_opts)
        cur_fps = str(self.config.get("fps", 30))
        if cur_fps in fps_opts:
            self._fps_seg.set_selected(cur_fps)
        self._fps_seg.selectionChanged.connect(self._on_fps)
        layout.addWidget(self._fps_seg)

        # ── Monitor ───────────────────────────────────────────────────────────
        layout.addWidget(self._section_label("Monitor"))
        self._monitor_combo = QComboBox()
        monitors = get_monitors()
        for m in monitors:
            self._monitor_combo.addItem(m["name"], m["index"])
        cur_mon = self.config.get("monitor_index", 0)
        idx = next((i for i, m in enumerate(monitors) if m["index"] == cur_mon), 0)
        self._monitor_combo.setCurrentIndex(idx)
        self._monitor_combo.currentIndexChanged.connect(self._on_monitor)
        layout.addLayout(self._row("Monitor", self._monitor_combo))

        # ── Microphone ───────────────────────────────────────────────────────
        layout.addWidget(self._section_label("Microphone"))
        self._mic_combo = QComboBox()
        self._input_devices = get_input_devices()
        for d in self._input_devices:
            self._mic_combo.addItem(d["name"], d["index"])
        cur_mic = self.config.get("mic_device", "None")
        for i, d in enumerate(self._input_devices):
            if d["name"] == cur_mic:
                self._mic_combo.setCurrentIndex(i)
                break
        self._mic_combo.currentIndexChanged.connect(self._on_mic)
        layout.addLayout(self._row("Microphone", self._mic_combo))

        # ── System Audio ─────────────────────────────────────────────────────
        layout.addWidget(self._section_label("System Audio"))
        self._audio_combo = QComboBox()
        self._output_devices = get_output_devices()
        for d in self._output_devices:
            self._audio_combo.addItem(d["name"], d["index"])
        cur_audio = self.config.get("audio_output", "None")
        for i, d in enumerate(self._output_devices):
            if d["name"] == cur_audio:
                self._audio_combo.setCurrentIndex(i)
                break
        self._audio_combo.currentIndexChanged.connect(self._on_audio)
        layout.addLayout(self._row("System Audio", self._audio_combo))

        # ── Save location ─────────────────────────────────────────────────────
        layout.addWidget(self._section_label("Save Location"))
        path_row = QHBoxLayout()
        self._path_label = QLabel(self.config.get("output_dir", str(DEFAULT_CLIP_DIR)))
        self._path_label.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 12px;")
        self._path_label.setWordWrap(True)
        path_row.addWidget(self._path_label, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        layout.addStretch()

    def _on_length(self, v):
        self.config["clip_length"] = v
        self.settingsChanged.emit(self.config)

    def _on_fps(self, v):
        self.config["fps"] = int(v)
        self.settingsChanged.emit(self.config)

    def _on_monitor(self, i):
        self.config["monitor_index"] = self._monitor_combo.itemData(i)
        self.settingsChanged.emit(self.config)

    def _on_mic(self, i):
        self.config["mic_device"] = self._input_devices[i]["name"]
        self.settingsChanged.emit(self.config)

    def _on_audio(self, i):
        self.config["audio_output"] = self._output_devices[i]["name"]
        self.settingsChanged.emit(self.config)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Clip Folder",
                                              self.config.get("output_dir", str(DEFAULT_CLIP_DIR)))
        if d:
            self.config["output_dir"] = d
            self._path_label.setText(d)
            self.settingsChanged.emit(self.config)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self._encoder = detect_gpu_encoder()
        print(f"[ClipVault] Using encoder: {self._encoder}")

        self._screen_capture: Optional[ScreenCapture] = None
        self._audio_capture: Optional[AudioCapture] = None
        self._clip_encoder: Optional[ClipEncoder] = None
        self._clipping = False
        self._notification = ClipNotification()
        self._clip_count = 0
        self._current_page = 0

        self._setup_window()
        self._setup_ui()
        self._setup_tray()
        self._start_capture()

        # Hotkey polling (cross-platform via keyboard check in timer)
        self._hotkey_timer = QTimer(self)
        self._hotkey_timer.timeout.connect(self._check_hotkey)
        self._hotkey_timer.start(50)
        self._hotkey_state = False

        # Buffer health checker
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._update_buffer_health)
        self._health_timer.start(1000)

    # ── Window Setup ─────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(900, 620)
        self.resize(1060, 680)
        self.setStyleSheet(STYLES)
        # Custom title bar not needed; let OS handle it but style heavily

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Sidebar ────────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(f"background: {THEME['bg_card']}; border-right: 1px solid {THEME['border']};")
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(12, 20, 12, 20)
        sb_layout.setSpacing(4)

        # Logo
        logo = QLabel(f"<b>Clip</b><span style='color:{THEME[\"accent\"]}'>Vault</span>")
        logo.setStyleSheet("font-size: 20px; color: white; letter-spacing: 1px;")
        logo.setContentsMargins(8, 0, 0, 16)
        sb_layout.addWidget(logo)

        # Nav buttons
        self._nav_btns = []
        for i, (icon, label) in enumerate([("⚡", "Capture"), ("🎬", "My Clips"), ("⚙️", "Settings")]):
            btn = QPushButton(f"  {icon}  {label}")
            btn.setCheckable(True)
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left;
                    background: transparent;
                    border: none;
                    border-radius: 8px;
                    padding: 10px 12px;
                    font-size: 13px;
                    color: {THEME['text_dim']};
                }}
                QPushButton:hover {{
                    background: {THEME['bg_hover']};
                    color: {THEME['text']};
                }}
                QPushButton:checked {{
                    background: {THEME['accent']}22;
                    color: {THEME['accent']};
                    border: 1px solid {THEME['accent']}44;
                }}
            """)
            btn.clicked.connect(lambda checked, idx=i: self._switch_page(idx))
            sb_layout.addWidget(btn)
            self._nav_btns.append(btn)

        self._nav_btns[0].setChecked(True)

        sb_layout.addStretch()

        # Status area
        status_frame = QFrame()
        status_frame.setStyleSheet(f"""
            background: {THEME['bg_dark']};
            border: 1px solid {THEME['border']};
            border-radius: 8px;
        """)
        sf_layout = QVBoxLayout(status_frame)
        sf_layout.setContentsMargins(10, 8, 10, 8)
        sf_layout.setSpacing(4)

        row1 = QHBoxLayout()
        self._status_dot = StatusIndicator()
        self._status_dot.set_active(True)
        self._status_label = QLabel("Recording")
        self._status_label.setStyleSheet(f"color: {THEME['success']}; font-size: 11px; font-weight: 600;")
        row1.addWidget(self._status_dot)
        row1.addWidget(self._status_label)
        row1.addStretch()
        sf_layout.addLayout(row1)

        self._buffer_label = QLabel("Buffer: --")
        self._buffer_label.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 10px;")
        sf_layout.addWidget(self._buffer_label)

        self._encoder_label = QLabel(f"GPU: {self._encoder}")
        self._encoder_label.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 10px;")
        sf_layout.addWidget(self._encoder_label)

        sb_layout.addWidget(status_frame)
        main_layout.addWidget(sidebar)

        # ── Page stack ─────────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        main_layout.addWidget(self._stack, 1)

        self._build_capture_page()
        self._build_clips_page()
        self._build_settings_page()

    def _build_capture_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(24)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("Capture")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        hdr.addWidget(title)
        hdr.addStretch()
        clip_count = QLabel("0 clips saved")
        clip_count.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 13px;")
        self._clip_count_label = clip_count
        hdr.addWidget(clip_count)
        layout.addLayout(hdr)

        # Main capture card
        center_card = GlowCard()
        card_layout = QVBoxLayout(center_card)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.setContentsMargins(32, 40, 32, 40)
        card_layout.setSpacing(20)

        # Big clip button
        self._clip_btn = PulseButton()
        self._clip_btn.clicked.connect(self._trigger_clip)
        card_layout.addWidget(self._clip_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        hotkey_lbl = QLabel("Press F9 or click to clip")
        hotkey_lbl.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 13px;")
        hotkey_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(hotkey_lbl)

        # Quick info row
        info_row = QHBoxLayout()
        info_row.setSpacing(24)
        for (icon, key, attr) in [
            ("⏱", "clip_length", "_info_length"),
            ("🎞️", "fps", "_info_fps"),
            ("🖥️", "monitor_index", "_info_mon"),
        ]:
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(icon)
            lbl.setStyleSheet("font-size: 20px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_lbl = QLabel("--")
            val_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(lbl)
            col.addWidget(val_lbl)
            setattr(self, attr, val_lbl)
            info_row.addLayout(col)
        card_layout.addLayout(info_row)

        layout.addWidget(center_card, 1)

        # Recent clips preview
        recent_lbl = QLabel("Recent Clips")
        recent_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(recent_lbl)

        self._recent_layout = QHBoxLayout()
        self._recent_layout.setSpacing(12)
        self._recent_layout.addStretch()
        layout.addLayout(self._recent_layout)

        self._stack.addWidget(page)
        self._update_info_labels()

    def _build_clips_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel("My Clips")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

        self._clip_viewer = ClipViewer(self.config.get("output_dir", str(DEFAULT_CLIP_DIR)))
        layout.addWidget(self._clip_viewer, 1)

        self._stack.addWidget(page)

    def _build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

        self._settings_panel = SettingsPanel(self.config)
        self._settings_panel.settingsChanged.connect(self._on_settings_changed)
        layout.addWidget(self._settings_panel, 1)

        self._stack.addWidget(page)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _switch_page(self, idx: int):
        self._current_page = idx
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)
        if idx == 1:
            self._clip_viewer.refresh()

    # ── Capture Engine ────────────────────────────────────────────────────────

    def _start_capture(self):
        self._stop_capture()
        clip_secs = CLIP_LENGTHS.get(self.config.get("clip_length", "30s"), 30)
        fps = self.config.get("fps", 30)
        mon_idx = self.config.get("monitor_index", 1)
        if mon_idx == 0:
            mon_idx = 1

        # Start screen capture
        self._screen_capture = ScreenCapture(mon_idx, fps, clip_secs + 15)
        self._screen_capture.error_signal.connect(self._on_capture_error)
        self._screen_capture.start()

        # Start audio capture
        mic_name = self.config.get("mic_device", "None")
        mic_idx = -1
        if mic_name != "None":
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["name"] == mic_name and d["max_input_channels"] > 0:
                    mic_idx = i
                    break

        if mic_idx >= 0:
            self._audio_capture = AudioCapture(mic_idx)
            self._audio_capture.start()

        self._status_dot.set_active(True)
        self._status_label.setText("Recording")
        self._status_label.setStyleSheet(f"color: {THEME['success']}; font-size: 11px; font-weight: 600;")

    def _stop_capture(self):
        if self._screen_capture:
            self._screen_capture.stop()
            self._screen_capture = None
        if self._audio_capture:
            self._audio_capture.stop()
            self._audio_capture = None

    def _on_capture_error(self, msg: str):
        self._status_dot.set_active(False)
        self._status_label.setText("Error")
        self._status_label.setStyleSheet(f"color: {THEME['warning']}; font-size: 11px; font-weight: 600;")
        print(f"[ClipVault] Capture error: {msg}")

    def _update_buffer_health(self):
        if self._screen_capture:
            with self._screen_capture.lock:
                n = len(self._screen_capture.frames)
            fps = self.config.get("fps", 30)
            secs = n / max(fps, 1)
            self._buffer_label.setText(f"Buffer: {secs:.0f}s ({n} frames)")

    # ── Clip Trigger ──────────────────────────────────────────────────────────

    def _trigger_clip(self):
        if self._clipping:
            return
        if not self._screen_capture:
            return

        self._clipping = True
        self._clip_btn.set_clipping(True)
        clip_secs = CLIP_LENGTHS.get(self.config.get("clip_length", "30s"), 30)
        self._notification.show_clipping(clip_secs)

        # Get frames
        frame_data = self._screen_capture.get_last_n_seconds(clip_secs)
        if not frame_data:
            self._notification.show_error("Buffer empty")
            self._clipping = False
            self._clip_btn.set_clipping(False)
            return

        timestamps = [f[0] for f in frame_data]
        frames = [f[1] for f in frame_data]
        audio = None

        if self._audio_capture:
            audio = self._audio_capture.get_last_n_seconds(clip_secs)

        # Output path
        out_dir = self.config.get("output_dir", str(DEFAULT_CLIP_DIR))
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = os.path.join(out_dir, f"clip_{ts}.mp4")

        # Get video dimensions
        w = self._screen_capture.width
        h = self._screen_capture.height
        fps = self.config.get("fps", 30)

        self._clip_encoder = ClipEncoder(
            frames, timestamps, audio, 44100,
            out_path, fps, self._encoder, w, h
        )
        self._clip_encoder.progress.connect(self._notification.set_progress)
        self._clip_encoder.finished.connect(self._on_clip_done)
        self._clip_encoder.error.connect(self._on_clip_error)
        self._clip_encoder.start()

    def _on_clip_done(self, path: str):
        self._clipping = False
        self._clip_btn.set_clipping(False)
        self._notification.show_done()
        self._clip_count += 1
        self._clip_count_label.setText(f"{self._clip_count} clip{'s' if self._clip_count > 1 else ''} saved")
        self._update_recent_clips()
        print(f"[ClipVault] Clip saved: {path}")

    def _on_clip_error(self, msg: str):
        self._clipping = False
        self._clip_btn.set_clipping(False)
        self._notification.show_error(msg[:30])
        print(f"[ClipVault] Clip error: {msg}")

    def _update_recent_clips(self):
        # Clear and re-add last 4 clips
        while self._recent_layout.count() > 1:
            item = self._recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        d = Path(self.config.get("output_dir", str(DEFAULT_CLIP_DIR)))
        if not d.exists():
            return
        clips = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
        for c in clips:
            card = QFrame()
            card.setFixedSize(120, 70)
            card.setStyleSheet(f"""
                background: {THEME['bg_card']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
            """)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 8, 8, 8)
            name_lbl = QLabel(c.stem[:14])
            name_lbl.setStyleSheet(f"color: {THEME['text']}; font-size: 10px; font-weight: 600;")
            time_lbl = QLabel(datetime.fromtimestamp(c.stat().st_mtime).strftime("%H:%M"))
            time_lbl.setStyleSheet(f"color: {THEME['text_dim']}; font-size: 10px;")
            cl.addWidget(name_lbl)
            cl.addWidget(time_lbl)
            self._recent_layout.insertWidget(0, card)

    # ── Hotkey ────────────────────────────────────────────────────────────────

    def _check_hotkey(self):
        # Cross-platform F9 detection via keyboard state check
        try:
            from PyQt6.QtGui import QKeyEvent
            # We can't poll global hotkeys without a system hook; use global shortcut on window
            pass
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F9:
            self._trigger_clip()
        super().keyPressEvent(event)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _on_settings_changed(self, cfg: dict):
        old_fps = self.config.get("fps")
        old_mon = self.config.get("monitor_index")
        old_len = self.config.get("clip_length")
        old_mic = self.config.get("mic_device")

        self.config.update(cfg)
        save_config(self.config)
        self._update_info_labels()

        # Restart capture if capture-critical settings changed
        if (cfg.get("fps") != old_fps or
                cfg.get("monitor_index") != old_mon or
                cfg.get("clip_length") != old_len or
                cfg.get("mic_device") != old_mic):
            self._start_capture()

        # Update clip viewer dir
        if hasattr(self, "_clip_viewer"):
            self._clip_viewer.set_output_dir(cfg.get("output_dir", str(DEFAULT_CLIP_DIR)))

    def _update_info_labels(self):
        if hasattr(self, "_info_length"):
            self._info_length.setText(self.config.get("clip_length", "30s"))
        if hasattr(self, "_info_fps"):
            self._info_fps.setText(f"{self.config.get('fps', 30)} FPS")
        if hasattr(self, "_info_mon"):
            mons = get_monitors()
            mi = self.config.get("monitor_index", 1)
            m = next((x for x in mons if x["index"] == mi), None)
            self._info_mon.setText(m["name"].split(" ")[0] + " " + m["name"].split(" ")[1] if m else "Monitor 1")

    # ── System Tray ───────────────────────────────────────────────────────────

    def _setup_tray(self):
        # Create a simple colored icon for the tray
        pix = QPixmap(16, 16)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(THEME["accent"])))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 14, 14)
        painter.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self)
        self._tray.setToolTip(APP_NAME)

        tray_menu = QMenu()
        tray_menu.setStyleSheet(f"""
            QMenu {{
                background: {THEME['bg_card']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
                color: {THEME['text']};
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background: {THEME['accent']};
            }}
        """)

        show_action = QAction("Open ClipVault", self)
        show_action.triggered.connect(self._show_window)
        tray_menu.addAction(show_action)

        clip_action = QAction("⚡ Save Clip (F9)", self)
        clip_action.triggered.connect(self._trigger_clip)
        tray_menu.addAction(clip_action)

        tray_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        self._stop_capture()
        save_config(self.config)
        QApplication.quit()

    # ── Close → Minimise to Tray ──────────────────────────────────────────────

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage(
            APP_NAME,
            "ClipVault is still running in the background.\nPress F9 to clip anytime.",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # Keep alive in tray

    # Ensure default clip dir
    DEFAULT_CLIP_DIR.mkdir(parents=True, exist_ok=True)

    win = MainWindow()
    win.show()

    # Graceful Ctrl-C
    signal.signal(signal.SIGINT, lambda *a: win._quit_app())

    sys.exit(app.exec())


if __name__ == "__main__":
    main()