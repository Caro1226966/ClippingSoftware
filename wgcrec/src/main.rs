// Continuous WGC -> on-GPU Media Foundation hardware encode, into a rolling ring
// of short mp4 segments (a replay buffer, like ShadowPlay / SteelSeries). Captures
// the FOREGROUND window (so it follows you into a game) and re-targets only when
// you switch to a DIFFERENT window; falls back to a monitor when there's no
// suitable window. Frames never leave the GPU (send_frame -> MF hardware encoder).
//
// usage: wgcrec <out_dir> <fallback_monitor_1based> <seg_seconds> <ring> <fps>
use std::fs::OpenOptions;
use std::io::Write;
use std::time::Instant;

use windows_capture::capture::{Context, GraphicsCaptureApiHandler};
use windows_capture::encoder::{
    AudioSettingsBuilder, ContainerSettingsBuilder, VideoEncoder, VideoSettingsBuilder,
    VideoSettingsSubType,
};
use windows_capture::frame::Frame;
use windows_capture::graphics_capture_api::InternalCaptureControl;
use windows_capture::monitor::Monitor;
use windows_capture::settings::{
    ColorFormat, CursorCaptureSettings, DirtyRegionSettings, DrawBorderSettings,
    MinimumUpdateIntervalSettings, SecondaryWindowSettings, Settings,
};
use windows_capture::window::Window;

// Identity ONLY (no width/height) so a borderless window reporting a 1px size
// flicker never counts as a target change (that was clearing the ring -> freezes).
#[derive(Clone, Copy, PartialEq)]
enum TargetId {
    Window(usize),
    Monitor(usize),
}

struct TargetInfo {
    id: TargetId,
    w: u32,
    h: u32,
}

fn even(v: i32) -> u32 {
    let v = v.max(2) as u32;
    v - (v % 2)
}

fn pick_target(fallback_mon: usize) -> TargetInfo {
    if let Ok(win) = Window::foreground() {
        if win.is_valid() {
            if let (Ok(w), Ok(h)) = (win.width(), win.height()) {
                let title = win.title().unwrap_or_default();
                let is_ours = title.contains("Clipping Software");
                if w >= 400 && h >= 300 && !is_ours {
                    return TargetInfo { id: TargetId::Window(win.as_raw_hwnd() as usize), w: even(w), h: even(h) };
                }
            }
        }
    }
    if let Ok(mon) = Monitor::from_index(fallback_mon) {
        let w = even(mon.width().unwrap_or(1920) as i32);
        let h = even(mon.height().unwrap_or(1080) as i32);
        return TargetInfo { id: TargetId::Monitor(fallback_mon), w, h };
    }
    TargetInfo { id: TargetId::Monitor(fallback_mon), w: 1920, h: 1080 }
}

fn log_line(dir: &str, msg: &str) {
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(format!("{}/wgcrec.log", dir)) {
        let _ = writeln!(f, "{}", msg);
    }
}

struct Cfg {
    dir: String,
    fallback_mon: usize,
    seg_seconds: f64,
    ring: u32,
    fps: u32,
    w: u32,
    h: u32,
    id: TargetId,
}

struct Cap {
    cfg: Cfg,
    encoder: Option<VideoEncoder>,
    seg_index: u32,
    seg_start: Instant,
    seg_frames: u64,
}

impl Cap {
    fn open(dir: &str, index: u32, w: u32, h: u32, fps: u32) -> Result<VideoEncoder, Box<dyn std::error::Error + Send + Sync>> {
        let path = format!("{}/seg{:04}.mp4", dir, index);
        let _ = std::fs::remove_file(&path);
        Ok(VideoEncoder::new(
            // H.264 (not the crate's HEVC default) — far lighter to decode and
            // universally playable; HEVC clips stuttered badly on playback.
            VideoSettingsBuilder::new(w, h).frame_rate(fps).sub_type(VideoSettingsSubType::H264),
            AudioSettingsBuilder::default().disabled(true),
            ContainerSettingsBuilder::default(),
            path,
        )?)
    }
}

impl GraphicsCaptureApiHandler for Cap {
    type Flags = Cfg;
    type Error = Box<dyn std::error::Error + Send + Sync>;

    fn new(ctx: Context<Self::Flags>) -> Result<Self, Self::Error> {
        let cfg = ctx.flags;
        let enc = Cap::open(&cfg.dir, 0, cfg.w, cfg.h, cfg.fps)?;
        Ok(Self { cfg, encoder: Some(enc), seg_index: 0, seg_start: Instant::now(), seg_frames: 0 })
    }

    fn on_frame_arrived(
        &mut self,
        frame: &mut Frame,
        cc: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        let elapsed = self.seg_start.elapsed().as_secs_f64();
        if elapsed >= self.cfg.seg_seconds {
            // Re-target only if the foreground is now a DIFFERENT window/monitor.
            if pick_target(self.cfg.fallback_mon).id != self.cfg.id {
                if let Some(e) = self.encoder.take() {
                    std::thread::spawn(move || { let _ = e.finish(); });
                }
                cc.stop();
                return Ok(());
            }
            // Log the raw delivered fps for this segment (diagnostics).
            log_line(&self.cfg.dir, &format!("seg {} : {} frames in {:.2}s = {:.1} fps",
                self.seg_index, self.seg_frames, elapsed, self.seg_frames as f64 / elapsed));
            // Rotate: open the next segment, swap, finish the old one OFF-thread so
            // the capture thread never stalls waiting on the moov write.
            let next = (self.seg_index + 1) % self.cfg.ring;
            let new_enc = Cap::open(&self.cfg.dir, next, self.cfg.w, self.cfg.h, self.cfg.fps)?;
            if let Some(old) = self.encoder.replace(new_enc) {
                std::thread::spawn(move || { let _ = old.finish(); });
            }
            self.seg_index = next;
            self.seg_start = Instant::now();
            self.seg_frames = 0;
        }

        if let Some(e) = self.encoder.as_mut() {
            e.send_frame(frame)?;
            self.seg_frames += 1;
        }
        Ok(())
    }

    fn on_closed(&mut self) -> Result<(), Self::Error> {
        Ok(())
    }
}

fn clear_dir(dir: &str) {
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            if e.path().extension().map(|x| x == "mp4").unwrap_or(false) {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let dir = a.get(1).cloned().unwrap_or_else(|| ".".into());
    let fallback_mon: usize = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(1);
    let seg_seconds: f64 = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(2.0);
    let ring: u32 = a.get(4).and_then(|s| s.parse().ok()).unwrap_or(40);
    let fps: u32 = a.get(5).and_then(|s| s.parse().ok()).unwrap_or(60);

    std::fs::create_dir_all(&dir).ok();

    loop {
        let t = pick_target(fallback_mon);
        clear_dir(&dir); // fresh ring per target so a clip never mixes sizes
        log_line(&dir, &format!("--- capturing {} {}x{} ---", match t.id {
            TargetId::Window(h) => format!("window 0x{:x}", h),
            TargetId::Monitor(i) => format!("monitor {}", i),
        }, t.w, t.h));

        let cfg = Cfg { dir: dir.clone(), fallback_mon, seg_seconds, ring, fps, w: t.w, h: t.h, id: t.id };

        let res = match t.id {
            TargetId::Window(hwnd) => {
                let win = Window::from_raw_hwnd(hwnd as *mut std::ffi::c_void);
                Cap::start(Settings::new(
                    win, CursorCaptureSettings::WithCursor, DrawBorderSettings::WithoutBorder,
                    SecondaryWindowSettings::Default, MinimumUpdateIntervalSettings::Default,
                    DirtyRegionSettings::Default, ColorFormat::Rgba8, cfg,
                ))
            }
            TargetId::Monitor(idx) => {
                let mon = Monitor::from_index(idx).unwrap_or_else(|_| Monitor::primary().unwrap());
                Cap::start(Settings::new(
                    mon, CursorCaptureSettings::WithCursor, DrawBorderSettings::WithoutBorder,
                    SecondaryWindowSettings::Default, MinimumUpdateIntervalSettings::Default,
                    DirtyRegionSettings::Default, ColorFormat::Rgba8, cfg,
                ))
            }
        };
        if let Err(e) = res {
            log_line(&dir, &format!("capture error: {:?}", e));
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    }
}
