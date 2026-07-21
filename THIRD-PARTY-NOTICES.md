# Third-party notices

This project's own source code is licensed under the MIT License (see `LICENSE`).
It bundles and depends on the following third-party components, which remain
under their own licences.

## FFmpeg (bundled binary — GPL v3)

`bin/ffmpeg.exe`, and the copy shipped inside the installer, is an unmodified
prebuilt FFmpeg binary:

- **Version:** `2026-05-21-git-0857141823-essentials_build`
- **Source of build:** https://www.gyan.dev/ffmpeg/builds/
- **Project home / source code:** https://ffmpeg.org/ — https://git.ffmpeg.org/ffmpeg.git
- **Licence:** **GNU General Public License version 3 or later.** This build is
  configured with `--enable-gpl --enable-version3` (and includes GPL components
  such as libx264), so the binary as a whole is covered by the GPL v3.
- **Licence text:** https://www.gnu.org/licenses/gpl-3.0.html

Clipping Software runs FFmpeg as a **separate program** (it is launched as a
subprocess and communicated with over a pipe); it is not linked into the
application. The application's own code is therefore released under the MIT
Licence, while the FFmpeg binary remains under the GPL v3.

The complete corresponding source code for the bundled FFmpeg build is
available from the URLs above.

## Python packages

Installed via pip and used at runtime — each under its own licence:

| Package | Licence | Purpose |
| --- | --- | --- |
| customtkinter | MIT | User interface |
| dxcam | MIT | GPU screen capture (Desktop Duplication) |
| mss | MIT | Screen capture fallback (GDI) |
| opencv-python | Apache-2.0 | JPEG encoding, video decoding for playback |
| numpy | BSD-3-Clause | Frame and audio buffers |
| SoundCard | BSD-3-Clause | Microphone and system-audio (WASAPI loopback) capture |
| sounddevice | MIT | Audio device listing and clip playback |
| Pillow | MIT-CMU | Icons and thumbnails |
| pystray | LGPL-3.0 | System tray icon |
| keyboard | MIT | Global clip hotkey |
| pywin32 | PSF-2.0 | Clipboard integration |
| PyInstaller | GPL-2.0 with bootloader exception | Builds the .exe (build-time only) |

Inno Setup (used to build the installer) is distributed under its own
[modified BSD licence](https://jrsoftware.org/files/is/license.txt).
