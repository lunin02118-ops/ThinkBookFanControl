use crate::config::app_dir;
use std::fs;
use std::time::Instant;
use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
};
use windows::Win32::System::IO::DeviceIoControl;
use windows::Win32::System::Services::{
    CloseServiceHandle, ControlService, CreateServiceW, OpenSCManagerW, OpenServiceW,
    StartServiceW, SC_MANAGER_ALL_ACCESS, SERVICE_ALL_ACCESS, SERVICE_CONTROL_STOP,
    SERVICE_DEMAND_START, SERVICE_ERROR_NORMAL, SERVICE_KERNEL_DRIVER, SERVICE_STATUS,
};
use windows::Win32::System::Threading::{GetCurrentThread, SetThreadAffinityMask};

const WINRING0_SYS_BYTES: &[u8] = include_bytes!("../WinRing0x64.sys");
const IOCTL_OLS_READ_MSR: u32 = 0x9C40_2084;

const MSR_IA32_THERM_STATUS: u32 = 0x019C;
const MSR_IA32_TEMPERATURE_TARGET: u32 = 0x01A2;
const MSR_IA32_PACKAGE_THERM_STATUS: u32 = 0x01B1;
const MSR_RAPL_POWER_UNIT: u32 = 0x0606;
const MSR_PKG_ENERGY_STATUS: u32 = 0x0611;

#[link(name = "kernel32")]
extern "system" {
    fn GetLogicalProcessorInformationEx(
        relationship_type: u32,
        buffer: *mut u8,
        returned_length: *mut u32,
    ) -> i32;
}

#[link(name = "ntdll")]
extern "system" {
    fn NtQuerySystemInformation(
        system_information_class: u32,
        system_information: *mut u8,
        system_information_length: u32,
        return_length: *mut u32,
    ) -> i32;
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

#[repr(C)]
#[derive(Copy, Clone, Default)]
struct SystemProcessorPerformanceInformation {
    idle_time: i64,
    kernel_time: i64,
    user_time: i64,
    dpc_time: i64,
    interrupt_time: i64,
    interrupt_count: u32,
    _pad: u32,
}

#[repr(C)]
struct PdhItemW {
    sz_name: *const u16,
    c_status: u32,
    _pad: u32,
    double_value: f64,
}

fn wide_null(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CoreKind {
    Performance,
    Efficient,
    LowPower,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct CoreInfo {
    pub label: String,
    pub kind: CoreKind,
    pub logical_cpus: Vec<usize>,
    pub temp_c: Option<f64>,
    pub load_pct: f64,
    pub mhz: u32,
}

#[derive(Debug, Clone)]
pub struct CpuTelemetry {
    pub msr_active: bool,
    pub avg_temp_c: Option<f64>,
    pub max_temp_c: Option<f64>,
    pub pkg_temp_c: Option<f64>,
    pub power_w: Option<f64>,
    pub total_load_pct: f64,
    pub cores: Vec<CoreInfo>,
}

struct PhysicalCoreSpec {
    label: String,
    kind: CoreKind,
    logical_cpus: Vec<usize>,
}

struct Ring0Driver {
    handle: HANDLE,
    energy_unit_joules: f64,
    prev_energy_raw: Option<u32>,
    prev_energy_time: Option<Instant>,
}

impl Ring0Driver {
    fn try_open() -> Option<Self> {
        unsafe {
            let dev_path = wide_null("\\\\.\\WinRing0_1_2_0");
            let mut h = CreateFileW(
                PCWSTR(dev_path.as_ptr()),
                GENERIC_READ.0 | GENERIC_WRITE.0,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                HANDLE::default(),
            )
            .unwrap_or(INVALID_HANDLE_VALUE);

            if h == INVALID_HANDLE_VALUE {
                // Deploy signed WinRing0x64.sys next to executable and start kernel service
                let sys_path = app_dir().join("WinRing0x64.sys");
                if !sys_path.exists() {
                    let _ = fs::write(&sys_path, WINRING0_SYS_BYTES);
                }
                if sys_path.exists() {
                    if let Ok(scm) = OpenSCManagerW(PCWSTR::null(), PCWSTR::null(), SC_MANAGER_ALL_ACCESS) {
                        let svc_name = wide_null("WinRing0_1_2_0");
                        let bin_path = wide_null(&sys_path.to_string_lossy());
                        let svc = OpenServiceW(scm, PCWSTR(svc_name.as_ptr()), SERVICE_ALL_ACCESS)
                            .or_else(|_| {
                                CreateServiceW(
                                    scm,
                                    PCWSTR(svc_name.as_ptr()),
                                    PCWSTR(svc_name.as_ptr()),
                                    SERVICE_ALL_ACCESS,
                                    SERVICE_KERNEL_DRIVER,
                                    SERVICE_DEMAND_START,
                                    SERVICE_ERROR_NORMAL,
                                    PCWSTR(bin_path.as_ptr()),
                                    PCWSTR::null(),
                                    None,
                                    PCWSTR::null(),
                                    PCWSTR::null(),
                                    PCWSTR::null(),
                                )
                            });

                        if let Ok(sh) = svc {
                            let _ = StartServiceW(sh, None);
                            let _ = CloseServiceHandle(sh);
                        }
                        let _ = CloseServiceHandle(scm);
                    }

                    h = CreateFileW(
                        PCWSTR(dev_path.as_ptr()),
                        GENERIC_READ.0 | GENERIC_WRITE.0,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None,
                        OPEN_EXISTING,
                        FILE_ATTRIBUTE_NORMAL,
                        HANDLE::default(),
                    )
                    .unwrap_or(INVALID_HANDLE_VALUE);
                }
            }

            if h == INVALID_HANDLE_VALUE {
                return None;
            }

            let mut drv = Self {
                handle: h,
                energy_unit_joules: 0.5_f64.powi(14),
                prev_energy_raw: None,
                prev_energy_time: None,
            };

            if let Some(unit_msr) = drv.read_msr(MSR_RAPL_POWER_UNIT) {
                let esu = ((unit_msr >> 8) & 0x1F) as i32;
                if (1..=28).contains(&esu) {
                    drv.energy_unit_joules = 0.5_f64.powi(esu);
                }
            }
            // Verify MSR 0x1A2 reads cleanly (rules out VBS/HVCI block)
            let tj = drv.read_msr(MSR_IA32_TEMPERATURE_TARGET)?;
            if tj == 0 {
                let _ = CloseHandle(drv.handle);
                return None;
            }

            Some(drv)
        }
    }

    #[inline]
    fn read_msr(&self, index: u32) -> Option<u64> {
        unsafe {
            let mut out_val = 0u64;
            let mut bytes_ret = 0u32;
            let ok = DeviceIoControl(
                self.handle,
                IOCTL_OLS_READ_MSR,
                Some(&index as *const u32 as *const _),
                4,
                Some(&mut out_val as *mut u64 as *mut _),
                8,
                Some(&mut bytes_ret),
                None,
            );
            if ok.is_ok() && bytes_ret == 8 {
                Some(out_val)
            } else {
                None
            }
        }
    }

    fn read_msr_on_cpu(&self, index: u32, logical_cpu: usize) -> Option<u64> {
        if logical_cpu >= 64 {
            return self.read_msr(index);
        }
        unsafe {
            let thread = GetCurrentThread();
            let prev_mask = SetThreadAffinityMask(thread, 1usize << logical_cpu);
            let res = self.read_msr(index);
            if prev_mask != 0 {
                let _ = SetThreadAffinityMask(thread, prev_mask);
            }
            res
        }
    }

    fn read_core_temp(&self, logical_cpu: usize) -> Option<f64> {
        let tj_msr = self.read_msr_on_cpu(MSR_IA32_TEMPERATURE_TARGET, logical_cpu)?;
        let mut tj_max = ((tj_msr >> 16) & 0xFF) as i32;
        if !(70..=125).contains(&tj_max) {
            tj_max = 110;
        }
        let therm = self.read_msr_on_cpu(MSR_IA32_THERM_STATUS, logical_cpu)?;
        if (therm & 0x8000_0000) == 0 {
            return None;
        }
        let readout = ((therm >> 16) & 0x7F) as i32;
        let temp = (tj_max - readout) as f64;
        if (5.0..=120.0).contains(&temp) {
            Some(temp)
        } else {
            None
        }
    }

    fn read_package_temp(&self) -> Option<f64> {
        let tj_msr = self.read_msr(MSR_IA32_TEMPERATURE_TARGET)?;
        let mut tj_max = ((tj_msr >> 16) & 0xFF) as i32;
        if !(70..=125).contains(&tj_max) {
            tj_max = 110;
        }
        let pkg = self.read_msr(MSR_IA32_PACKAGE_THERM_STATUS)?;
        if (pkg & 0x8000_0000) == 0 {
            return None;
        }
        let readout = ((pkg >> 16) & 0x7F) as i32;
        let temp = (tj_max - readout) as f64;
        if (5.0..=120.0).contains(&temp) {
            Some(temp)
        } else {
            None
        }
    }

    fn read_package_power_w(&mut self) -> Option<f64> {
        let raw_msr = self.read_msr(MSR_PKG_ENERGY_STATUS)?;
        let cur_raw = raw_msr as u32;
        let now = Instant::now();

        let res = if let (Some(prev_r), Some(prev_t)) = (self.prev_energy_raw, self.prev_energy_time) {
            let dt = now.duration_since(prev_t).as_secs_f64();
            if dt >= 0.15 {
                let delta_units = cur_raw.wrapping_sub(prev_r);
                let joules = (delta_units as f64) * self.energy_unit_joules;
                let watts = joules / dt;
                if (0.1..=220.0).contains(&watts) {
                    Some((watts * 10.0).round() / 10.0)
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };

        self.prev_energy_raw = Some(cur_raw);
        self.prev_energy_time = Some(now);
        res
    }
}

impl Drop for Ring0Driver {
    fn drop(&mut self) {
        unsafe {
            if self.handle != INVALID_HANDLE_VALUE && !self.handle.is_invalid() {
                let _ = CloseHandle(self.handle);
                self.handle = INVALID_HANDLE_VALUE;
            }
            if let Ok(scm) = OpenSCManagerW(PCWSTR::null(), PCWSTR::null(), SC_MANAGER_ALL_ACCESS) {
                let svc_name = wide_null("WinRing0_1_2_0");
                if let Ok(sh) = OpenServiceW(scm, PCWSTR(svc_name.as_ptr()), SERVICE_ALL_ACCESS) {
                    let mut st = SERVICE_STATUS::default();
                    let _ = ControlService(sh, SERVICE_CONTROL_STOP, &mut st);
                    let _ = CloseServiceHandle(sh);
                }
                let _ = CloseServiceHandle(scm);
            }
        }
    }
}

pub struct CpuTelemetryEngine {
    specs: Vec<PhysicalCoreSpec>,
    num_logical: usize,
    prev_times: Vec<SystemProcessorPerformanceInformation>,
    ring0: Option<Ring0Driver>,
    ring0_attempted: bool,
    pdh_q: isize,
    cnt_base_mhz: isize,
    cnt_perf_pct: isize,
}

impl CpuTelemetryEngine {
    pub fn new() -> Self {
        let (specs, num_logical) = Self::discover_topology();
        let mut pdh_q = 0isize;
        let mut cnt_base_mhz = 0isize;
        let mut cnt_perf_pct = 0isize;

        unsafe {
            if PdhOpenQueryW(std::ptr::null(), 0, &mut pdh_q) == 0 {
                let p_freq = wide_null("\\Processor Information(0,*)\\Processor Frequency");
                let p_perf = wide_null("\\Processor Information(0,*)\\% Processor Performance");
                let _ = PdhAddEnglishCounterW(pdh_q, p_freq.as_ptr(), 0, &mut cnt_base_mhz);
                let _ = PdhAddEnglishCounterW(pdh_q, p_perf.as_ptr(), 0, &mut cnt_perf_pct);
                let _ = PdhCollectQueryData(pdh_q);
            }
        }

        let mut eng = Self {
            specs,
            num_logical,
            prev_times: Vec::new(),
            ring0: None,
            ring0_attempted: false,
            pdh_q,
            cnt_base_mhz,
            cnt_perf_pct,
        };
        eng.prev_times = eng.query_logical_times();
        eng
    }

    fn discover_topology() -> (Vec<PhysicalCoreSpec>, usize) {
        let mut raw_cores: Vec<(u8, u8, Vec<usize>)> = Vec::new();
        let mut max_lp = 0usize;

        unsafe {
            // RelationProcessorCore = 0
            let mut len = 0u32;
            let _ = GetLogicalProcessorInformationEx(0, std::ptr::null_mut(), &mut len);
            if len > 0 {
                let mut buf = vec![0u8; len as usize];
                if GetLogicalProcessorInformationEx(0, buf.as_mut_ptr(), &mut len) != 0 {
                    let mut off = 0usize;
                    while off + 40 <= len as usize {
                        let size = u32::from_le_bytes([buf[off + 4], buf[off + 5], buf[off + 6], buf[off + 7]]) as usize;
                        if size == 0 {
                            break;
                        }
                        let flags = buf[off + 8];
                        let eff_class = buf[off + 9];
                        let mask = u64::from_le_bytes([
                            buf[off + 32], buf[off + 33], buf[off + 34], buf[off + 35],
                            buf[off + 36], buf[off + 37], buf[off + 38], buf[off + 39],
                        ]);
                        let mut lps = Vec::new();
                        for bit in 0..64 {
                            if ((mask >> bit) & 1) != 0 {
                                lps.push(bit);
                                max_lp = max_lp.max(bit + 1);
                            }
                        }
                        if !lps.is_empty() {
                            raw_cores.push((eff_class, flags, lps));
                        }
                        off += size;
                    }
                }
            }
        }

        let mut p_group = Vec::new();
        let mut e_candidates = Vec::new();
        for (eff, smt, lps) in raw_cores {
            if eff > 0 || smt > 0 || lps.len() >= 2 {
                p_group.push(lps);
            } else {
                e_candidates.push(lps);
            }
        }

        let (e_group, lp_group) = if p_group.len() == 6 && e_candidates.len() == 10 {
            (e_candidates[..8].to_vec(), e_candidates[8..].to_vec())
        } else {
            (e_candidates, Vec::new())
        };

        let mut specs = Vec::new();
        for (i, lps) in p_group.into_iter().enumerate() {
            specs.push(PhysicalCoreSpec {
                label: format!("P{}", i + 1),
                kind: CoreKind::Performance,
                logical_cpus: lps,
            });
        }
        for (i, lps) in e_group.into_iter().enumerate() {
            specs.push(PhysicalCoreSpec {
                label: format!("E{}", i + 1),
                kind: CoreKind::Efficient,
                logical_cpus: lps,
            });
        }
        for (i, lps) in lp_group.into_iter().enumerate() {
            specs.push(PhysicalCoreSpec {
                label: format!("LP{}", i + 1),
                kind: CoreKind::LowPower,
                logical_cpus: lps,
            });
        }

        (specs, max_lp.max(1))
    }

    fn query_logical_times(&self) -> Vec<SystemProcessorPerformanceInformation> {
        let count = self.num_logical.max(64);
        let mut arr = vec![SystemProcessorPerformanceInformation::default(); count];
        let byte_len = (count * std::mem::size_of::<SystemProcessorPerformanceInformation>()) as u32;
        let mut ret_len = 0u32;
        unsafe {
            // SystemProcessorPerformanceInformation = 8
            if NtQuerySystemInformation(8, arr.as_mut_ptr() as *mut u8, byte_len, &mut ret_len) == 0 {
                let actual = (ret_len as usize) / std::mem::size_of::<SystemProcessorPerformanceInformation>();
                arr.truncate(actual);
                return arr;
            }
        }
        Vec::new()
    }

    unsafe fn read_pdh_lp_map(counter: isize) -> std::collections::HashMap<usize, f64> {
        let mut map = std::collections::HashMap::new();
        if counter == 0 {
            return map;
        }
        let mut bs = 0u32;
        let mut ic = 0u32;
        let _ = PdhGetFormattedCounterArrayW(counter, 0x0000_0200, &mut bs, &mut ic, std::ptr::null_mut());
        if bs == 0 || ic == 0 {
            return map;
        }
        let mut buf = vec![0u8; bs as usize];
        if PdhGetFormattedCounterArrayW(counter, 0x0000_0200, &mut bs, &mut ic, buf.as_mut_ptr()) != 0 {
            return map;
        }
        let items = std::slice::from_raw_parts(buf.as_ptr() as *const PdhItemW, ic as usize);
        for it in items {
            if it.sz_name.is_null() {
                continue;
            }
            let mut l = 0usize;
            while *it.sz_name.add(l) != 0 {
                l += 1;
            }
            let s = String::from_utf16_lossy(std::slice::from_raw_parts(it.sz_name, l));
            if let Some(rest) = s.strip_prefix("0,") {
                if let Ok(lp_idx) = rest.trim().parse::<usize>() {
                    map.insert(lp_idx, it.double_value);
                }
            }
        }
        map
    }

    pub fn poll(&mut self, ec_cpu_temp: Option<i32>, full_detail: bool) -> CpuTelemetry {
        if !self.ring0_attempted {
            self.ring0_attempted = true;
            self.ring0 = Ring0Driver::try_open();
        }

        if !full_detail {
            let pkg_temp_msr = self.ring0.as_ref().and_then(|r| r.read_package_temp());
            let avg_t = ec_cpu_temp.map(|v| v as f64).or(pkg_temp_msr);
            let max_t = pkg_temp_msr.or(avg_t);
            return CpuTelemetry {
                msr_active: self.ring0.is_some(),
                avg_temp_c: avg_t,
                max_temp_c: max_t,
                pkg_temp_c: pkg_temp_msr.or(avg_t),
                power_w: None,
                total_load_pct: 0.0,
                cores: self
                    .specs
                    .iter()
                    .map(|s| CoreInfo {
                        label: s.label.clone(),
                        kind: s.kind,
                        logical_cpus: s.logical_cpus.clone(),
                        temp_c: avg_t,
                        load_pct: 0.0,
                        mhz: 0,
                    })
                    .collect(),
            };
        }

        // 1. Per-logical-CPU utilization via single NtQuerySystemInformation syscall (0.005 ms)
        let cur_times = self.query_logical_times();
        let mut lp_loads = vec![0.0f64; cur_times.len().max(self.num_logical)];
        if cur_times.len() == self.prev_times.len() && !cur_times.is_empty() {
            for i in 0..cur_times.len() {
                let d_idle = (cur_times[i].idle_time - self.prev_times[i].idle_time).max(0) as f64;
                let d_kern = (cur_times[i].kernel_time - self.prev_times[i].kernel_time).max(0) as f64;
                let d_user = (cur_times[i].user_time - self.prev_times[i].user_time).max(0) as f64;
                let total = d_kern + d_user; // Note: in NT kernel_time includes idle_time!
                if total > 0.0 {
                    let busy = (total - d_idle).max(0.0);
                    lp_loads[i] = ((busy * 100.0) / total).clamp(0.0, 100.0);
                }
            }
        }
        self.prev_times = cur_times;

        // 2. Effective real-time Turbo MHz via PDH
        let (base_map, perf_map) = unsafe {
            if self.pdh_q != 0 {
                let _ = PdhCollectQueryData(self.pdh_q);
                (
                    Self::read_pdh_lp_map(self.cnt_base_mhz),
                    Self::read_pdh_lp_map(self.cnt_perf_pct),
                )
            } else {
                (Default::default(), Default::default())
            }
        };

        // 3. Per-core DTS temperatures & RAPL package power via Ring0 MSR
        let pkg_temp_msr = self.ring0.as_ref().and_then(|r| r.read_package_temp());
        let power_w = self.ring0.as_mut().and_then(|r| r.read_package_power_w());

        let mut cores_out = Vec::with_capacity(self.specs.len());
        let mut valid_core_temps = Vec::with_capacity(self.specs.len());

        for spec in &self.specs {
            let first_lp = spec.logical_cpus[0];
            let load_sum: f64 = spec.logical_cpus.iter().map(|&lp| *lp_loads.get(lp).unwrap_or(&0.0)).sum();
            let core_load = ((load_sum / (spec.logical_cpus.len() as f64)) * 10.0).round() / 10.0;

            let default_base = match spec.kind {
                CoreKind::Performance => 1400.0,
                CoreKind::Efficient => 900.0,
                CoreKind::LowPower => 700.0,
            };
            let base_mhz = *base_map.get(&first_lp).unwrap_or(&default_base);
            let perf_pct = *perf_map.get(&first_lp).unwrap_or(&100.0);
            let eff_mhz = ((base_mhz * (perf_pct / 100.0)).round() as u32).clamp(400, 6200);

            let msr_temp = self.ring0.as_ref().and_then(|r| r.read_core_temp(first_lp));
            if let Some(t) = msr_temp {
                valid_core_temps.push(t);
            }

            let display_temp = msr_temp.or_else(|| {
                ec_cpu_temp.map(|ec| ((ec as f64) + core_load * 0.04).round())
            });

            cores_out.push(CoreInfo {
                label: spec.label.clone(),
                kind: spec.kind,
                logical_cpus: spec.logical_cpus.clone(),
                temp_c: display_temp,
                load_pct: core_load,
                mhz: eff_mhz,
            });
        }

        let msr_active = !valid_core_temps.is_empty();
        let (avg_temp_c, max_temp_c) = if msr_active {
            let sum: f64 = valid_core_temps.iter().sum();
            let avg = ((sum / (valid_core_temps.len() as f64)) * 10.0).round() / 10.0;
            let max = valid_core_temps
                .iter()
                .copied()
                .fold(f64::NEG_INFINITY, f64::max);
            (Some(avg), Some(max))
        } else if let Some(ec) = ec_cpu_temp {
            (Some(ec as f64), Some(ec as f64))
        } else {
            (None, None)
        };

        let total_load_pct = if !cores_out.is_empty() {
            let s: f64 = cores_out.iter().map(|c| c.load_pct).sum();
            ((s / (cores_out.len() as f64)) * 10.0).round() / 10.0
        } else {
            0.0
        };

        CpuTelemetry {
            msr_active,
            avg_temp_c,
            max_temp_c,
            pkg_temp_c: pkg_temp_msr.or_else(|| ec_cpu_temp.map(|v| v as f64)),
            power_w,
            total_load_pct,
            cores: cores_out,
        }
    }
}

impl Drop for CpuTelemetryEngine {
    fn drop(&mut self) {
        if self.pdh_q != 0 {
            unsafe {
                let _ = PdhCloseQuery(self.pdh_q);
            }
            self.pdh_q = 0;
        }
    }
}
