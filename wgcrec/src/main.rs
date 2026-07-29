// Continuous WGC -> on-GPU Media Foundation hardware encode, into a rolling ring
// of short mp4 segments (a replay buffer, like ShadowPlay / SteelSeries). Captures
// the FOREGROUND window (so it follows you into a game) and re-targets when you
// switch apps; falls back to a monitor when there's no suitable window.
//
// usage: wgcrec <out_dir> <fallback_monitor_index> <seg_seconds> <ring> <fps>
//
// Frames never leave the GPU (send_frame -> MF hardware encoder), so a game
// maxing the graphics cores can't starve it the way a CPU-readback path does.
use std::path::Path;
use std::time::Instant;

use windows_capture::capture::{Context, GraphicsCaptureApiHandler};
use windows_capture::encoder::{
    AudioSettingsBuilder, ContainerSettingsBuilder, VideoEncoder, VideoSettingsBuilder,
};
use windows_capture::frame::Frame;
use windows_capture::graphics_capture_api::InternalCaptureControl;
use windows_capture::monitor::Monitor;
use windows_capture::settings::{
    ColorFormat, CursorCaptureSettings, DirtyRegionSettings, DrawBorderSettings,
    MinimumUpdateIntervalSettings, SecondaryWindowSettings, Settings,
};
use windows_capture::window::Window;

#[derive(Clone, PartialEq)]
enum Target {
    Window(usize, u32, u32), // hwnd, w, h
    Monitor(usize, u32, u32),
}

fn even(v: i32) -> u32 {
    let v = v.max(2) as u32;
    v - (v % 2)
}

// Pick what to capture right now: the foreground window if it's a real, big,
// visible window; otherwise the fallback monitor.
fn pick_target(fallback_mon: usize) -> Target {
    if let Ok(win) = Window::foreground() {
        if win.is_valid() {
            if let (Ok(w), Ok(h)) = (win.width(), win.height()) {
                let title = win.title().unwrap_or_default();
                let is_ours = title.contains("Clipping Software");
                if w >= 400 && h >= 300 && !is_ours {
                    return Target::Window(win.as_raw_hwnd() as usize, even(w), even(h));
                }
            }
        }
    }
    if let Ok(mon) = Monitor::from_index(fallback_mon) {
        let w = mon.width().unwrap_or(1920);
        let h = mon.height().unwrap_or(1080);
        return Target::Monitor(fallback_mon, even(w as i32), even(h as i32));
    }
    Target::Monitor(fallback_mon, 1920, 1080)
}

struct Cfg {
    dir: String,
    fallback_mon: usize,
    seg_seconds: f64,
    ring: u32,
    fps: u32,
    w: u32,
    h: u32,
    target: Target,
}

struct Cap {
    cfg: Cfg,
    encoder: Option<VideoEncoder>,
    seg_index: u32,
    seg_start: Instant,
    frame_count: u64,
}

impl Cap {
    fn open(dir: &str, index: u32, w: u32, h: u32, fps: u32) -> Result<VideoEncoder, Box<dyn std::error::Error + Send + Sync>> {
        let path = format!("{}/seg{:04}.mp4", dir, index);
        let _ = std::fs::remove_file(&path);
        Ok(VideoEncoder::new(
            VideoSettingsBuilder::new(w, h).frame_rate(fps),
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
        Ok(Self { cfg, encoder: Some(enc), seg_index: 0, seg_start: Instant::now(), frame_count: 0 })
    }

    fn on_frame_arrived(
        &mut self,
        frame: &mut Frame,
        cc: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        self.frame_count += 1;

        // At each segment boundary: either re-target (foreground changed) or
        // rotate to the next segment in the ring.
        if self.seg_start.elapsed().as_secs_f64() >= self.cfg.seg_seconds {
            let now_target = pick_target(self.cfg.fallback_mon);
            if now_target != self.cfg.target {
                // foreground switched -> end this session so main() rebuilds on
                // the new target (and clears the ring for a clean, same-size clip)
                if let Some(e) = self.encoder.take() {
                    let _ = e.finish();
                }
                cc.stop();
                return Ok(());
            }
            // rotate segment
            let next = (self.seg_index + 1) % self.cfg.ring;
            let new_enc = Cap::open(&self.cfg.dir, next, self.cfg.w, self.cfg.h, self.cfg.fps)?;
            if let Some(old) = self.encoder.replace(new_enc) {
                let _ = old.finish();
            }
            self.seg_index = next;
            self.seg_start = Instant::now();
        }

        if let Some(e) = self.encoder.as_mut() {
            e.send_frame(frame)?;
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
        let target = pick_target(fallback_mon);
        clear_dir(&dir); // fresh ring per target so a clip never mixes sizes
        let (w, h) = match target {
            Target::Window(_, w, h) => (w, h),
            Target::Monitor(_, w, h) => (w, h),
        };
        eprintln!("wgcrec: capturing {:?} {}x{}", match &target {
            Target::Window(hwnd, ..) => format!("window 0x{:x}", hwnd),
            Target::Monitor(i, ..) => format!("monitor {}", i),
        }, w, h);

        let cfg = Cfg { dir: dir.clone(), fallback_mon, seg_seconds, ring, fps, w, h, target: target.clone() };

        let res = match target {
            Target::Window(hwnd, ..) => {
                let win = Window::from_raw_hwnd(hwnd as *mut std::ffi::c_void);
                let settings = Settings::new(
                    win, CursorCaptureSettings::WithoutCursor, DrawBorderSettings::WithoutBorder,
                    SecondaryWindowSettings::Default, MinimumUpdateIntervalSettings::Default,
                    DirtyRegionSettings::Default, ColorFormat::Rgba8, cfg,
                );
                Cap::start(settings)
            }
            Target::Monitor(idx, ..) => {
                let mon = Monitor::from_index(idx).unwrap_or_else(|_| Monitor::primary().unwrap());
                let settings = Settings::new(
                    mon, CursorCaptureSettings::WithoutCursor, DrawBorderSettings::WithoutBorder,
                    SecondaryWindowSettings::Default, MinimumUpdateIntervalSettings::Default,
                    DirtyRegionSettings::Default, ColorFormat::Rgba8, cfg,
                );
                Cap::start(settings)
            }
        };
        if let Err(e) = res {
            eprintln!("wgcrec: capture error: {:?}", e);
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    }
}
