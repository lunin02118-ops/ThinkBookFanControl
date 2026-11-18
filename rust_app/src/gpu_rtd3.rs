use std::ffi::c_void;
use std::time::Instant;
use windows::core::PCWSTR;
use windows::Win32::Foundation::{FreeLibrary, HMODULE};
use windows::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryW};

pub const POWER_DEVICE_D0: u32 = 1;
pub const POWER_DEVICE_D3: u32 = 4;

#[repr(C)]
#[derive(Copy, Clone)]
struct DevPropKey {
    fmtid: [u8; 16],
    pid: u32,
}

// DEVPKEY_Device_PowerData: {A45C254E-DF1C-4EFD-8020-67D146A850E0}, 32
const DEVPKEY_DEVICE_POWER_DATA: DevPropKey = DevPropKey {
    fmtid: [
        0x4E, 0x25, 0x5C, 0xA4, 0x1C, 0xDF, 0xFD, 0x4E,
        0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0,
    ],
    pid: 32,
};

// DEVPKEY_DisplayAdapter_Luid: {60B193CB-5276-4D0F-96FC-F173ABAD3EC6}, 2
const DEVPKEY_DISPLAY_ADAPTER_LUID: DevPropKey = DevPropKey {
    fmtid: [
        0xCB, 0x93, 0xB1, 0x60, 0x76, 0x52, 0x0F, 0x4D,
        0x96, 0xFC, 0xF1, 0x73, 0xAB, 0xAD, 0x3E, 0xC6,
    ],
    pid: 2,
};

#[repr(C)]
struct PdhFmtCounterValueItemW {
    sz_name: *const u16,
    c_status: u32,
    _pad: u32,
    double_value: f64,
}

#[link(name = "windows.0.52.0")]
extern "system" {
    fn CM_Get_Device_ID_List_SizeW(pul_len: *mut u32, psz_filter: *const u16, ul_flags: u32) -> u32;
    fn CM_Get_Device_ID_ListW(psz_filter: *const u16, buffer: *mut u16, buffer_len: u32, ul_flags: u32) -> u32;
    fn CM_Locate_DevNodeW(pdn_dev_inst: *mut u32, p_device_id: *const u16, ul_flags: u32) -> u32;
    fn CM_Get_DevNode_PropertyW(
        dn_dev_inst: u32,
        property_key: *const DevPropKey,
        property_type: *mut u32,
        property_buffer: *mut u8,
        property_buffer_size: *mut u32,
        ul_flags: u32,
    ) -> u32;
}

#[link(name = "windows.0.52.0")]
extern "system" {
    fn PdhOpenQueryW(sz_data_source: *const u16, dw_user_data: usize, ph_query: *mut isize) -> u32;
    fn PdhAddEnglishCounterW(
        h_query: isize,
        sz_full_counter_path: *const u16,
        dw_user_data: usize,
        ph_counter: *mut isize,
    ) -> u32;
    fn PdhCollectQueryData(h_query: isize) -> u32;
    fn PdhGetFormattedCounterArrayW(
        h_counter: isize,
        dw_format: u32,
        lpdw_buffer_size: *mut u32,
        lpdw_item_count: *mut u32,
        item_buffer: *mut u8,
    ) -> u32;
    fn PdhCloseQuery(h_query: isize) -> u32;
}

fn wide_null(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

unsafe fn ptr_to_string_lower(p: *const u16) -> String {
    if p.is_null() {
        return String::new();
    }
    let mut len = 0usize;
    while *p.add(len) != 0 {
        len += 1;
    }
    String::from_utf16_lossy(std::slice::from_raw_parts(p, len)).to_ascii_lowercase()
}

pub struct OptimusRtd3Detector {
    devinst: Option<u32>,
    luid_tag: Option<String>,
    pdh_query: isize,
    cnt_util: isize,
    cnt_vram: isize,
    initialized: bool,
}

impl OptimusRtd3Detector {
    pub fn new() -> Self {
        Self {
            devinst: None,
            luid_tag: None,
            pdh_query: 0,
            cnt_util: 0,
            cnt_vram: 0,
            initialized: false,
        }
    }

    pub fn ensure_init(&mut self) {
        if self.initialized {
            return;
        }
        self.initialized = true;

        unsafe {
            let disp_class = wide_null("{4d36e968-e325-11ce-bfc1-08002be10318}");
            let flags = 0x0000_0300u32; // CM_GETIDLIST_FILTER_CLASS | CM_GETIDLIST_FILTER_PRESENT
            let mut ul_len = 0u32;
            if CM_Get_Device_ID_List_SizeW(&mut ul_len, disp_class.as_ptr(), flags) == 0 && ul_len > 1 {
                let mut buf = vec![0u16; ul_len as usize];
                if CM_Get_Device_ID_ListW(disp_class.as_ptr(), buf.as_mut_ptr(), ul_len, flags) == 0 {
                    for slice in buf.split(|&c| c == 0) {
                        if slice.is_empty() {
                            continue;
                        }
                        let id_str = String::from_utf16_lossy(slice);
                        if !id_str.to_ascii_uppercase().starts_with("PCI\\VEN_10DE&") {
                            continue;
                        }
                        let w_id = wide_null(&id_str);
                        let mut dn = 0u32;
                        if CM_Locate_DevNodeW(&mut dn, w_id.as_ptr(), 0) == 0 {
                            self.devinst = Some(dn);
                            let mut pt = 0u32;
                            let mut luid_buf = [0u8; 16];
                            let mut bl = 16u32;
                            if CM_Get_DevNode_PropertyW(
                                dn,
                                &DEVPKEY_DISPLAY_ADAPTER_LUID,
                                &mut pt,
                                luid_buf.as_mut_ptr(),
                                &mut bl,
                                0,
                            ) == 0 && bl >= 8
                            {
                                let low = u32::from_le_bytes([luid_buf[0], luid_buf[1], luid_buf[2], luid_buf[3]]);
                                let high = u32::from_le_bytes([luid_buf[4], luid_buf[5], luid_buf[6], luid_buf[7]]);
                                self.luid_tag = Some(format!("luid_0x{:08x}_0x{:08x}", high, low));
                            }
                            break;
                        }
                    }
                }
            }

            let mut hq = 0isize;
            if PdhOpenQueryW(std::ptr::null(), 0, &mut hq) == 0 {
                self.pdh_query = hq;
                let p_util = wide_null("\\GPU Engine(*)\\Utilization Percentage");
                let p_vram = wide_null("\\GPU Adapter Memory(*)\\Dedicated Usage");
                let mut cu = 0isize;
                let mut cv = 0isize;
                if PdhAddEnglishCounterW(hq, p_util.as_ptr(), 0, &mut cu) == 0 {
                    self.cnt_util = cu;
                }
                if PdhAddEnglishCounterW(hq, p_vram.as_ptr(), 0, &mut cv) == 0 {
                    self.cnt_vram = cv;
                }
                let _ = PdhCollectQueryData(hq);
            }
        }
    }

    pub fn get_power_state(&mut self) -> Option<u32> {
        self.ensure_init();
        let dn = self.devinst?;
        unsafe {
            let mut buf = [0u8; 128];
            let mut pt = 0u32;
            let mut bl = 128u32;
            if CM_Get_DevNode_PropertyW(
                dn,
                &DEVPKEY_DEVICE_POWER_DATA,
                &mut pt,
                buf.as_mut_ptr(),
                &mut bl,
                0,
            ) == 0
                && bl >= 8
            {
                return Some(u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]));
            }
        }
        None
    }

    unsafe fn read_pdh_items(counter: isize) -> Vec<(String, f64)> {
        if counter == 0 {
            return Vec::new();
        }
        let mut bs = 0u32;
        let mut ic = 0u32;
        let _ = PdhGetFormattedCounterArrayW(counter, 0x0000_0200, &mut bs, &mut ic, std::ptr::null_mut());
        if bs == 0 || ic == 0 {
            return Vec::new();
        }
        let mut buf = vec![0u8; bs as usize];
        if PdhGetFormattedCounterArrayW(counter, 0x0000_0200, &mut bs, &mut ic, buf.as_mut_ptr()) != 0 {
            return Vec::new();
        }
        let items = std::slice::from_raw_parts(buf.as_ptr() as *const PdhFmtCounterValueItemW, ic as usize);
        let mut out = Vec::with_capacity(ic as usize);
        for it in items {
            out.push((ptr_to_string_lower(it.sz_name), it.double_value));
        }
        out
    }

    pub fn get_wddm_activity(&mut self) -> (f64, f64) {
        self.ensure_init();
        if self.pdh_query == 0 {
            return (0.0, 0.0);
        }
        unsafe {
            let _ = PdhCollectQueryData(self.pdh_query);
            let vram_items = Self::read_pdh_items(self.cnt_vram);

            if self.luid_tag.is_none() {
                for (name, val) in &vram_items {
                    if *val > 0.0 && name.contains("luid_0x") {
                        if let Some(prefix) = name.split("_phys_").next() {
                            if prefix.starts_with("luid_0x") {
                                self.luid_tag = Some(prefix.to_string());
                                break;
                            }
                        }
                    }
                }
            }

            let mut ded_mb = 0.0f64;
            for (name, val) in &vram_items {
                if let Some(ref tag) = self.luid_tag {
                    if name.contains(tag) {
                        ded_mb = ded_mb.max(*val / (1024.0 * 1024.0));
                    }
                } else if *val > 0.0 {
                    ded_mb = ded_mb.max(*val / (1024.0 * 1024.0));
                }
            }

            let mut total_util = 0.0f64;
            if let Some(ref tag) = self.luid_tag {
                for (name, val) in Self::read_pdh_items(self.cnt_util) {
                    if name.contains(tag) && val > 0.0 {
                        total_util += val;
                    }
                }
            }

            (total_util.min(100.0), ded_mb)
        }
    }
}

impl Drop for OptimusRtd3Detector {
    fn drop(&mut self) {
        if self.pdh_query != 0 {
            unsafe {
                let _ = PdhCloseQuery(self.pdh_query);
            }
            self.pdh_query = 0;
        }
    }
}

#[repr(C)]
struct NvmlUtilization {
    gpu: u32,
    memory: u32,
}

type FnNvmlInit = unsafe extern "C" fn() -> i32;
type FnNvmlShutdown = unsafe extern "C" fn() -> i32;
type FnNvmlGetHandle = unsafe extern "C" fn(u32, *mut *mut c_void) -> i32;
type FnNvmlGetTemp = unsafe extern "C" fn(*mut c_void, i32, *mut u32) -> i32;
type FnNvmlGetPower = unsafe extern "C" fn(*mut c_void, *mut u32) -> i32;
type FnNvmlGetUtil = unsafe extern "C" fn(*mut c_void, *mut NvmlUtilization) -> i32;

struct NvmlApi {
    module: HMODULE,
    device: *mut c_void,
    fn_shutdown: FnNvmlShutdown,
    fn_temp: FnNvmlGetTemp,
    fn_power: FnNvmlGetPower,
    fn_util: FnNvmlGetUtil,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DgpuState {
    D3Cold,
    D0Idle,
    D0Active,
}

#[derive(Debug, Clone)]
pub struct GpuTelemetry {
    pub state: DgpuState,
    pub sleeping: bool,
    pub temp_c: Option<i32>,
    pub power_w: Option<f64>,
    pub gpu_util: Option<u32>,
    pub mem_util: Option<u32>,
}

pub struct NvmlHardware {
    api: Option<NvmlApi>,
    pub rtd3: OptimusRtd3Detector,
    idle_ticks: u32,
    idle_released: bool,
    prev_pstate: Option<u32>,
    last_init_attempt: Option<Instant>,
}

impl NvmlHardware {
    pub fn new() -> Self {
        Self {
            api: None,
            rtd3: OptimusRtd3Detector::new(),
            idle_ticks: 0,
            idle_released: true,
            prev_pstate: None,
            last_init_attempt: None,
        }
    }

    fn open_nvml(&mut self) -> bool {
        if self.api.is_some() {
            return true;
        }
        if let Some(t) = self.last_init_attempt {
            if t.elapsed().as_millis() < 1500 {
                return false;
            }
        }
        self.last_init_attempt = Some(Instant::now());

        let candidates = [
            "nvml.dll",
            "C:\\Program Files\\NVIDIA Corporation\\NVSMI\\nvml.dll",
        ];

        unsafe {
            for path in candidates {
                let w = wide_null(path);
                let Ok(hmod) = LoadLibraryW(PCWSTR(w.as_ptr())) else { continue };
                let p_init = GetProcAddress(hmod, windows::core::s!("nvmlInit_v2"));
                let p_shut = GetProcAddress(hmod, windows::core::s!("nvmlShutdown"));
                let p_hnd = GetProcAddress(hmod, windows::core::s!("nvmlDeviceGetHandleByIndex_v2"));
                let p_tmp = GetProcAddress(hmod, windows::core::s!("nvmlDeviceGetTemperature"));
                let p_pwr = GetProcAddress(hmod, windows::core::s!("nvmlDeviceGetPowerUsage"));
                let p_utl = GetProcAddress(hmod, windows::core::s!("nvmlDeviceGetUtilizationRates"));

                if let (Some(fi), Some(fs), Some(fh), Some(ft), Some(fp), Some(fu)) =
                    (p_init, p_shut, p_hnd, p_tmp, p_pwr, p_utl)
                {
                    let fn_init: FnNvmlInit = std::mem::transmute(fi);
                    let fn_shutdown: FnNvmlShutdown = std::mem::transmute(fs);
                    let fn_handle: FnNvmlGetHandle = std::mem::transmute(fh);
                    let fn_temp: FnNvmlGetTemp = std::mem::transmute(ft);
                    let fn_power: FnNvmlGetPower = std::mem::transmute(fp);
                    let fn_util: FnNvmlGetUtil = std::mem::transmute(fu);

                    if fn_init() == 0 {
                        let mut dev: *mut c_void = std::ptr::null_mut();
                        if fn_handle(0, &mut dev) == 0 && !dev.is_null() {
                            self.api = Some(NvmlApi {
                                module: hmod,
                                device: dev,
                                fn_shutdown,
                                fn_temp,
                                fn_power,
                                fn_util,
                            });
                            return true;
                        }
                        let _ = fn_shutdown();
                    }
                }
                let _ = FreeLibrary(hmod);
            }
        }
        false
    }

    pub fn close_nvml(&mut self) {
        if let Some(api) = self.api.take() {
            unsafe {
                let _ = (api.fn_shutdown)();
                let _ = FreeLibrary(api.module);
            }
        }
    }

    pub fn poll_rtd3_safe(&mut self, log_fn: &mut dyn FnMut(&str)) -> GpuTelemetry {
        let p_state = self.rtd3.get_power_state();
        let (wddm_util, wddm_vram_mb) = self.rtd3.get_wddm_activity();

        if p_state == Some(POWER_DEVICE_D3) {
            if self.api.is_some() {
                self.close_nvml();
            }
            if self.prev_pstate != Some(POWER_DEVICE_D3) {
                log_fn("NVIDIA dGPU entered RTD3 sleep (D3cold, 0W — running on Intel Arc iGPU).");
            }
            self.prev_pstate = Some(POWER_DEVICE_D3);
            self.idle_released = true;
            self.idle_ticks = 0;
            return GpuTelemetry {
                state: DgpuState::D3Cold,
                sleeping: true,
                temp_c: None,
                power_w: Some(0.0),
                gpu_util: Some(0),
                mem_util: Some(0),
            };
        }

        let has_workload = (wddm_util >= 1.0) || (wddm_vram_mb >= 32.0);
        self.prev_pstate = p_state;

        if self.idle_released {
            if has_workload {
                self.idle_released = false;
                self.idle_ticks = 0;
                log_fn(&format!(
                    "NVIDIA dGPU active (WDDM util={:.1}%, VRAM={:.1}MB) — resuming NVML telemetry.",
                    wddm_util, wddm_vram_mb
                ));
            } else {
                return GpuTelemetry {
                    state: DgpuState::D0Idle,
                    sleeping: true,
                    temp_c: None,
                    power_w: None,
                    gpu_util: Some(0),
                    mem_util: Some(0),
                };
            }
        }

        if !self.open_nvml() {
            return GpuTelemetry {
                state: DgpuState::D0Idle,
                sleeping: true,
                temp_c: None,
                power_w: None,
                gpu_util: None,
                mem_util: None,
            };
        }

        let mut temp_c = None;
        let mut power_w = None;
        let mut gpu_util = None;
        let mut mem_util = None;
        let mut comm_err = false;

        if let Some(ref api) = self.api {
            unsafe {
                let mut t = 0u32;
                let rt = (api.fn_temp)(api.device, 0, &mut t);
                if rt == 0 && (5..=115).contains(&t) {
                    temp_c = Some(t as i32);
                } else if rt != 0 {
                    comm_err = true;
                }

                let mut p = 0u32;
                let rp = (api.fn_power)(api.device, &mut p);
                if rp == 0 {
                    power_w = Some(((p as f64) / 100.0).round() / 10.0);
                }

                let mut u = NvmlUtilization { gpu: 0, memory: 0 };
                if (api.fn_util)(api.device, &mut u) == 0 {
                    gpu_util = Some(u.gpu);
                    mem_util = Some(u.memory);
                }
            }
        }

        if comm_err {
            self.close_nvml();
        }

        let nv_u = gpu_util.unwrap_or(0);
        let is_idle_now = !has_workload
            && nv_u <= 1
            && power_w.map(|w| w <= 8.0).unwrap_or(true)
            && temp_c.map(|t| t <= 68).unwrap_or(true);

        if is_idle_now {
            self.idle_ticks += 1;
            if self.idle_ticks >= 4 {
                log_fn("NVIDIA dGPU idle for 4s — releasing NVML & unloading DLL to allow Optimus D3cold sleep.");
                self.close_nvml();
                self.idle_released = true;
            }
        } else {
            self.idle_ticks = 0;
        }

        GpuTelemetry {
            state: DgpuState::D0Active,
            sleeping: false,
            temp_c,
            power_w,
            gpu_util,
            mem_util,
        }
    }
}

impl Drop for NvmlHardware {
    fn drop(&mut self) {
        self.close_nvml();
    }
}
