# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec — produces a single background-friendly ClippingSoftware.exe.

Build with:   .venv\\Scripts\\python.exe -m PyInstaller ClippingSoftware.spec --noconfirm
Output:       dist\\ClippingSoftware.exe
"""
from PyInstaller.utils.hooks import collect_all

# ffmpeg is invoked as a subprocess, so it ships alongside the code.
datas = [('bin/ffmpeg.exe', 'bin'), ('defaults.csv', '.')]
binaries = []
hiddenimports = [
    'pystray._win32',      # tray backend is picked at runtime
    'win32clipboard',      # "copy file to clipboard" in the share menu
    'PIL._tkinter_finder',
]

# These ship data files / native DLLs that PyInstaller can't infer on its own
# (customtkinter themes, PortAudio, soundcard's WASAPI bindings, dxcam, and the
# windows_capture Rust extension used for fullscreen game capture).
for pkg in ('customtkinter', 'soundcard', 'sounddevice', 'dxcam', 'windows_capture'):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # tests.py is an old PyQt prototype that isn't part of the app — keep its
    # (and other unused) heavyweight deps out of the bundle.
    excludes=['PyQt6', 'PyQt5', 'PySide6', 'matplotlib', 'pandas', 'scipy',
              'IPython', 'pytest', 'tkinter.test', 'test'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ClippingSoftware',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # windowed: no console pops up when run from Startup
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
