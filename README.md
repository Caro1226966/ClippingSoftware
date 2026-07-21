# Clipping Software

A background clip recorder for Windows. It continuously records the last stretch
of your screen, so when something cool happens you just press a key and it saves
the clip that **already happened** — no need to be recording in advance.

## Download

Grab **`ClippingSoftware-Setup.exe`** from the
[latest release](../../releases/latest) and run it.

> Windows will show a **"Windows protected your PC"** warning, because the
> installer isn't code-signed. Click **More info → Run anyway**.

You can install it just for yourself (no admin needed) or for everyone on the PC.
The installer can optionally add a desktop shortcut and start it with Windows.

## Using it

- It sits in the **system tray** and records in the background.
- Press the clip key (**F8** by default) to save the last N seconds.
- Clips are saved to **`Videos\clipping`**.
- Open it from the Start Menu or the tray icon to change the key, clip length,
  FPS, monitor and audio devices.
- The **video icon in the top-right** opens the clip browser.

### What gets recorded

- Your screen, on whichever monitor you choose
- **Your microphone and your game/system audio**, mixed together
- Your mouse cursor (optional — the cursor isn't captured by the screen APIs, so
  it's drawn in)

### Clip browser

Tiles of every clip, with a player you can scrub through. Hover a tile for the
**⋮** menu to rename, delete or share.

**Share** re-compresses a clip to fit **10 / 20 / 50 / 100 MB** so it will
actually upload to Discord, then copies it to your clipboard — paste it into the
chat with <kbd>Ctrl</kbd>+<kbd>V</kbd>. The compressed copy is temporary and is
cleaned up when you leave the menu.

## Notes and limitations

- 64-bit Windows 10/11.
- Capture uses the GPU (Desktop Duplication) where possible and falls back to GDI
  automatically.
- Games in **exclusive fullscreen** may not capture — use borderless/windowed if
  you get a black clip.
- Settings live in `%APPDATA%\ClippingSoftware\`, along with a `log.txt` that's
  worth checking if something misbehaves.

## Building from source

Requires Python 3.11+ on Windows.

```sh
python -m venv .venv
.venv\Scripts\python.exe -m pip install customtkinter dxcam mss opencv-python numpy ^
    soundcard sounddevice pillow pystray keyboard pywin32 pyinstaller
```

Run it directly:

```sh
.venv\Scripts\python.exe main.py --show
```

Build the exe, then the installer ([Inno Setup](https://jrsoftware.org/isinfo.php)):

```sh
.venv\Scripts\python.exe -m PyInstaller ClippingSoftware.spec --noconfirm
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

Both land in `dist\`.

### How it works

- **`config.py`** — paths, settings, and the capture engines: `ScreenCapture`
  (dxcam with an mss fallback, writing JPEG frames into a rolling buffer) and
  `AudioSource`/`AudioManager` (mic + WASAPI loopback).
- **`main.py`** — the settings window, tray icon, clip hotkey, and clip encoding.
- **`clip_browser.py`** — the clip gallery, player and share/compress flow.
- **`button_callbacks.py`** — UI callbacks and settings persistence.

Captured frames are timestamped and re-timed to a constant frame rate when a clip
is written, so clips play at real speed even if the capture rate dipped, with the
audio taken from the same wall-clock window to stay in sync.

## Credit
This program was made by Caro122 and Claude. I do not take credit for anything made by claude.

## Licence

MIT — see [`LICENSE`](LICENSE).

Ships a prebuilt **FFmpeg** binary, which is licensed separately under the
**GPL v3**; see [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) for details
and source links.
