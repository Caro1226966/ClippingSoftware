# wgcrec — native WGC + hardware-encode recorder

The GPU-native capture helper the app shells out to (`bin/wgcrec.exe`). It captures
the foreground window (or a monitor) via **Windows Graphics Capture** and encodes on
the GPU's **hardware encoder** through Media Foundation — no CPU readback — into a
rolling ring of short `.mp4` segments. This is what lets recording survive a game
pinning the GPU at ~100%, where Desktop Duplication and CPU-readback capture collapse.

## Build
Needs the Rust **GNU** toolchain plus MinGW's `dlltool` on PATH (for the windows-rs
import libraries). No Visual Studio required.

```
rustup default stable-x86_64-pc-windows-gnu
cargo build --release
copy target\release\wgcrec.exe ..\bin\wgcrec.exe
```

The resulting exe links only Windows system DLLs (self-contained).

## Usage
`wgcrec.exe <out_dir> <fallback_monitor_1based> <segment_seconds> <ring_count> <fps>`
