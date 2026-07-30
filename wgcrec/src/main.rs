// Continuous WGC -> on-GPU Media Foundation hardware encode of a MONITOR, into a
// rolling ring of short mp4 segments (a replay buffer). Monitor capture is stable
// (fixed size, never re-targets), unlike following the foreground window which
// crashed on window resizes and wiped the ring on every alt-tab. Whole-screen is
// also what the app wants; with MPO/independent-flip disabled it captures games at
// full 60fps. Frames never leave the GPU (send_frame -> MF hardware encoder).
//
// usage: wgcrec <out_dir> <monitor_1based> <seg_seconds> <ring> <fps>
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

fn even(v: u32) -> u32 {
    if v < 2 { 2 } else { v - (v % 2) }
}

fn log_line(dir: &str, msg: &str) {
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(format!("{}/wgcrec.log", dir)) {
        let _ = writeln!(f, "{}", msg);
    }
}

struct Cfg {
    dir: String,
    seg_seconds: f64,
    ring: u32,
    fps: u32,
    w: u32,
    h: u32,
}

struct Cap {
    cfg: Cfg,
    encoder: Option<VideoEncoder>,
    seg_index: u64,   // MONOTONIC counter (never wraps) so filename order == capture order
    seg_start: Instant,
    seg_frames: u64,
}

impl Cap {
    fn open(dir: &str, index: u64, w: u32, h: u32, fps: u32) -> Result<VideoEncoder, Box<dyn std::error::Error + Send + Sync>> {
        let path = format!("{}/seg{:010}.mp4", dir, index);
        let _ = std::fs::remove_file(&path);
        Ok(VideoEncoder::new(
            // H.264 (not the crate's HEVC default) — light to decode, universally playable.
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
        _cc: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        let elapsed = self.seg_start.elapsed().as_secs_f64();
        if elapsed >= self.cfg.seg_seconds {
            log_line(&self.cfg.dir, &format!("seg {} : {} frames in {:.2}s = {:.1} fps",
                self.seg_index, self.seg_frames, elapsed, self.seg_frames as f64 / elapsed));
            // Open the next segment (monotonic index), swap, finish the old one
            // OFF-thread so the capture thread never stalls on the moov write.
            let next = self.seg_index + 1;
            let new_enc = Cap::open(&self.cfg.dir, next, self.cfg.w, self.cfg.h, self.cfg.fps)?;
            if let Some(old) = self.encoder.replace(new_enc) {
                std::thread::spawn(move || { let _ = old.finish(); });
            }
            // Keep only the last `ring` segments on disk.
            if next >= self.cfg.ring as u64 {
                let old_idx = next - self.cfg.ring as u64;
                let _ = std::fs::remove_file(format!("{}/seg{:010}.mp4", self.cfg.dir, old_idx));
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

fn clear_segments(dir: &str) {
    if let Ok(rd) = std::fs::read_dir(dir) {
        for e in rd.flatten() {
            let p = e.path();
            if p.extension().map(|x| x == "mp4").unwrap_or(false)
                && p.file_name().map(|n| n.to_string_lossy().starts_with("seg")).unwrap_or(false)
            {
                let _ = std::fs::remove_file(p);
            }
        }
    }
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let dir = a.get(1).cloned().unwrap_or_else(|| ".".into());
    let mon_index: usize = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(1);
    let seg_seconds: f64 = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(2.0);
    let ring: u32 = a.get(4).and_then(|s| s.parse().ok()).unwrap_or(40);
    let fps: u32 = a.get(5).and_then(|s| s.parse().ok()).unwrap_or(60);

    std::fs::create_dir_all(&dir).ok();

    loop {
        let mon = match Monitor::from_index(mon_index).or_else(|_| Monitor::primary()) {
            Ok(m) => m,
            Err(_) => {
                log_line(&dir, "no monitor; retrying");
                std::thread::sleep(std::time::Duration::from_millis(1000));
                continue;
            }
        };
        let w = even(mon.width().unwrap_or(1920));
        let h = even(mon.height().unwrap_or(1080));
        clear_segments(&dir);
        log_line(&dir, &format!("--- capturing monitor {} {}x{} ---", mon_index, w, h));

        let cfg = Cfg { dir: dir.clone(), seg_seconds, ring, fps, w, h };
        let res = Cap::start(Settings::new(
            mon,
            CursorCaptureSettings::WithCursor,
            DrawBorderSettings::WithoutBorder,
            SecondaryWindowSettings::Default,
            MinimumUpdateIntervalSettings::Default,
            DirtyRegionSettings::Default,
            ColorFormat::Rgba8,
            cfg,
        ));
        if let Err(e) = res {
            log_line(&dir, &format!("capture error: {:?}; restarting", e));
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    }
}
