use crate::config::{curve_presets, default_curve, ConfigManager, CurvePoint};
use crate::cpu_msr::{CoreKind, CpuTelemetry};
use crate::gpu_rtd3::DgpuState;
use std::cell::RefCell;
use std::sync::{Arc, Condvar, Mutex};
use windows::core::PCWSTR;
use windows::Win32::Foundation::{COLORREF, HANDLE, HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_USE_IMMERSIVE_DARK_MODE};
use windows::Win32::Graphics::Gdi::{
    BeginPaint, BitBlt, CreateCompatibleDC, CreateDIBSection, CreateFontW, CreatePen,
    CreateRoundRectRgn, CreateSolidBrush, DeleteDC, DeleteObject, DrawTextW, EndPaint, FillRect,
    FillRgn, FrameRgn, InvalidateRect, LineTo, MoveToEx, SelectObject, SetBkMode, SetTextColor,
    BITMAPINFO, BITMAPINFOHEADER, BI_RGB, CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, DEFAULT_CHARSET,
    DIB_RGB_COLORS, DRAW_TEXT_FORMAT, DT_CENTER, DT_END_ELLIPSIS, DT_LEFT, DT_NOPREFIX, DT_RIGHT,
    DT_SINGLELINE, DT_VCENTER, DT_WORDBREAK, HBITMAP, HDC, HFONT, HGDIOBJ, OUT_DEFAULT_PRECIS,
    PAINTSTRUCT, PS_DASH, PS_SOLID, SRCCOPY, TRANSPARENT,
};
use windows::Win32::Graphics::GdiPlus::{
    GdipCreateFromHDC, GdipCreateLineBrushI, GdipCreatePen1, GdipCreateSolidFill,
    GdipDeleteBrush, GdipDeleteGraphics, GdipDeletePen, GdipDrawLineI, GdipFillEllipseI,
    GdipFillPolygonI, GdipSetSmoothingMode, GdiplusShutdown, GdiplusStartup, GdiplusStartupInput,
    GpBrush, GpGraphics, GpLineGradient, GpPen, GpSolidFill, Point as GpPoint,
    SmoothingModeAntiAlias, UnitPixel, WrapModeTileFlipX,
};
use windows::Win32::System::ProcessStatus::K32EmptyWorkingSet;
use windows::Win32::System::Threading::GetCurrentProcess;
use windows::Win32::UI::HiDpi::{
    GetDpiForWindow, SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{ReleaseCapture, SetCapture};
use windows::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY,
    NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreateIconIndirect, CreatePopupMenu, CreateWindowExW, DefWindowProcW,
    DestroyIcon, DestroyMenu, DestroyWindow, DispatchMessageW, GetClientRect, GetCursorPos,
    GetMessageW, IsWindowVisible, LoadCursorW, LoadIconW, PostQuitMessage, RegisterClassW,
    SetForegroundWindow, ShowWindow, TrackPopupMenu, TranslateMessage, CS_DBLCLKS, CW_USEDEFAULT,
    HICON, ICONINFO, IDC_ARROW, IDC_HAND, MF_SEPARATOR, MF_STRING, MSG, SW_HIDE, SW_RESTORE,
    SW_SHOW, TPM_BOTTOMALIGN, TPM_LEFTALIGN, TPM_RETURNCMD, WM_APP, WM_CLOSE, WM_DESTROY,
    WM_DPICHANGED, WM_ENDSESSION, WM_ERASEBKGND, WM_LBUTTONDBLCLK, WM_LBUTTONDOWN, WM_LBUTTONUP,
    WM_MOUSEMOVE, WM_PAINT, WM_QUERYENDSESSION, WM_RBUTTONDOWN, WM_SIZE, WNDCLASSW,
    WS_CLIPCHILDREN, WS_OVERLAPPEDWINDOW,
};

pub const WM_APP_TELEMETRY: u32 = WM_APP + 1;
pub const WM_APP_TRAY: u32 = WM_APP + 2;
pub const WM_APP_SHOW_WINDOW: u32 = WM_APP + 3;
pub const WM_APP_QUIT_CLEAN: u32 = WM_APP + 4;
pub const WM_APP_IPC_MODE: u32 = WM_APP + 5;

const TRAY_ID_CPU: u32 = 101;
const TRAY_ID_GPU: u32 = 102;

#[derive(Clone)]
pub struct SharedTelemetry {
    pub dgpu_state: DgpuState,
    pub gpu_sleeping: bool,
    pub gpu_temp: Option<i32>,
    pub gpu_pwr: Option<f64>,
    pub gpu_util: Option<u32>,
    pub mem_util: Option<u32>,
    pub ec_cpu_temp: Option<i32>,
    pub cpu: CpuTelemetry,
    pub fan1_rpm: Option<i32>,
    pub fan2_rpm: Option<i32>,
    pub mode: String,
    pub target_rpm: i32,
    pub curve_ref_temp: f64,
    pub safety_level: u8,
    pub wmi_ok: bool,
    pub mode_desc: String,
}

pub struct ControllerBridge {
    pub config: ConfigManager,
    pub latest_telemetry: Mutex<Option<SharedTelemetry>>,
    pub wake_pair: (Mutex<bool>, Condvar),
    pub running: std::sync::atomic::AtomicBool,
}

impl ControllerBridge {
    pub fn notify_wake(&self) {
        if let Ok(mut g) = self.wake_pair.0.lock() {
            *g = true;
            self.wake_pair.1.notify_all();
        }
    }
}

#[derive(Clone)]
struct HitBox {
    rect: RECT,
    action: UiAction,
}

#[derive(Clone)]
enum UiAction {
    ToggleCores,
    SelectMode(usize),
    TargetDelta(i32),
    TargetPreset(i32),
    CurveSensor(&'static str),
    CurvePreset(usize),
    CurveAddPoint,
    CurveReset,
    ManualDelta(i32),
    ManualPreset(i32),
    ConfirmBiosAuto,
    ActivateBoost,
    ReturnTargetMode,
    ToggleMinToTray,
    ToggleAutostart,
    ToggleSafety,
    MinimizeToTray,
    SafeExit,
}

struct AppUiState {
    hwnd: HWND,
    bridge: Arc<ControllerBridge>,
    dpi: f32,
    show_cores_panel: bool,
    hitboxes: Vec<HitBox>,
    hover_idx: Option<usize>,

    // Interactive curve & slider state
    curve_plot_rect: Option<RECT>,
    dragging_curve_pt: Option<usize>,
    hover_curve_pt: Option<usize>,
    slider_rect: Option<RECT>,
    dragging_slider: bool,

    // Offscreen GDI backbuffer (freed immediately when hidden to tray!)
    back_dc: HDC,
    back_bmp: HBITMAP,
    back_w: i32,
    back_h: i32,

    // Cached tray icons to avoid GDI churn when values haven't changed
    tray_cpu_added: bool,
    tray_gpu_added: bool,
    tray_cpu_icon: HICON,
    tray_gpu_icon: HICON,
    last_cpu_badge_key: String,
    last_gpu_badge_key: String,
}

thread_local! {
    static UI_STATE: RefCell<Option<AppUiState>> = const { RefCell::new(None) };
}

fn wide_null(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn copy_wide_trunc(dst: &mut [u16], text: &str) {
    dst.fill(0);
    for (i, ch) in text.encode_utf16().take(dst.len().saturating_sub(1)).enumerate() {
        dst[i] = ch;
    }
}

#[inline]
fn rgb(r: u8, g: u8, b: u8) -> COLORREF {
    COLORREF((r as u32) | ((g as u32) << 8) | ((b as u32) << 16))
}

#[inline]
fn argb(a: u8, r: u8, g: u8, b: u8) -> u32 {
    ((a as u32) << 24) | ((r as u32) << 16) | ((g as u32) << 8) | (b as u32)
}

#[inline]
fn pt_in_rect(r: &RECT, x: i32, y: i32) -> bool {
    x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
}

unsafe fn draw_round_box(hdc: HDC, r: RECT, radius: i32, fill: COLORREF, border: COLORREF) {
    let rgn = CreateRoundRectRgn(r.left, r.top, r.right + 1, r.bottom + 1, radius, radius);
    let br_fill = CreateSolidBrush(fill);
    let br_bord = CreateSolidBrush(border);
    let _ = FillRgn(hdc, rgn, br_fill);
    let _ = FrameRgn(hdc, rgn, br_bord, 1, 1);
    let _ = DeleteObject(br_fill);
    let _ = DeleteObject(br_bord);
    let _ = DeleteObject(rgn);
}

unsafe fn make_font(px_height: i32, weight: i32) -> HFONT {
    let face = wide_null("Segoe UI");
    CreateFontW(
        -px_height.max(8),
        0,
        0,
        0,
        weight,
        0,
        0,
        0,
        DEFAULT_CHARSET.0 as u32,
        OUT_DEFAULT_PRECIS.0 as u32,
        CLIP_DEFAULT_PRECIS.0 as u32,
        CLEARTYPE_QUALITY.0 as u32,
        0,
        PCWSTR(face.as_ptr()),
    )
}

unsafe fn draw_label(hdc: HDC, mut r: RECT, text: &str, font: HFONT, color: COLORREF, flags: DRAW_TEXT_FORMAT) {
    let old = SelectObject(hdc, HGDIOBJ(font.0));
    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(hdc, color);
    let mut w = wide_null(text);
    let len = w.len().saturating_sub(1) as i32;
    let _ = DrawTextW(hdc, &mut w[..len as usize], &mut r, DT_NOPREFIX | flags);
    SelectObject(hdc, old);
}

fn temp_color_rgb(t: Option<f64>) -> COLORREF {
    match t {
        None => rgb(148, 163, 184),
        Some(v) if v < 60.0 => rgb(16, 185, 129),
        Some(v) if v < 75.0 => rgb(56, 189, 248),
        Some(v) if v < 85.0 => rgb(245, 158, 11),
        Some(_) => rgb(239, 68, 68),
    }
}

unsafe fn create_labeled_tray_hicon(header: &str, val_txt: &str, hdr_argb: u32, body_argb: u32) -> HICON {
    let size = 64i32;
    let bmi = BITMAPINFO {
        bmiHeader: BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: size,
            biHeight: -size, // top-down
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB.0,
            ..Default::default()
        },
        ..Default::default()
    };

    let hdc = CreateCompatibleDC(HDC::default());
    let mut bits: *mut std::ffi::c_void = std::ptr::null_mut();
    let Ok(hbm_color) = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &mut bits, HANDLE::default(), 0) else {
        let _ = DeleteDC(hdc);
        return HICON::default();
    };
    let old_bm = SelectObject(hdc, HGDIOBJ(hbm_color.0));

    // Fill crisp 64x64 rounded badge directly into 32-bit ARGB buffer
    let px = std::slice::from_raw_parts_mut(bits as *mut u32, (size * size) as usize);
    px.fill(0);
    for y in 1..63 {
        for x in 1..63 {
            // Smooth corner clip (radius 9)
            let cx = if x < 10 { 10 - x } else if x > 53 { x - 53 } else { 0 };
            let cy = if y < 10 { 10 - y } else if y > 53 { y - 53 } else { 0 };
            if cx * cx + cy * cy <= 81 {
                px[(y * size + x) as usize] = if y <= 22 { hdr_argb } else { body_argb };
            }
        }
    }

    // Render crisp white text via GDI and restore alpha=255 on rendered pixels
    let f_hdr = make_font(15, 800);
    let is_word = !val_txt.chars().any(|c| c.is_ascii_digit());
    let f_val = make_font(if is_word { 19 } else { 27 }, 800);

    draw_label(
        hdc,
        RECT { left: 1, top: 1, right: 63, bottom: 23 },
        header,
        f_hdr,
        rgb(255, 255, 255),
        DT_CENTER | DT_VCENTER | DT_SINGLELINE,
    );
    draw_label(
        hdc,
        RECT { left: 1, top: 23, right: 63, bottom: 63 },
        val_txt,
        f_val,
        rgb(255, 255, 255),
        DT_CENTER | DT_VCENTER | DT_SINGLELINE,
    );

    let _ = DeleteObject(f_hdr);
    let _ = DeleteObject(f_val);

    // GDI DrawText clears high alpha byte on touched pixels; set alpha=0xFF for all inside-badge pixels
    for y in 1..63 {
        for x in 1..63 {
            let cx = if x < 10 { 10 - x } else if x > 53 { x - 53 } else { 0 };
            let cy = if y < 10 { 10 - y } else if y > 53 { y - 53 } else { 0 };
            if cx * cx + cy * cy <= 81 {
                px[(y * size + x) as usize] |= 0xFF00_0000;
            }
        }
    }

    let hbm_mask = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &mut std::ptr::null_mut(), HANDLE::default(), 0)
        .unwrap_or_default();

    SelectObject(hdc, old_bm);
    let _ = DeleteDC(hdc);

    let ii = ICONINFO {
        fIcon: true.into(),
        xHotspot: 0,
        yHotspot: 0,
        hbmMask: hbm_mask,
        hbmColor: hbm_color,
    };
    let hicon = CreateIconIndirect(&ii).unwrap_or_default();
    let _ = DeleteObject(hbm_color);
    if !hbm_mask.is_invalid() {
        let _ = DeleteObject(hbm_mask);
    }
    hicon
}

impl AppUiState {
    fn s(&self, v: i32) -> i32 {
        ((v as f32) * self.dpi).round() as i32
    }

    unsafe fn free_backbuffer(&mut self) {
        if !self.back_bmp.is_invalid() && self.back_bmp.0 != std::ptr::null_mut() {
            let _ = DeleteObject(self.back_bmp);
            self.back_bmp = HBITMAP::default();
        }
        if !self.back_dc.is_invalid() && self.back_dc.0 != std::ptr::null_mut() {
            let _ = DeleteDC(self.back_dc);
            self.back_dc = HDC::default();
        }
        self.back_w = 0;
        self.back_h = 0;
        // Trim working set when hidden to tray so background footprint stays ~5-8 MB!
        let _ = K32EmptyWorkingSet(GetCurrentProcess());
    }

    unsafe fn ensure_backbuffer(&mut self, w: i32, h: i32) -> HDC {
        if w <= 0 || h <= 0 {
            return HDC::default();
        }
        if self.back_w == w && self.back_h == h && !self.back_dc.is_invalid() && self.back_dc.0 != std::ptr::null_mut() {
            return self.back_dc;
        }
        self.free_backbuffer();
        let hdc = CreateCompatibleDC(HDC::default());
        let bmi = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w,
                biHeight: -h,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        let mut bits: *mut std::ffi::c_void = std::ptr::null_mut();
        if let Ok(bmp) = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &mut bits, HANDLE::default(), 0) {
            SelectObject(hdc, HGDIOBJ(bmp.0));
            self.back_dc = hdc;
            self.back_bmp = bmp;
            self.back_w = w;
            self.back_h = h;
        }
        self.back_dc
    }

    unsafe fn update_tray_icons(&mut self, tel: Option<&SharedTelemetry>) {
        let cfg = self.bridge.config.get();
        let tgt = cfg.target_temp.round() as i32;

        // 1. CPU Tray Icon
        let (cpu_val_str, cpu_hdr_c, cpu_body_c, cpu_tip) = if let Some(t) = tel {
            let disp_c = t.cpu.avg_temp_c.or_else(|| t.ec_cpu_temp.map(|v| v as f64));
            let c_str = disp_c.map(|v| format!("{}", v.round() as i32)).unwrap_or_else(|| "--".to_string());
            let body = match disp_c {
                Some(v) if v < 60.0 => argb(255, 15, 118, 110),
                Some(v) if v < 75.0 => argb(255, 30, 58, 138),
                Some(v) if v < 85.0 => argb(255, 180, 83, 9),
                Some(_) => argb(255, 185, 28, 28),
                None => argb(255, 30, 41, 59),
            };
            let avg_s = disp_c.map(|v| format!("{:.1}°C", v)).unwrap_or_else(|| "--".into());
            let max_s = t.cpu.max_temp_c.map(|v| format!("{:.0}°C", v)).unwrap_or_else(|| "--".into());
            let f_s = format!(
                "{}/{} RPM",
                t.fan1_rpm.map(|v| v.to_string()).unwrap_or_else(|| "--".into()),
                t.fan2_rpm.map(|v| v.to_string()).unwrap_or_else(|| "--".into())
            );
            let tip = format!("CPU Core Ultra 7 155H\nСр: {} | Пик: {}\nКулеры: {}", avg_s, max_s, f_s);
            (c_str, argb(255, 2, 132, 199), body, tip)
        } else {
            ("--".to_string(), argb(255, 2, 132, 199), argb(255, 30, 41, 59), "CPU — ThinkBook Fan Control".to_string())
        };

        let cpu_key = format!("{}:{:X}", cpu_val_str, cpu_body_c);
        let cpu_icon_changed = cpu_key != self.last_cpu_badge_key;
        if cpu_icon_changed {
            let new_ic = create_labeled_tray_hicon("CPU", &cpu_val_str, cpu_hdr_c, cpu_body_c);
            if !self.tray_cpu_icon.is_invalid() {
                let _ = DestroyIcon(self.tray_cpu_icon);
            }
            self.tray_cpu_icon = new_ic;
            self.last_cpu_badge_key = cpu_key;
        }

        let mut nid_cpu = NOTIFYICONDATAW {
            cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: self.hwnd,
            uID: TRAY_ID_CPU,
            uFlags: NIF_MESSAGE | NIF_TIP | if !self.tray_cpu_added || cpu_icon_changed { NIF_ICON } else { Default::default() },
            uCallbackMessage: WM_APP_TRAY,
            hIcon: self.tray_cpu_icon,
            ..Default::default()
        };
        copy_wide_trunc(&mut nid_cpu.szTip, &cpu_tip);
        let _ = Shell_NotifyIconW(if self.tray_cpu_added { NIM_MODIFY } else { NIM_ADD }, &nid_cpu);
        self.tray_cpu_added = true;

        // 2. GPU Tray Icon
        let (gpu_val_str, gpu_hdr_c, gpu_body_c, gpu_tip) = if let Some(t) = tel {
            match t.dgpu_state {
                DgpuState::D3Cold => (
                    "OFF".to_string(),
                    argb(255, 5, 150, 105),
                    argb(255, 6, 78, 59),
                    "GPU RTX 4060: ОТКЛ. (Сон D3cold, 0.0 Вт)\nРаботает встроенная Intel Arc".to_string(),
                ),
                DgpuState::D0Idle => (
                    "IDLE".to_string(),
                    argb(255, 2, 132, 199),
                    argb(255, 51, 65, 85),
                    "GPU RTX 4060: Простой (NVML отпущен, ожидание сна)".to_string(),
                ),
                DgpuState::D0Active => {
                    let g_s = t.gpu_temp.map(|v| v.to_string()).unwrap_or_else(|| "--".into());
                    let body = match t.gpu_temp {
                        Some(v) if v < tgt - 4 => argb(255, 6, 95, 70),
                        Some(v) if v <= tgt + 1 => argb(255, 30, 58, 138),
                        Some(v) if v <= 75 => argb(255, 180, 83, 9),
                        Some(_) => argb(255, 185, 28, 28),
                        None => argb(255, 30, 41, 59),
                    };
                    let pw_s = t.gpu_pwr.map(|v| format!("{:.1} Вт", v)).unwrap_or_else(|| "--".into());
                    let ut_s = t.gpu_util.map(|v| format!("{}%", v)).unwrap_or_else(|| "--".into());
                    (
                        g_s.clone(),
                        argb(255, 124, 58, 237),
                        body,
                        format!("GPU RTX 4060: {}°C | {} | Нагрузка {}", g_s, pw_s, ut_s),
                    )
                }
            }
        } else {
            ("OFF".to_string(), argb(255, 5, 150, 105), argb(255, 6, 78, 59), "GPU — ThinkBook Fan Control".to_string())
        };

        let gpu_key = format!("{}:{:X}", gpu_val_str, gpu_body_c);
        let gpu_icon_changed = gpu_key != self.last_gpu_badge_key;
        if gpu_icon_changed {
            let new_ic = create_labeled_tray_hicon("GPU", &gpu_val_str, gpu_hdr_c, gpu_body_c);
            if !self.tray_gpu_icon.is_invalid() {
                let _ = DestroyIcon(self.tray_gpu_icon);
            }
            self.tray_gpu_icon = new_ic;
            self.last_gpu_badge_key = gpu_key;
        }

        let mut nid_gpu = NOTIFYICONDATAW {
            cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: self.hwnd,
            uID: TRAY_ID_GPU,
            uFlags: NIF_MESSAGE | NIF_TIP | if !self.tray_gpu_added || gpu_icon_changed { NIF_ICON } else { Default::default() },
            uCallbackMessage: WM_APP_TRAY,
            hIcon: self.tray_gpu_icon,
            ..Default::default()
        };
        copy_wide_trunc(&mut nid_gpu.szTip, &gpu_tip);
        let _ = Shell_NotifyIconW(if self.tray_gpu_added { NIM_MODIFY } else { NIM_ADD }, &nid_gpu);
        self.tray_gpu_added = true;
    }

    unsafe fn remove_tray_icons(&mut self) {
        for id in [TRAY_ID_CPU, TRAY_ID_GPU] {
            let nid = NOTIFYICONDATAW {
                cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
                hWnd: self.hwnd,
                uID: id,
                ..Default::default()
            };
            let _ = Shell_NotifyIconW(NIM_DELETE, &nid);
        }
        if !self.tray_cpu_icon.is_invalid() {
            let _ = DestroyIcon(self.tray_cpu_icon);
            self.tray_cpu_icon = HICON::default();
        }
        if !self.tray_gpu_icon.is_invalid() {
            let _ = DestroyIcon(self.tray_gpu_icon);
            self.tray_gpu_icon = HICON::default();
        }
    }

    unsafe fn render(&mut self, w: i32, h: i32) {
        let hdc = self.ensure_backbuffer(w, h);
        if hdc.is_invalid() || hdc.0 == std::ptr::null_mut() {
            return;
        }

        self.hitboxes.clear();
        self.curve_plot_rect = None;
        self.slider_rect = None;

        let cfg = self.bridge.config.get();
        let tel_opt = self.bridge.latest_telemetry.lock().ok().and_then(|g| g.clone());

        // Background #12141a
        let bg_br = CreateSolidBrush(rgb(18, 20, 26));
        let full = RECT { left: 0, top: 0, right: w, bottom: h };
        let _ = FillRect(hdc, &full, bg_br);
        let _ = DeleteObject(bg_br);

        let f_title = make_font(self.s(18), 700);
        let f_sub = make_font(self.s(12), 400);
        let f_card_h = make_font(self.s(11), 700);
        let f_card_v = make_font(self.s(22), 700);
        let f_btn = make_font(self.s(12), 600);
        let f_bold = make_font(self.s(13), 700);
        let f_small = make_font(self.s(10), 600);
        let f_tiny = make_font(self.s(9), 400);

        let pad = self.s(16);
        let mut y = self.s(12);

        // ---- 1. Header ----
        draw_label(
            hdc,
            RECT { left: pad, top: y, right: w / 2 + self.s(120), bottom: y + self.s(24) },
            "ThinkBook Fan Control — Native Rust Edition",
            f_title,
            rgb(248, 250, 252),
            DT_LEFT | DT_VCENTER | DT_SINGLELINE,
        );
        draw_label(
            hdc,
            RECT { left: pad, top: y + self.s(22), right: w / 2 + self.s(160), bottom: y + self.s(40) },
            "Lenovo 14 G6+ IMH • Core Ultra 7 155H (16 ядер) • RTX 4060 RTD3 • <8 МБ RAM",
            f_sub,
            rgb(148, 163, 184),
            DT_LEFT | DT_VCENTER | DT_SINGLELINE,
        );

        let (badge_txt, badge_bg) = match cfg.mode.as_str() {
            "curve" => ("📈 РЕЖИМ: КРИВАЯ".to_string(), rgb(99, 102, 241)),
            "manual" => ("🎛️ РЕЖИМ: РУЧНОЙ".to_string(), rgb(139, 92, 246)),
            "auto" => ("🍃 РЕЖИМ: BIOS (AUTO)".to_string(), rgb(16, 185, 129)),
            "boost" => ("🚀 РЕЖИМ: 100% ТУРБО".to_string(), rgb(239, 68, 68)),
            _ => (format!("⚡ РЕЖИМ: ЦЕЛЕВАЯ {:.0}°C", cfg.target_temp), rgb(2, 132, 199)),
        };
        let badge_w = self.s(195);
        let badge_r = RECT {
            left: w - pad - badge_w,
            top: y + self.s(4),
            right: w - pad,
            bottom: y + self.s(34),
        };
        draw_round_box(hdc, badge_r, self.s(12), badge_bg, badge_bg);
        draw_label(hdc, badge_r, &badge_txt, f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);

        let tgl_w = self.s(145);
        let tgl_r = RECT {
            left: badge_r.left - self.s(8) - tgl_w,
            top: y + self.s(4),
            right: badge_r.left - self.s(8),
            bottom: y + self.s(34),
        };
        draw_round_box(hdc, tgl_r, self.s(8), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(
            hdc,
            tgl_r,
            if self.show_cores_panel { "Скрыть ядра" } else { "Показать ядра" },
            f_btn,
            rgb(226, 232, 240),
            DT_CENTER | DT_VCENTER | DT_SINGLELINE,
        );
        self.hitboxes.push(HitBox { rect: tgl_r, action: UiAction::ToggleCores });

        y += self.s(48);

        // ---- 2. Five Summary Cards ----
        let gap = self.s(8);
        let card_w = (w - 2 * pad - 4 * gap) / 5;
        let card_h = self.s(74);

        let disp_cpu = tel_opt
            .as_ref()
            .and_then(|t| t.cpu.avg_temp_c.or_else(|| t.ec_cpu_temp.map(|v| v as f64)));
        let cpu_val_s = disp_cpu.map(|v| format!("{:.1} °C", v)).unwrap_or_else(|| "-- °C".into());
        let cpu_sub_s = if let Some(ref t) = tel_opt {
            let pk = t.cpu.max_temp_c.map(|v| format!("{:.0}°C", v)).unwrap_or_else(|| "--".into());
            let ec = t.ec_cpu_temp.map(|v| format!("{}°C", v)).unwrap_or_else(|| "--".into());
            format!("Пик: {} • EC: {}", pk, ec)
        } else {
            "Чтение...".into()
        };

        let (gpu_val_s, gpu_col, gpu_sub_s, pwr_val_s, pwr_col, pwr_sub_s) = if let Some(ref t) = tel_opt {
            let cpu_pw = t.cpu.power_w.map(|v| format!("CPU: {:.1} W", v)).unwrap_or_else(|| format!("CPU: {:.0}%", t.cpu.total_load_pct));
            match t.dgpu_state {
                DgpuState::D3Cold => (
                    "ОТКЛ.".to_string(),
                    rgb(16, 185, 129),
                    "Сон D3cold • iGPU".to_string(),
                    "0.0 W".to_string(),
                    rgb(16, 185, 129),
                    format!("dGPU Сон • {}", cpu_pw),
                ),
                DgpuState::D0Idle => (
                    "ПРОСТОЙ".to_string(),
                    rgb(56, 189, 248),
                    "NVML закрыт".to_string(),
                    "~1 W".to_string(),
                    rgb(148, 163, 184),
                    cpu_pw,
                ),
                DgpuState::D0Active => {
                    let gs = t.gpu_temp.map(|v| format!("{} °C", v)).unwrap_or_else(|| "-- °C".into());
                    let gc = temp_color_rgb(t.gpu_temp.map(|v| v as f64));
                    let gu = format!("Load: {}%", t.gpu_util.map(|v| v.to_string()).unwrap_or_else(|| "--".into()));
                    let ps = t.gpu_pwr.map(|v| format!("{:.1} W", v)).unwrap_or_else(|| "-- W".into());
                    (gs, gc, gu, ps, rgb(244, 63, 94), cpu_pw)
                }
            }
        } else {
            ("-- °C".into(), rgb(16, 185, 129), "Load: --%".into(), "-- W".into(), rgb(244, 63, 94), "CPU: --".into())
        };

        let f1_s = tel_opt.as_ref().and_then(|t| t.fan1_rpm).map(|v| format!("{} RPM", v)).unwrap_or_else(|| "-- RPM".into());
        let f2_s = tel_opt.as_ref().and_then(|t| t.fan2_rpm).map(|v| format!("{} RPM", v)).unwrap_or_else(|| "-- RPM".into());
        let tgt_s = tel_opt
            .as_ref()
            .map(|t| if t.target_rpm > 0 { format!("Target: {} RPM", t.target_rpm) } else { "Target: BIOS Auto".into() })
            .unwrap_or_else(|| "Target: --".into());

        let summary_cards = [
            ("CPU СРЕДНЯЯ", cpu_val_s, temp_color_rgb(disp_cpu), cpu_sub_s),
            ("GPU RTX 4060", gpu_val_s, gpu_col, gpu_sub_s),
            ("ПИТАНИЕ GPU / CPU", pwr_val_s, pwr_col, pwr_sub_s),
            ("FAN 1 (CPU)", f1_s, rgb(129, 140, 248), tgt_s.clone()),
            ("FAN 2 (GPU)", f2_s, rgb(167, 139, 250), tgt_s),
        ];

        for (i, (ctitle, cval, ccol, csub)) in summary_cards.iter().enumerate() {
            let x0 = pad + (i as i32) * (card_w + gap);
            let cr = RECT { left: x0, top: y, right: x0 + card_w, bottom: y + card_h };
            draw_round_box(hdc, cr, self.s(10), rgb(26, 29, 38), rgb(42, 46, 61));
            let in_pad = self.s(10);
            draw_label(hdc, RECT { left: x0 + in_pad, top: y + self.s(6), right: cr.right - in_pad, bottom: y + self.s(22) }, ctitle, f_card_h, rgb(148, 163, 184), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
            draw_label(hdc, RECT { left: x0 + in_pad, top: y + self.s(22), right: cr.right - in_pad, bottom: y + self.s(52) }, cval, f_card_v, *ccol, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
            draw_label(hdc, RECT { left: x0 + in_pad, top: y + self.s(52), right: cr.right - in_pad, bottom: cr.bottom - self.s(4) }, csub, f_small, rgb(100, 116, 139), DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        }

        y += card_h + self.s(10);

        // ---- 3. 16-Core CPU Matrix + GPU Details Panel ----
        if self.show_cores_panel {
            let panel_h = self.s(156);
            let gpu_panel_w = (w - 2 * pad - gap) * 27 / 100;
            let cpu_panel_w = (w - 2 * pad - gap) - gpu_panel_w;

            let cpu_r = RECT { left: pad, top: y, right: pad + cpu_panel_w, bottom: y + panel_h };
            let gpu_r = RECT { left: cpu_r.right + gap, top: y, right: w - pad, bottom: y + panel_h };

            draw_round_box(hdc, cpu_r, self.s(10), rgb(26, 29, 38), rgb(42, 46, 61));
            draw_round_box(hdc, gpu_r, self.s(10), rgb(26, 29, 38), rgb(42, 46, 61));

            // CPU Matrix Header + Average Pill
            draw_label(
                hdc,
                RECT { left: cpu_r.left + self.s(10), top: y + self.s(6), right: cpu_r.left + self.s(270), bottom: y + self.s(26) },
                "🧠 ЯДРА CPU (6P + 8E + 2LP)",
                f_btn,
                rgb(241, 245, 249),
                DT_LEFT | DT_VCENTER | DT_SINGLELINE,
            );

            let avg_pill_txt = disp_cpu
                .map(|v| format!("Средняя по CPU: {:.1} °C", v))
                .unwrap_or_else(|| "Средняя по CPU: -- °C".into());
            let avg_pill_r = RECT {
                left: cpu_r.right - self.s(330),
                top: y + self.s(5),
                right: cpu_r.right - self.s(155),
                bottom: y + self.s(26),
            };
            draw_round_box(hdc, avg_pill_r, self.s(8), rgb(12, 74, 110), rgb(2, 132, 199));
            draw_label(hdc, avg_pill_r, &avg_pill_txt, f_small, rgb(56, 189, 248), DT_CENTER | DT_VCENTER | DT_SINGLELINE);

            let load_pill_txt = if let Some(ref t) = tel_opt {
                if let Some(pw) = t.cpu.power_w {
                    format!("CPU {:.1}% • {:.1} W", t.cpu.total_load_pct, pw)
                } else {
                    format!("Нагрузка: {:.1}%", t.cpu.total_load_pct)
                }
            } else {
                "CPU: --%".into()
            };
            let ld_pill_r = RECT {
                left: cpu_r.right - self.s(148),
                top: y + self.s(5),
                right: cpu_r.right - self.s(10),
                bottom: y + self.s(26),
            };
            draw_round_box(hdc, ld_pill_r, self.s(8), rgb(30, 41, 59), rgb(51, 65, 85));
            draw_label(hdc, ld_pill_r, &load_pill_txt, f_small, rgb(203, 213, 225), DT_CENTER | DT_VCENTER | DT_SINGLELINE);

            // 8x2 Core Grid
            let gx0 = cpu_r.left + self.s(8);
            let gy0 = y + self.s(31);
            let g_gap = self.s(5);
            let tile_w = (cpu_panel_w - self.s(16) - 7 * g_gap) / 8;
            let tile_h = (panel_h - self.s(38) - g_gap) / 2;

            let cores_slice = tel_opt.as_ref().map(|t| t.cpu.cores.as_slice()).unwrap_or(&[]);
            for idx in 0..16 {
                let row = (idx / 8) as i32;
                let col = (idx % 8) as i32;
                let tx = gx0 + col * (tile_w + g_gap);
                let ty = gy0 + row * (tile_h + g_gap);
                let tr = RECT { left: tx, top: ty, right: tx + tile_w, bottom: ty + tile_h };
                draw_round_box(hdc, tr, self.s(6), rgb(20, 23, 32), rgb(37, 42, 58));

                if let Some(ci) = cores_slice.get(idx) {
                    let kcol = match ci.kind {
                        CoreKind::Performance => rgb(56, 189, 248),
                        CoreKind::Efficient => rgb(129, 140, 248),
                        CoreKind::LowPower => rgb(52, 211, 153),
                    };
                    draw_label(hdc, RECT { left: tx + self.s(5), top: ty + self.s(3), right: tx + tile_w / 2, bottom: ty + self.s(19) }, &ci.label, f_small, kcol, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
                    let ct_s = ci.temp_c.map(|v| format!("{:.0}°", v)).unwrap_or_else(|| "--°".into());
                    draw_label(hdc, RECT { left: tx + tile_w / 2 - self.s(4), top: ty + self.s(3), right: tr.right - self.s(5), bottom: ty + self.s(19) }, &ct_s, f_btn, temp_color_rgb(ci.temp_c), DT_RIGHT | DT_VCENTER | DT_SINGLELINE);

                    let meta_s = format!("{:.0}% • {:.1}G", ci.load_pct, (ci.mhz as f64) / 1000.0);
                    draw_label(hdc, RECT { left: tx + self.s(5), top: ty + self.s(20), right: tr.right - self.s(4), bottom: tr.bottom - self.s(9) }, &meta_s, f_tiny, rgb(100, 116, 139), DT_LEFT | DT_VCENTER | DT_SINGLELINE);

                    // Mini load bar
                    let bar_bg = RECT { left: tx + self.s(5), top: tr.bottom - self.s(7), right: tr.right - self.s(5), bottom: tr.bottom - self.s(3) };
                    let br_b = CreateSolidBrush(rgb(31, 36, 51));
                    let _ = FillRect(hdc, &bar_bg, br_b);
                    let _ = DeleteObject(br_b);

                    let fill_w = (((bar_bg.right - bar_bg.left) as f64) * (ci.load_pct / 100.0)).round() as i32;
                    if fill_w > 0 {
                        let bar_fg = RECT { left: bar_bg.left, top: bar_bg.top, right: bar_bg.left + fill_w, bottom: bar_bg.bottom };
                        let br_f = CreateSolidBrush(kcol);
                        let _ = FillRect(hdc, &bar_fg, br_f);
                        let _ = DeleteObject(br_f);
                    }
                }
            }

            // Right GPU Detail Panel
            draw_label(
                hdc,
                RECT { left: gpu_r.left + self.s(10), top: y + self.s(6), right: gpu_r.right - self.s(10), bottom: y + self.s(26) },
                "🎮 ВИДЕОКАРТА (Optimus)",
                f_btn,
                rgb(241, 245, 249),
                DT_LEFT | DT_VCENTER | DT_SINGLELINE,
            );

            let (g_st, g_t, g_p, g_u) = if let Some(ref t) = tel_opt {
                match t.dgpu_state {
                    DgpuState::D3Cold => ("Сон D3cold (0 Вт)".into(), "ОТКЛ. (Обесточена)".into(), "0.0 W".into(), "0% / 0%".into()),
                    DgpuState::D0Idle => ("Простой (ждёт D3)".into(), "NVML отпущен".into(), "~1.0 W".into(), "0% / 0%".into()),
                    DgpuState::D0Active => (
                        "RTX 4060 Активна".into(),
                        t.gpu_temp.map(|v| format!("{} °C", v)).unwrap_or_else(|| "--".into()),
                        t.gpu_pwr.map(|v| format!("{:.1} W", v)).unwrap_or_else(|| "--".into()),
                        format!("{}% / {}%", t.gpu_util.unwrap_or(0), t.mem_util.unwrap_or(0)),
                    ),
                }
            } else {
                ("Инициализация".into(), "--".into(), "--".into(), "--".into())
            };

            let rows = [
                ("Статус dGPU:", g_st),
                ("Температура:", g_t),
                ("Потребление:", g_p),
                ("Ядро / VRAM:", g_u),
                ("Экран (iGPU):", "Intel Arc • Активна".into()),
            ];
            for (ri, (rk, rv)) in rows.iter().enumerate() {
                let ry = y + self.s(32) + (ri as i32) * self.s(23);
                draw_label(hdc, RECT { left: gpu_r.left + self.s(10), top: ry, right: gpu_r.left + self.s(110), bottom: ry + self.s(20) }, rk, f_small, rgb(148, 163, 184), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
                draw_label(hdc, RECT { left: gpu_r.left + self.s(105), top: ry, right: gpu_r.right - self.s(10), bottom: ry + self.s(20) }, rv, f_small, rgb(241, 245, 249), DT_RIGHT | DT_VCENTER | DT_SINGLELINE);
            }

            y += panel_h + self.s(10);
        }

        // ---- 4. Mode Selector Tabs ----
        let mode_names = [
            (0usize, "target_65", "🎯 Авто-цель °C (Тихий ПИД)"),
            (1usize, "curve", "📈 Кривая вентиляторов"),
            (2usize, "manual", "🎛️ Ручной режим (RPM)"),
            (3usize, "auto", "🍃 Штатный BIOS (Auto)"),
            (4usize, "boost", "🚀 100% Турбо (Boost)"),
        ];
        let tab_w = (w - 2 * pad - 4 * gap) / 5;
        let tab_h = self.s(34);
        for (mi, mkey, mlabel) in mode_names {
            let tx = pad + (mi as i32) * (tab_w + gap);
            let tr = RECT { left: tx, top: y, right: tx + tab_w, bottom: y + tab_h };
            let active = cfg.mode == mkey;
            let fill = if active { rgb(2, 132, 199) } else { rgb(30, 34, 48) };
            let bord = if active { rgb(56, 189, 248) } else { rgb(45, 51, 72) };
            let tcol = if active { rgb(255, 255, 255) } else { rgb(148, 163, 184) };
            draw_round_box(hdc, tr, self.s(8), fill, bord);
            draw_label(hdc, tr, mlabel, f_btn, tcol, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            self.hitboxes.push(HitBox { rect: tr, action: UiAction::SelectMode(mi) });
        }

        y += tab_h + self.s(10);

        // ---- 5. Active Mode Content Area ----
        let bottom_bar_h = self.s(38);
        let content_r = RECT { left: pad, top: y, right: w - pad, bottom: h - pad - bottom_bar_h - self.s(8) };
        draw_round_box(hdc, content_r, self.s(10), rgb(26, 29, 38), rgb(42, 46, 61));

        match cfg.mode.as_str() {
            "curve" => self.render_curve_page(hdc, content_r, &cfg.curve_points, &cfg.curve_sensor, cfg.target_temp, tel_opt.as_ref(), f_btn, f_small),
            "manual" => self.render_manual_page(hdc, content_r, cfg.manual_rpm, f_bold, f_btn),
            "auto" => self.render_auto_page(hdc, content_r, f_bold, f_sub, f_btn),
            "boost" => self.render_boost_page(hdc, content_r, tel_opt.as_ref(), f_bold, f_sub, f_btn),
            _ => self.render_target_page(hdc, content_r, cfg.target_temp, tel_opt.as_ref(), f_bold, f_sub, f_btn),
        }

        // ---- 6. Bottom Toolbar ----
        let by = h - pad - bottom_bar_h;
        let checks = [
            (cfg.minimize_to_tray_on_close, "В трей при закрытии", UiAction::ToggleMinToTray, self.s(165)),
            (cfg.autostart, "Автозапуск с Windows", UiAction::ToggleAutostart, self.s(175)),
            (cfg.safety_override, "Защита от перегрева (>80°C)", UiAction::ToggleSafety, self.s(210)),
        ];
        let mut cx = pad;
        for (checked, clabel, cact, cw) in checks {
            let box_r = RECT { left: cx, top: by + self.s(10), right: cx + self.s(18), bottom: by + self.s(28) };
            let cfill = if checked { rgb(2, 132, 199) } else { rgb(30, 34, 48) };
            let cbord = if checked { rgb(56, 189, 248) } else { rgb(59, 66, 86) };
            draw_round_box(hdc, box_r, self.s(4), cfill, cbord);
            if checked {
                draw_label(hdc, box_r, "✓", f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            }
            let lbl_r = RECT { left: box_r.right + self.s(6), top: by, right: cx + cw, bottom: by + bottom_bar_h };
            draw_label(hdc, lbl_r, clabel, f_sub, rgb(148, 163, 184), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
            self.hitboxes.push(HitBox { rect: RECT { left: cx, top: by + self.s(4), right: cx + cw, bottom: by + bottom_bar_h - self.s(4) }, action: cact });
            cx += cw + self.s(12);
        }

        let exit_w = self.s(180);
        let exit_r = RECT { left: w - pad - exit_w, top: by + self.s(3), right: w - pad, bottom: by + bottom_bar_h - self.s(3) };
        draw_round_box(hdc, exit_r, self.s(6), rgb(153, 27, 27), rgb(239, 68, 68));
        draw_label(hdc, exit_r, "Выход (Сброс на BIOS)", f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: exit_r, action: UiAction::SafeExit });

        let min_w = self.s(135);
        let min_r = RECT { left: exit_r.left - self.s(8) - min_w, top: by + self.s(3), right: exit_r.left - self.s(8), bottom: by + bottom_bar_h - self.s(3) };
        draw_round_box(hdc, min_r, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, min_r, "Свернуть в трей", f_btn, rgb(226, 232, 240), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: min_r, action: UiAction::MinimizeToTray });

        for f in [f_title, f_sub, f_card_h, f_card_v, f_btn, f_bold, f_small, f_tiny] {
            let _ = DeleteObject(f);
        }
    }

    unsafe fn render_target_page(
        &mut self,
        hdc: HDC,
        r: RECT,
        target: f64,
        tel: Option<&SharedTelemetry>,
        f_bold: HFONT,
        f_sub: HFONT,
        f_btn: HFONT,
    ) {
        let p = self.s(16);
        draw_label(
            hdc,
            RECT { left: r.left + p, top: r.top + self.s(12), right: r.right - p, bottom: r.top + self.s(34) },
            "🎯 Нативное плавное удержание целевой температуры (Адаптивный I-PD)",
            f_bold,
            rgb(56, 189, 248),
            DT_LEFT | DT_VCENTER | DT_SINGLELINE,
        );
        draw_label(
            hdc,
            RECT { left: r.left + p, top: r.top + self.s(36), right: r.right - p, bottom: r.top + self.s(76) },
            "Регулятор контролирует инерцию общей теплотрубки GPU RTX 4060 и CPU Core Ultra. В простое держит тихие ~2100 RPM и отпускает RTX 4060 в глубокий сон D3cold (0 Вт), а под нагрузкой удерживает цель с 0 RPM дребезга.",
            f_sub,
            rgb(148, 163, 184),
            DT_LEFT | DT_WORDBREAK,
        );

        let cy = r.top + self.s(86);
        draw_label(hdc, RECT { left: r.left + p, top: cy, right: r.left + self.s(210), bottom: cy + self.s(34) }, "Целевая температура:", f_bold, rgb(241, 245, 249), DT_LEFT | DT_VCENTER | DT_SINGLELINE);

        let btn_minus = RECT { left: r.left + self.s(215), top: cy, right: r.left + self.s(250), bottom: cy + self.s(34) };
        let val_box = RECT { left: btn_minus.right + self.s(6), top: cy, right: btn_minus.right + self.s(96), bottom: cy + self.s(34) };
        let btn_plus = RECT { left: val_box.right + self.s(6), top: cy, right: val_box.right + self.s(41), bottom: cy + self.s(34) };

        draw_round_box(hdc, btn_minus, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, btn_minus, "−", f_bold, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: btn_minus, action: UiAction::TargetDelta(-1) });

        draw_round_box(hdc, val_box, self.s(6), rgb(20, 23, 32), rgb(56, 189, 248));
        draw_label(hdc, val_box, &format!("{:.0} °C", target), f_bold, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);

        draw_round_box(hdc, btn_plus, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, btn_plus, "+", f_bold, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: btn_plus, action: UiAction::TargetDelta(1) });

        let mut px = btn_plus.right + self.s(20);
        for &pt in &[60i32, 65, 68, 72] {
            let pw = if pt == 65 { self.s(125) } else { self.s(72) };
            let pr = RECT { left: px, top: cy, right: px + pw, bottom: cy + self.s(34) };
            let act = (target.round() as i32) == pt;
            draw_round_box(hdc, pr, self.s(6), if act { rgb(2, 132, 199) } else { rgb(36, 41, 56) }, rgb(51, 58, 79));
            let lbl = if pt == 65 { "65°C (Оптимум)".into() } else { format!("{}°C", pt) };
            draw_label(hdc, pr, &lbl, f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            self.hitboxes.push(HitBox { rect: pr, action: UiAction::TargetPreset(pt) });
            px += pw + self.s(8);
        }

        let st_r = RECT { left: r.left + p, top: cy + self.s(48), right: r.right - p, bottom: cy + self.s(86) };
        let desc = tel.map(|t| t.mode_desc.as_str()).unwrap_or("Инициализация датчиков...");
        let wmi_ok = tel.map(|t| t.wmi_ok).unwrap_or(true);
        draw_round_box(hdc, st_r, self.s(6), if wmi_ok { rgb(30, 41, 59) } else { rgb(42, 18, 21) }, if wmi_ok { rgb(16, 185, 129) } else { rgb(244, 63, 94) });
        draw_label(
            hdc,
            RECT { left: st_r.left + self.s(12), top: st_r.top, right: st_r.right - self.s(12), bottom: st_r.bottom },
            &format!("Регулятор активен ➔ {}", desc),
            f_btn,
            if wmi_ok { rgb(16, 185, 129) } else { rgb(244, 63, 94) },
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
        );
    }

    unsafe fn render_curve_page(
        &mut self,
        hdc: HDC,
        r: RECT,
        pts: &[CurvePoint],
        sensor: &str,
        target_ind: f64,
        tel: Option<&SharedTelemetry>,
        f_btn: HFONT,
        f_small: HFONT,
    ) {
        let p = self.s(12);
        let top_y = r.top + self.s(8);

        // Sensor selector pills + presets on top bar
        let sensors: [(&'static str, &str, i32); 3] = [
            ("max", "Макс: GPU/CPU", self.s(112)),
            ("gpu", "Только GPU", self.s(92)),
            ("cpu", "Только CPU", self.s(92)),
        ];
        let mut sx = r.left + p;
        for (skey, slbl, sw) in sensors {
            let sr = RECT { left: sx, top: top_y, right: sx + sw, bottom: top_y + self.s(26) };
            let act = sensor == skey;
            draw_round_box(hdc, sr, self.s(6), if act { rgb(2, 132, 199) } else { rgb(36, 41, 56) }, rgb(51, 58, 79));
            draw_label(hdc, sr, slbl, f_small, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            self.hitboxes.push(HitBox { rect: sr, action: UiAction::CurveSensor(skey) });
            sx += sw + self.s(5);
        }

        sx += self.s(10);
        for (pi, (pname, _)) in curve_presets().iter().enumerate() {
            let pw = self.s(82);
            let pr = RECT { left: sx, top: top_y, right: sx + pw, bottom: top_y + self.s(26) };
            draw_round_box(hdc, pr, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
            draw_label(hdc, pr, pname, f_small, rgb(226, 232, 240), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            self.hitboxes.push(HitBox { rect: pr, action: UiAction::CurvePreset(pi) });
            sx += pw + self.s(5);
        }

        let rst_r = RECT { left: r.right - p - self.s(76), top: top_y, right: r.right - p, bottom: top_y + self.s(26) };
        draw_round_box(hdc, rst_r, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, rst_r, "Сбросить", f_small, rgb(226, 232, 240), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: rst_r, action: UiAction::CurveReset });

        let add_r = RECT { left: rst_r.left - self.s(5) - self.s(74), top: top_y, right: rst_r.left - self.s(5), bottom: top_y + self.s(26) };
        draw_round_box(hdc, add_r, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, add_r, "+ Точка", f_small, rgb(226, 232, 240), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: add_r, action: UiAction::CurveAddPoint });

        // Plot Area
        let plot = RECT {
            left: r.left + self.s(54),
            top: top_y + self.s(34),
            right: r.right - self.s(18),
            bottom: r.bottom - self.s(24),
        };
        if plot.bottom - plot.top < 40 || plot.right - plot.left < 100 {
            return;
        }
        self.curve_plot_rect = Some(plot);

        let br_plot = CreateSolidBrush(rgb(20, 23, 32));
        let _ = FillRect(hdc, &plot, br_plot);
        let _ = DeleteObject(br_plot);

        let pw = (plot.right - plot.left) as f64;
        let ph = (plot.bottom - plot.top) as f64;
        let to_px = |temp: f64, rpm: f64| -> (i32, i32) {
            let nx = ((temp - 30.0) / 60.0).clamp(0.0, 1.0);
            let ny = ((rpm - 2000.0) / 5500.0).clamp(0.0, 1.0);
            (
                plot.left + (nx * pw).round() as i32,
                plot.top + ((1.0 - ny) * ph).round() as i32,
            )
        };

        let pen_grid = CreatePen(PS_DASH, 1, rgb(45, 52, 70));
        let old_pen = SelectObject(hdc, HGDIOBJ(pen_grid.0));
        for rpm_v in (2000..=7500).step_by(1000) {
            let (_, py) = to_px(30.0, rpm_v as f64);
            let _ = MoveToEx(hdc, plot.left, py, None);
            let _ = LineTo(hdc, plot.right, py);
            draw_label(hdc, RECT { left: r.left + self.s(4), top: py - self.s(8), right: plot.left - self.s(6), bottom: py + self.s(8) }, &format!("{}", rpm_v), f_small, rgb(148, 163, 184), DT_RIGHT | DT_VCENTER | DT_SINGLELINE);
        }
        for t_v in (30..=90).step_by(10) {
            let (px, _) = to_px(t_v as f64, 2000.0);
            let _ = MoveToEx(hdc, px, plot.top, None);
            let _ = LineTo(hdc, px, plot.bottom);
            draw_label(hdc, RECT { left: px - self.s(22), top: plot.bottom + self.s(3), right: px + self.s(22), bottom: r.bottom }, &format!("{}°C", t_v), f_small, rgb(148, 163, 184), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        }
        let (tx_ind, _) = to_px(target_ind, 2000.0);
        let pen_tgt = CreatePen(PS_SOLID, 1, rgb(245, 158, 11));
        SelectObject(hdc, HGDIOBJ(pen_tgt.0));
        let _ = MoveToEx(hdc, tx_ind, plot.top, None);
        let _ = LineTo(hdc, tx_ind, plot.bottom);
        SelectObject(hdc, old_pen);
        let _ = DeleteObject(pen_grid);
        let _ = DeleteObject(pen_tgt);

        // Antialiased curve + gradient via GDI+ (CPU rasterizer — zero GPU wake!)
        let mut gp: *mut GpGraphics = std::ptr::null_mut();
        if GdipCreateFromHDC(hdc, &mut gp).0 == 0 && !gp.is_null() {
            let _ = GdipSetSmoothingMode(gp, SmoothingModeAntiAlias);

            if !pts.is_empty() {
                let (fx, fy) = to_px(pts[0].temp as f64, pts[0].rpm as f64);
                let (lx, ly) = to_px(pts.last().unwrap().temp as f64, pts.last().unwrap().rpm as f64);

                let mut poly = Vec::with_capacity(pts.len() + 4);
                poly.push(GpPoint { X: plot.left, Y: fy });
                for pt in pts {
                    let (x, y_p) = to_px(pt.temp as f64, pt.rpm as f64);
                    poly.push(GpPoint { X: x, Y: y_p });
                }
                poly.push(GpPoint { X: plot.right, Y: ly });
                poly.push(GpPoint { X: plot.right, Y: plot.bottom });
                poly.push(GpPoint { X: plot.left, Y: plot.bottom });

                let mut grad: *mut GpLineGradient = std::ptr::null_mut();
                if GdipCreateLineBrushI(
                    &GpPoint { X: plot.left, Y: plot.top },
                    &GpPoint { X: plot.left, Y: plot.bottom },
                    argb(110, 0, 180, 216),
                    argb(12, 0, 119, 182),
                    WrapModeTileFlipX,
                    &mut grad,
                ).0 == 0
                {
                    let _ = GdipFillPolygonI(gp, grad as *mut GpBrush, poly.as_ptr(), poly.len() as i32, windows::Win32::Graphics::GdiPlus::FillModeAlternate);
                    let _ = GdipDeleteBrush(grad as *mut GpBrush);
                }

                let mut pen_c: *mut GpPen = std::ptr::null_mut();
                if GdipCreatePen1(argb(255, 0, 180, 216), (2.5 * self.dpi).max(2.0), UnitPixel, &mut pen_c).0 == 0 {
                    let _ = GdipDrawLineI(gp, pen_c, plot.left, fy, fx, fy);
                    for w_pt in pts.windows(2) {
                        let (x0, y0) = to_px(w_pt[0].temp as f64, w_pt[0].rpm as f64);
                        let (x1, y1) = to_px(w_pt[1].temp as f64, w_pt[1].rpm as f64);
                        let _ = GdipDrawLineI(gp, pen_c, x0, y0, x1, y1);
                    }
                    let _ = GdipDrawLineI(gp, pen_c, lx, ly, plot.right, ly);
                    let _ = GdipDeletePen(pen_c);
                }

                let mut br_pt: *mut GpSolidFill = std::ptr::null_mut();
                let mut br_w: *mut GpSolidFill = std::ptr::null_mut();
                let _ = GdipCreateSolidFill(argb(255, 56, 189, 248), &mut br_pt);
                let _ = GdipCreateSolidFill(argb(255, 255, 255, 255), &mut br_w);
                for (idx, pt) in pts.iter().enumerate() {
                    let (x, y_p) = to_px(pt.temp as f64, pt.rpm as f64);
                    let rad = if self.dragging_curve_pt == Some(idx) || self.hover_curve_pt == Some(idx) {
                        self.s(7)
                    } else {
                        self.s(5)
                    };
                    let _ = GdipFillEllipseI(gp, br_w as *mut GpBrush, x - rad - 2, y_p - rad - 2, (rad + 2) * 2, (rad + 2) * 2);
                    let _ = GdipFillEllipseI(gp, br_pt as *mut GpBrush, x - rad, y_p - rad, rad * 2, rad * 2);
                }
                let _ = GdipDeleteBrush(br_pt as *mut GpBrush);
                let _ = GdipDeleteBrush(br_w as *mut GpBrush);
            }

            // Live green operating dot
            if let Some(t) = tel {
                let l_rpm = t.fan2_rpm.or(t.fan1_rpm).unwrap_or(t.target_rpm) as f64;
                let (lx, ly) = to_px(t.curve_ref_temp, l_rpm);
                let mut br_g: *mut GpSolidFill = std::ptr::null_mut();
                if GdipCreateSolidFill(argb(255, 56, 176, 0), &mut br_g).0 == 0 {
                    let rad = self.s(6);
                    let _ = GdipFillEllipseI(gp, br_g as *mut GpBrush, lx - rad, ly - rad, rad * 2, rad * 2);
                    let _ = GdipDeleteBrush(br_g as *mut GpBrush);
                }
                draw_label(
                    hdc,
                    RECT { left: plot.left + self.s(8), top: plot.top + self.s(4), right: plot.left + self.s(260), bottom: plot.top + self.s(22) },
                    &format!("Рабочая точка: {:.1}°C @ {:.0} RPM", t.curve_ref_temp, l_rpm),
                    f_btn,
                    rgb(74, 222, 128),
                    DT_LEFT | DT_VCENTER | DT_SINGLELINE,
                );
            }

            let _ = GdipDeleteGraphics(gp);
        }
    }

    unsafe fn render_manual_page(&mut self, hdc: HDC, r: RECT, manual_rpm: i32, f_bold: HFONT, f_btn: HFONT) {
        let p = self.s(16);
        draw_label(hdc, RECT { left: r.left + p, top: r.top + self.s(14), right: r.right - p, bottom: r.top + self.s(38) }, "🎛️ Ручная установка фиксированных оборотов (RPM)", f_bold, rgb(248, 250, 252), DT_LEFT | DT_VCENTER | DT_SINGLELINE);

        let sy = r.top + self.s(54);
        let sl_r = RECT { left: r.left + p, top: sy, right: r.right - p - self.s(190), bottom: sy + self.s(32) };
        self.slider_rect = Some(sl_r);

        let groove_y = (sl_r.top + sl_r.bottom) / 2;
        let groove = RECT { left: sl_r.left, top: groove_y - self.s(4), right: sl_r.right, bottom: groove_y + self.s(4) };
        draw_round_box(hdc, groove, self.s(4), rgb(36, 41, 56), rgb(51, 58, 79));

        let ratio = ((manual_rpm - 2000) as f64 / 5500.0).clamp(0.0, 1.0);
        let knob_x = sl_r.left + (((sl_r.right - sl_r.left) as f64) * ratio).round() as i32;
        let sub_g = RECT { left: sl_r.left, top: groove.top, right: knob_x.max(sl_r.left + 4), bottom: groove.bottom };
        draw_round_box(hdc, sub_g, self.s(4), rgb(2, 132, 199), rgb(2, 132, 199));

        let knob = RECT { left: knob_x - self.s(9), top: groove_y - self.s(9), right: knob_x + self.s(9), bottom: groove_y + self.s(9) };
        draw_round_box(hdc, knob, self.s(9), rgb(56, 189, 248), rgb(255, 255, 255));

        let bm = RECT { left: sl_r.right + self.s(10), top: sy, right: sl_r.right + self.s(44), bottom: sy + self.s(32) };
        let vb = RECT { left: bm.right + self.s(5), top: sy, right: bm.right + self.s(105), bottom: sy + self.s(32) };
        let bp = RECT { left: vb.right + self.s(5), top: sy, right: vb.right + self.s(39), bottom: sy + self.s(32) };

        draw_round_box(hdc, bm, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, bm, "−", f_bold, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: bm, action: UiAction::ManualDelta(-50) });

        draw_round_box(hdc, vb, self.s(6), rgb(20, 23, 32), rgb(56, 189, 248));
        draw_label(hdc, vb, &format!("{} RPM", manual_rpm), f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);

        draw_round_box(hdc, bp, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, bp, "+", f_bold, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: bp, action: UiAction::ManualDelta(50) });

        let qy = sy + self.s(48);
        let mut qx = r.left + p;
        for &rv in &[2100i32, 2600, 3200, 3800, 4500, 5500, 6500, 7500] {
            let qw = self.s(92);
            let qr = RECT { left: qx, top: qy, right: qx + qw, bottom: qy + self.s(32) };
            let act = manual_rpm == rv;
            draw_round_box(hdc, qr, self.s(6), if act { rgb(2, 132, 199) } else { rgb(36, 41, 56) }, rgb(51, 58, 79));
            draw_label(hdc, qr, &format!("{} RPM", rv), f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            self.hitboxes.push(HitBox { rect: qr, action: UiAction::ManualPreset(rv) });
            qx += qw + self.s(7);
        }
    }

    unsafe fn render_auto_page(&mut self, hdc: HDC, r: RECT, f_bold: HFONT, f_sub: HFONT, f_btn: HFONT) {
        let p = self.s(16);
        draw_label(hdc, RECT { left: r.left + p, top: r.top + self.s(14), right: r.right - p, bottom: r.top + self.s(38) }, "🍃 Штатный автоматический режим Lenovo BIOS / EC", f_bold, rgb(16, 185, 129), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        draw_label(hdc, RECT { left: r.left + p, top: r.top + self.s(42), right: r.right - p, bottom: r.top + self.s(82) }, "Управление вентиляторами полностью передаётся заводской прошивке встроенного контроллера Lenovo EC без лишних цикличных перезаписей WMI.", f_sub, rgb(148, 163, 184), DT_LEFT | DT_WORDBREAK);
        let br = RECT { left: r.left + p, top: r.top + self.s(90), right: r.left + p + self.s(260), bottom: r.top + self.s(126) };
        draw_round_box(hdc, br, self.s(6), rgb(2, 132, 199), rgb(56, 189, 248));
        draw_label(hdc, br, "Подтвердить сброс на BIOS", f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: br, action: UiAction::ConfirmBiosAuto });
    }

    unsafe fn render_boost_page(&mut self, hdc: HDC, r: RECT, tel: Option<&SharedTelemetry>, f_bold: HFONT, f_sub: HFONT, f_btn: HFONT) {
        let p = self.s(16);
        draw_label(hdc, RECT { left: r.left + p, top: r.top + self.s(14), right: r.right - p, bottom: r.top + self.s(38) }, "🚀 Режим максимального охлаждения (100% Турбо ~7300–7700 RPM)", f_bold, rgb(244, 63, 94), DT_LEFT | DT_VCENTER | DT_SINGLELINE);
        draw_label(hdc, RECT { left: r.left + p, top: r.top + self.s(40), right: r.right - p, bottom: r.top + self.s(72) }, "Включает непрерывную продувку на физическом максимуме кулеров с защитой от сторожевого таймера EC. При возврате обороты снижаются плавно без удара.", f_sub, rgb(203, 213, 225), DT_LEFT | DT_WORDBREAK);

        let st_r = RECT { left: r.left + p, top: r.top + self.s(78), right: r.right - p, bottom: r.top + self.s(114) };
        draw_round_box(hdc, st_r, self.s(6), rgb(42, 18, 21), rgb(127, 29, 29));
        let st_txt = if let Some(t) = tel {
            format!("Статус: 🚀 100% ТУРБО АКТИВЕН ➔ Fan 1: {} RPM | Fan 2: {} RPM", t.fan1_rpm.unwrap_or(0), t.fan2_rpm.unwrap_or(0))
        } else {
            "Статус: 🚀 100% ТУРБО".into()
        };
        draw_label(hdc, RECT { left: st_r.left + self.s(12), top: st_r.top, right: st_r.right - self.s(12), bottom: st_r.bottom }, &st_txt, f_btn, rgb(244, 63, 94), DT_LEFT | DT_VCENTER | DT_SINGLELINE);

        let by = r.top + self.s(124);
        let b1 = RECT { left: r.left + p, top: by, right: r.left + p + self.s(220), bottom: by + self.s(36) };
        let b2 = RECT { left: b1.right + self.s(10), top: by, right: b1.right + self.s(220), bottom: by + self.s(36) };
        let b3 = RECT { left: b2.right + self.s(10), top: by, right: b2.right + self.s(200), bottom: by + self.s(36) };

        draw_round_box(hdc, b1, self.s(6), rgb(190, 18, 60), rgb(244, 63, 94));
        draw_label(hdc, b1, "🔥 АКТИВИРОВАТЬ 100% ТУРБО", f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: b1, action: UiAction::ActivateBoost });

        draw_round_box(hdc, b2, self.s(6), rgb(2, 132, 199), rgb(56, 189, 248));
        draw_label(hdc, b2, "🎯 Вернуться к Авто-цели", f_btn, rgb(255, 255, 255), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: b2, action: UiAction::ReturnTargetMode });

        draw_round_box(hdc, b3, self.s(6), rgb(36, 41, 56), rgb(51, 58, 79));
        draw_label(hdc, b3, "🍃 Сброс на BIOS (Auto)", f_btn, rgb(226, 232, 240), DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        self.hitboxes.push(HitBox { rect: b3, action: UiAction::ConfirmBiosAuto });
    }

    fn apply_action(&mut self, act: UiAction) {
        match act {
            UiAction::ToggleCores => {
                self.show_cores_panel = !self.show_cores_panel;
            }
            UiAction::SelectMode(idx) => {
                let m = match idx {
                    1 => "curve",
                    2 => "manual",
                    3 => "auto",
                    4 => "boost",
                    _ => "target_65",
                };
                self.bridge.config.update(|c| c.mode = m.to_string(), true);
                self.bridge.notify_wake();
            }
            UiAction::TargetDelta(d) => {
                self.bridge.config.update(|c| c.target_temp = (c.target_temp + d as f64).clamp(50.0, 80.0), true);
                self.bridge.notify_wake();
            }
            UiAction::TargetPreset(t) => {
                self.bridge.config.update(|c| c.target_temp = t as f64, true);
                self.bridge.notify_wake();
            }
            UiAction::CurveSensor(s) => {
                self.bridge.config.update(|c| c.curve_sensor = s.to_string(), true);
                self.bridge.notify_wake();
            }
            UiAction::CurvePreset(pi) => {
                let presets = curve_presets();
                if let Some((_, pts)) = presets.get(pi) {
                    let cloned = pts.clone();
                    self.bridge.config.update(|c| c.curve_points = cloned, true);
                    self.bridge.notify_wake();
                }
            }
            UiAction::CurveAddPoint => {
                self.bridge.config.update(|c| {
                    let mut max_gap = 0;
                    let mut ins = 0;
                    for i in 0..c.curve_points.len().saturating_sub(1) {
                        let g = c.curve_points[i + 1].temp - c.curve_points[i].temp;
                        if g > max_gap {
                            max_gap = g;
                            ins = i;
                        }
                    }
                    if max_gap >= 4 {
                        let p0 = &c.curve_points[ins];
                        let p1 = &c.curve_points[ins + 1];
                        let nt = (p0.temp + p1.temp) / 2;
                        let nr = ((p0.rpm + p1.rpm) / 2 / 50) * 50;
                        c.curve_points.insert(ins + 1, CurvePoint { temp: nt, rpm: nr });
                    }
                }, true);
                self.bridge.notify_wake();
            }
            UiAction::CurveReset => {
                self.bridge.config.update(|c| c.curve_points = default_curve(), true);
                self.bridge.notify_wake();
            }
            UiAction::ManualDelta(d) => {
                self.bridge.config.update(|c| c.manual_rpm = (c.manual_rpm + d).clamp(2000, 7500), true);
                self.bridge.notify_wake();
            }
            UiAction::ManualPreset(rv) => {
                self.bridge.config.update(|c| c.manual_rpm = rv, true);
                self.bridge.notify_wake();
            }
            UiAction::ConfirmBiosAuto => {
                self.bridge.config.update(|c| c.mode = "auto".into(), true);
                self.bridge.notify_wake();
            }
            UiAction::ActivateBoost => {
                self.bridge.config.update(|c| c.mode = "boost".into(), true);
                self.bridge.notify_wake();
            }
            UiAction::ReturnTargetMode => {
                self.bridge.config.update(|c| c.mode = "target_65".into(), true);
                self.bridge.notify_wake();
            }
            UiAction::ToggleMinToTray => {
                self.bridge.config.update(|c| c.minimize_to_tray_on_close = !c.minimize_to_tray_on_close, true);
            }
            UiAction::ToggleAutostart => {
                let cur = self.bridge.config.get().autostart;
                self.bridge.config.set_autostart(!cur);
            }
            UiAction::ToggleSafety => {
                self.bridge.config.update(|c| c.safety_override = !c.safety_override, true);
                self.bridge.notify_wake();
            }
            UiAction::MinimizeToTray => unsafe {
                let _ = ShowWindow(self.hwnd, SW_HIDE);
                self.free_backbuffer();
            },
            UiAction::SafeExit => unsafe {
                self.safe_quit();
            },
        }
        unsafe {
            let _ = InvalidateRect(self.hwnd, None, false);
        }
    }

    unsafe fn safe_quit(&mut self) {
        self.bridge.config.save();
        self.bridge.running.store(false, std::sync::atomic::Ordering::SeqCst);
        self.bridge.notify_wake();
        self.remove_tray_icons();
        self.free_backbuffer();
        let _ = DestroyWindow(self.hwnd);
        PostQuitMessage(0);
    }

    unsafe fn show_tray_popup_menu(&mut self) {
        let Ok(menu) = CreatePopupMenu() else { return };
        let items: [(usize, &str); 7] = [
            (10, "Показать окно (Ядра CPU и GPU)"),
            (20, "🎯 Авто-цель °C (Тихий ПИД)"),
            (21, "📈 Кривая вентиляторов"),
            (22, "🎛️ Ручной режим (RPM)"),
            (23, "🍃 Штатный BIOS (Auto)"),
            (24, "🚀 100% Турбо (Boost)"),
            (99, "Выход (Сброс на BIOS)"),
        ];
        for (id, txt) in items {
            if id == 20 || id == 99 {
                let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
            }
            let w = wide_null(txt);
            let _ = AppendMenuW(menu, MF_STRING, id, PCWSTR(w.as_ptr()));
        }
        let mut pt = POINT::default();
        let _ = GetCursorPos(&mut pt);
        let _ = SetForegroundWindow(self.hwnd);
        let cmd = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_BOTTOMALIGN | TPM_LEFTALIGN, pt.x, pt.y, 0, self.hwnd, None).0 as usize;
        let _ = DestroyMenu(menu);

        match cmd {
            10 => {
                let _ = ShowWindow(self.hwnd, SW_SHOW);
                let _ = ShowWindow(self.hwnd, SW_RESTORE);
                let _ = SetForegroundWindow(self.hwnd);
            }
            20..=24 => self.apply_action(UiAction::SelectMode(cmd - 20)),
            99 => self.safe_quit(),
            _ => {}
        }
    }
}

unsafe extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match msg {
        WM_ERASEBKGND => LRESULT(1),
        WM_DPICHANGED => {
            let new_dpi = ((wparam.0 & 0xFFFF) as f32) / 96.0;
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    st.dpi = new_dpi.max(1.0);
                    st.free_backbuffer();
                }
            });
            let suggested = *(lparam.0 as *const RECT);
            let _ = windows::Win32::UI::WindowsAndMessaging::SetWindowPos(
                hwnd,
                HWND::default(),
                suggested.left,
                suggested.top,
                suggested.right - suggested.left,
                suggested.bottom - suggested.top,
                windows::Win32::UI::WindowsAndMessaging::SWP_NOZORDER | windows::Win32::UI::WindowsAndMessaging::SWP_NOACTIVATE,
            );
            let _ = InvalidateRect(hwnd, None, false);
            LRESULT(0)
        }
        WM_SIZE => {
            if wparam.0 == 1 {
                // SIZE_MINIMIZED -> immediately release offscreen DIB & trim working set
                UI_STATE.with(|cell| {
                    if let Some(st) = cell.borrow_mut().as_mut() {
                        st.free_backbuffer();
                    }
                });
            } else {
                let _ = InvalidateRect(hwnd, None, false);
            }
            LRESULT(0)
        }
        WM_QUERYENDSESSION => LRESULT(1),
        WM_ENDSESSION => {
            if wparam.0 != 0 {
                UI_STATE.with(|cell| {
                    if let Some(st) = cell.borrow_mut().as_mut() {
                        st.bridge.running.store(false, std::sync::atomic::Ordering::SeqCst);
                        st.bridge.notify_wake();
                    }
                });
                let mut wmi = crate::wmi_ec::LenovoWmiHardware::new();
                let _ = wmi.reset_to_auto();
            }
            LRESULT(0)
        }
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let hdc = BeginPaint(hwnd, &mut ps);
            let mut rc = RECT::default();
            let _ = GetClientRect(hwnd, &mut rc);
            let w = rc.right - rc.left;
            let h = rc.bottom - rc.top;
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    st.render(w, h);
                    if !st.back_dc.is_invalid() {
                        let _ = BitBlt(hdc, 0, 0, w, h, st.back_dc, 0, 0, SRCCOPY);
                    }
                }
            });
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        WM_APP_TELEMETRY => {
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    let tel_opt = st.bridge.latest_telemetry.lock().ok().and_then(|g| g.clone());
                    st.update_tray_icons(tel_opt.as_ref());
                    if IsWindowVisible(hwnd).as_bool() {
                        let _ = InvalidateRect(hwnd, None, false);
                    }
                }
            });
            LRESULT(0)
        }
        WM_APP_TRAY => {
            let ev = (lparam.0 & 0xFFFF) as u32;
            if ev == WM_LBUTTONDOWN || ev == WM_LBUTTONDBLCLK {
                if IsWindowVisible(hwnd).as_bool() {
                    UI_STATE.with(|cell| {
                        if let Some(st) = cell.borrow_mut().as_mut() {
                            let _ = ShowWindow(hwnd, SW_HIDE);
                            st.free_backbuffer();
                        }
                    });
                } else {
                    let _ = ShowWindow(hwnd, SW_SHOW);
                    let _ = ShowWindow(hwnd, SW_RESTORE);
                    let _ = SetForegroundWindow(hwnd);
                    let _ = InvalidateRect(hwnd, None, false);
                }
            } else if ev == WM_RBUTTONDOWN {
                UI_STATE.with(|cell| {
                    if let Some(st) = cell.borrow_mut().as_mut() {
                        st.show_tray_popup_menu();
                    }
                });
            }
            LRESULT(0)
        }
        WM_APP_SHOW_WINDOW => {
            let _ = ShowWindow(hwnd, SW_SHOW);
            let _ = ShowWindow(hwnd, SW_RESTORE);
            let _ = SetForegroundWindow(hwnd);
            let _ = InvalidateRect(hwnd, None, false);
            LRESULT(0)
        }
        WM_APP_QUIT_CLEAN => {
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    st.safe_quit();
                }
            });
            LRESULT(0)
        }
        WM_APP_IPC_MODE => {
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    st.apply_action(UiAction::SelectMode(wparam.0));
                }
            });
            LRESULT(0)
        }
        WM_LBUTTONDOWN => {
            let mx = (lparam.0 & 0xFFFF) as i16 as i32;
            let my = ((lparam.0 >> 16) & 0xFFFF) as i16 as i32;
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    if let Some(sl) = st.slider_rect {
                        if pt_in_rect(&sl, mx, my) {
                            st.dragging_slider = true;
                            let _ = SetCapture(hwnd);
                            let ratio = ((mx - sl.left) as f64 / ((sl.right - sl.left).max(1) as f64)).clamp(0.0, 1.0);
                            let rpm = (((2000.0 + ratio * 5500.0) / 50.0).round() as i32) * 50;
                            st.bridge.config.update(|c| c.manual_rpm = rpm, false);
                            st.bridge.notify_wake();
                            let _ = InvalidateRect(hwnd, None, false);
                            return;
                        }
                    }
                    if let Some(plot) = st.curve_plot_rect {
                        let cfg = st.bridge.config.get();
                        let pw = (plot.right - plot.left).max(1) as f64;
                        let ph = (plot.bottom - plot.top).max(1) as f64;
                        for (i, pt) in cfg.curve_points.iter().enumerate() {
                            let px = plot.left + (((pt.temp - 30) as f64 / 60.0) * pw).round() as i32;
                            let py = plot.top + ((1.0 - (pt.rpm - 2000) as f64 / 5500.0) * ph).round() as i32;
                            let d2 = (px - mx) * (px - mx) + (py - my) * (py - my);
                            let r_hit = st.s(14);
                            if d2 <= r_hit * r_hit {
                                st.dragging_curve_pt = Some(i);
                                let _ = SetCapture(hwnd);
                                return;
                            }
                        }
                    }
                    let hit = st.hitboxes.iter().find(|h| pt_in_rect(&h.rect, mx, my)).map(|h| h.action.clone());
                    if let Some(act) = hit {
                        st.apply_action(act);
                    }
                }
            });
            LRESULT(0)
        }
        WM_LBUTTONDBLCLK => {
            let mx = (lparam.0 & 0xFFFF) as i16 as i32;
            let my = ((lparam.0 >> 16) & 0xFFFF) as i16 as i32;
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    if let Some(plot) = st.curve_plot_rect {
                        if pt_in_rect(&plot, mx, my) {
                            let nx = ((mx - plot.left) as f64 / ((plot.right - plot.left).max(1) as f64)).clamp(0.0, 1.0);
                            let ny = (1.0 - (my - plot.top) as f64 / ((plot.bottom - plot.top).max(1) as f64)).clamp(0.0, 1.0);
                            let t_int = (30.0 + nx * 60.0).round() as i32;
                            let r_int = (((2000.0 + ny * 5500.0) / 50.0).round() as i32) * 50;
                            st.bridge.config.update(|c| {
                                if let Some(existing) = c.curve_points.iter_mut().find(|p| (p.temp - t_int).abs() <= 2) {
                                    existing.rpm = r_int;
                                } else {
                                    c.curve_points.push(CurvePoint { temp: t_int, rpm: r_int });
                                }
                            }, true);
                            st.bridge.notify_wake();
                            let _ = InvalidateRect(hwnd, None, false);
                        }
                    }
                }
            });
            LRESULT(0)
        }
        WM_RBUTTONDOWN => {
            let mx = (lparam.0 & 0xFFFF) as i16 as i32;
            let my = ((lparam.0 >> 16) & 0xFFFF) as i16 as i32;
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    if let Some(plot) = st.curve_plot_rect {
                        let cfg = st.bridge.config.get();
                        if cfg.curve_points.len() > 2 {
                            let pw = (plot.right - plot.left).max(1) as f64;
                            let ph = (plot.bottom - plot.top).max(1) as f64;
                            let mut rem_idx = None;
                            for (i, pt) in cfg.curve_points.iter().enumerate() {
                                let px = plot.left + (((pt.temp - 30) as f64 / 60.0) * pw).round() as i32;
                                let py = plot.top + ((1.0 - (pt.rpm - 2000) as f64 / 5500.0) * ph).round() as i32;
                                let r_hit = st.s(14);
                                if (px - mx) * (px - mx) + (py - my) * (py - my) <= r_hit * r_hit {
                                    rem_idx = Some(i);
                                    break;
                                }
                            }
                            if let Some(idx) = rem_idx {
                                st.bridge.config.update(|c| {
                                    if c.curve_points.len() > 2 && idx < c.curve_points.len() {
                                        c.curve_points.remove(idx);
                                    }
                                }, true);
                                st.bridge.notify_wake();
                                let _ = InvalidateRect(hwnd, None, false);
                            }
                        }
                    }
                }
            });
            LRESULT(0)
        }
        WM_MOUSEMOVE => {
            let mx = (lparam.0 & 0xFFFF) as i16 as i32;
            let my = ((lparam.0 >> 16) & 0xFFFF) as i16 as i32;
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    if st.dragging_slider {
                        if let Some(sl) = st.slider_rect {
                            let ratio = ((mx - sl.left) as f64 / ((sl.right - sl.left).max(1) as f64)).clamp(0.0, 1.0);
                            let rpm = (((2000.0 + ratio * 5500.0) / 50.0).round() as i32) * 50;
                            st.bridge.config.update(|c| c.manual_rpm = rpm, false);
                            st.bridge.notify_wake();
                            let _ = InvalidateRect(hwnd, None, false);
                        }
                        return;
                    }
                    if let Some(idx) = st.dragging_curve_pt {
                        if let Some(plot) = st.curve_plot_rect {
                            let nx = ((mx - plot.left) as f64 / ((plot.right - plot.left).max(1) as f64)).clamp(0.0, 1.0);
                            let ny = (1.0 - (my - plot.top) as f64 / ((plot.bottom - plot.top).max(1) as f64)).clamp(0.0, 1.0);
                            let t_raw = (30.0 + nx * 60.0).round() as i32;
                            let r_raw = (((2000.0 + ny * 5500.0) / 50.0).round() as i32) * 50;
                            st.bridge.config.update(|c| {
                                if idx < c.curve_points.len() {
                                    let min_t = if idx == 0 { 30 } else { c.curve_points[idx - 1].temp + 1 };
                                    let max_t = if idx + 1 >= c.curve_points.len() { 90 } else { c.curve_points[idx + 1].temp - 1 };
                                    c.curve_points[idx].temp = t_raw.clamp(min_t, max_t.max(min_t));
                                    c.curve_points[idx].rpm = r_raw.clamp(2000, 7500);
                                }
                            }, false);
                            st.bridge.notify_wake();
                            let _ = InvalidateRect(hwnd, None, false);
                        }
                        return;
                    }

                    let over_btn = st.hitboxes.iter().position(|h| pt_in_rect(&h.rect, mx, my));
                    let cur_id = if over_btn.is_some() { IDC_HAND } else { IDC_ARROW };
                    if let Ok(cur) = LoadCursorW(HINSTANCE::default(), cur_id) {
                        windows::Win32::UI::WindowsAndMessaging::SetCursor(cur);
                    }
                    st.hover_idx = over_btn;
                }
            });
            LRESULT(0)
        }
        WM_LBUTTONUP => {
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    if st.dragging_slider || st.dragging_curve_pt.is_some() {
                        st.dragging_slider = false;
                        st.dragging_curve_pt = None;
                        let _ = ReleaseCapture();
                        st.bridge.config.save();
                        let _ = InvalidateRect(hwnd, None, false);
                    }
                }
            });
            LRESULT(0)
        }
        WM_CLOSE => {
            UI_STATE.with(|cell| {
                if let Some(st) = cell.borrow_mut().as_mut() {
                    if st.bridge.config.get().minimize_to_tray_on_close {
                        let _ = ShowWindow(hwnd, SW_HIDE);
                        st.free_backbuffer();
                    } else {
                        st.safe_quit();
                    }
                }
            });
            LRESULT(0)
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

pub fn create_main_window_and_run(bridge: Arc<ControllerBridge>, start_minimized: bool, hwnd_out: Arc<Mutex<Option<isize>>>) {
    unsafe {
        let _ = SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

        let mut gdiplus_token = 0usize;
        let gdiplus_in = GdiplusStartupInput {
            GdiplusVersion: 1,
            ..Default::default()
        };
        let _ = GdiplusStartup(&mut gdiplus_token, &gdiplus_in, std::ptr::null_mut());

        let cls_name = wide_null("ThinkBookFanControlRustWnd");
        let h_inst: HINSTANCE = windows::Win32::System::LibraryLoader::GetModuleHandleW(PCWSTR::null())
            .map(|m| HINSTANCE(m.0))
            .unwrap_or_default();
        let app_hicon = LoadIconW(h_inst, PCWSTR(1usize as *const u16)).unwrap_or_default();
        let wc = WNDCLASSW {
            style: CS_DBLCLKS,
            lpfnWndProc: Some(wnd_proc),
            hInstance: h_inst,
            hIcon: app_hicon,
            lpszClassName: PCWSTR(cls_name.as_ptr()),
            hCursor: LoadCursorW(HINSTANCE::default(), IDC_ARROW).unwrap_or_default(),
            ..Default::default()
        };
        let _ = RegisterClassW(&wc);

        let title = wide_null("ThinkBook Fan Control — Lenovo 14 G6+ IMH (Native Rust)");
        let hwnd = CreateWindowExW(
            Default::default(),
            PCWSTR(cls_name.as_ptr()),
            PCWSTR(title.as_ptr()),
            WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            1020,
            800,
            HWND::default(),
            None,
            HINSTANCE::default(),
            None,
        )
        .unwrap_or_default();

        // Dark Windows title bar
        let dark: i32 = 1;
        let _ = DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, &dark as *const i32 as *const _, 4);

        let dpi = (GetDpiForWindow(hwnd) as f32 / 96.0).max(1.0);
        let scaled_w = (980.0 * dpi).round() as i32;
        let scaled_h = (770.0 * dpi).round() as i32;
        let _ = windows::Win32::UI::WindowsAndMessaging::SetWindowPos(
            hwnd,
            HWND::default(),
            0,
            0,
            scaled_w,
            scaled_h,
            windows::Win32::UI::WindowsAndMessaging::SWP_NOMOVE | windows::Win32::UI::WindowsAndMessaging::SWP_NOZORDER,
        );

        let mut st = AppUiState {
            hwnd,
            bridge: bridge.clone(),
            dpi,
            show_cores_panel: true,
            hitboxes: Vec::new(),
            hover_idx: None,
            curve_plot_rect: None,
            dragging_curve_pt: None,
            hover_curve_pt: None,
            slider_rect: None,
            dragging_slider: false,
            back_dc: HDC::default(),
            back_bmp: HBITMAP::default(),
            back_w: 0,
            back_h: 0,
            tray_cpu_added: false,
            tray_gpu_added: false,
            tray_cpu_icon: HICON::default(),
            tray_gpu_icon: HICON::default(),
            last_cpu_badge_key: String::new(),
            last_gpu_badge_key: String::new(),
        };
        st.update_tray_icons(None);

        if let Ok(mut g) = hwnd_out.lock() {
            *g = Some(hwnd.0 as isize);
        }

        if start_minimized {
            let _ = ShowWindow(hwnd, SW_HIDE);
            st.free_backbuffer();
        } else {
            let _ = ShowWindow(hwnd, SW_SHOW);
        }

        UI_STATE.with(|cell| {
            *cell.borrow_mut() = Some(st);
        });

        let mut msg = MSG::default();
        while GetMessageW(&mut msg, HWND::default(), 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        UI_STATE.with(|cell| {
            if let Some(mut st) = cell.borrow_mut().take() {
                st.remove_tray_icons();
                st.free_backbuffer();
            }
        });

        if gdiplus_token != 0 {
            GdiplusShutdown(gdiplus_token);
        }
    }
}

pub fn smoke_test_tray_icons() -> bool {
    unsafe {
        let ic_cpu = create_labeled_tray_hicon("CPU", "44", argb(255, 2, 132, 199), argb(255, 15, 118, 110));
        let ic_gpu = create_labeled_tray_hicon("GPU", "OFF", argb(255, 5, 150, 105), argb(255, 6, 78, 59));
        let ok = !ic_cpu.is_invalid() && !ic_cpu.0.is_null() && !ic_gpu.is_invalid() && !ic_gpu.0.is_null();
        if !ic_cpu.is_invalid() {
            let _ = DestroyIcon(ic_cpu);
        }
        if !ic_gpu.is_invalid() {
            let _ = DestroyIcon(ic_gpu);
        }
        ok
    }
}
