#![cfg_attr(not(test), windows_subsystem = "windows")]

mod config;
mod cpu_msr;
mod gpu_rtd3;
mod regulator;
mod ui_win32;
mod wmi_ec;

use crate::config::{app_dir, default_curve, ConfigManager};
use crate::cpu_msr::CpuTelemetryEngine;
use crate::gpu_rtd3::{DgpuState, NvmlHardware, OptimusRtd3Detector, POWER_DEVICE_D3};
use crate::regulator::NativeSmoothRegulator;
use crate::ui_win32::{
    create_main_window_and_run, smoke_test_tray_icons, ControllerBridge, SharedTelemetry,
    WM_APP_IPC_MODE, WM_APP_QUIT_CLEAN, WM_APP_SHOW_WINDOW, WM_APP_TELEMETRY,
};
use crate::wmi_ec::LenovoWmiHardware;

use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use windows::core::PCWSTR;
use windows::Win32::Foundation::{
    CloseHandle, GetLastError, BOOL, ERROR_ALREADY_EXISTS, ERROR_PIPE_CONNECTED, HANDLE, HWND,
    INVALID_HANDLE_VALUE, LPARAM, WPARAM,
};
use windows::Win32::Security::{
    InitializeSecurityDescriptor, SetSecurityDescriptorDacl, PSECURITY_DESCRIPTOR,
    SECURITY_ATTRIBUTES, SECURITY_DESCRIPTOR,
};
use windows::Win32::Storage::FileSystem::{FlushFileBuffers, ReadFile, WriteFile, FILE_FLAGS_AND_ATTRIBUTES};
use windows::Win32::System::Console::SetConsoleCtrlHandler;
use windows::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, NAMED_PIPE_MODE,
    PIPE_READMODE_BYTE, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT,
};
use windows::Win32::System::ProcessStatus::{K32GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS};
use windows::Win32::System::SystemInformation::GetLocalTime;
use windows::Win32::System::Threading::{CreateMutexW, GetCurrentProcess, GetCurrentProcessId};
use windows::Win32::UI::Shell::{IsUserAnAdmin, ShellExecuteW};
use windows::Win32::UI::WindowsAndMessaging::{
    IsWindowVisible, MessageBoxW, PostMessageW, MB_ICONWARNING, MB_OK, SW_SHOWNORMAL,
};

const PIPE_NAME: &str = r"\\.\pipe\ThinkBookFanControl_Rust_v2";
const LEGACY_PY_PIPE_NAME: &str = r"\\.\pipe\ThinkBookFanControl_IPC_2026";
const MUTEX_NAME: &str = "Local\\ThinkBookFanControlMutex_Rust_v2";
const LOG_MAX_BYTES: u64 = 1_048_576; // 1 MB bounded rotating log

static GLOBAL_RUNNING: AtomicBool = AtomicBool::new(true);

fn wide_null(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

pub fn log_line(msg: &str) {
    let st = unsafe { GetLocalTime() };
    let line = format!(
        "[{:04}-{:02}-{:02} {:02}:{:02}:{:02}] {}\r\n",
        st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, msg
    );
    let log_path = app_dir().join("fan_control.log");
    if let Ok(meta) = fs::metadata(&log_path) {
        if meta.len() > LOG_MAX_BYTES {
            let old = app_dir().join("fan_control.log.old");
            let _ = fs::remove_file(&old);
            let _ = fs::rename(&log_path, &old);
        }
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&log_path) {
        let _ = f.write_all(line.as_bytes());
    }
}

fn process_memory_mb() -> (f64, f64) {
    unsafe {
        let mut pmc = PROCESS_MEMORY_COUNTERS {
            cb: std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32,
            ..Default::default()
        };
        if K32GetProcessMemoryInfo(GetCurrentProcess(), &mut pmc, pmc.cb).as_bool() {
            let ws = ((pmc.WorkingSetSize as f64) / (1024.0 * 1024.0) * 100.0).round() / 100.0;
            let pf = ((pmc.PagefileUsage as f64) / (1024.0 * 1024.0) * 100.0).round() / 100.0;
            return (ws, pf);
        }
    }
    (0.0, 0.0)
}

#[derive(Serialize)]
struct SelfTestReport {
    pass: bool,
    version: &'static str,
    pid: u32,
    working_set_mb: f64,
    private_commit_mb: f64,
    config_ok: bool,
    regulator_ok: bool,
    tray_gdi_ok: bool,
    cpu_cores_detected: usize,
    cpu_p_cores: usize,
    cpu_e_cores: usize,
    cpu_lp_cores: usize,
    rtd3_power_state_before: Option<u32>,
    rtd3_power_state_after: Option<u32>,
    d3cold_preserved: bool,
}

fn run_self_test(out_path: Option<String>) -> i32 {
    let tmp_cfg_path = std::env::temp_dir().join("tbfc_rust_selftest_cfg.json");
    let _ = fs::remove_file(&tmp_cfg_path);
    let cfg_mgr = ConfigManager::new(Some(tmp_cfg_path.clone()));
    cfg_mgr.update(|c| c.target_temp = 64.0, true);
    let cfg_mgr2 = ConfigManager::new(Some(tmp_cfg_path.clone()));
    let config_ok = (cfg_mgr2.get().target_temp - 64.0).abs() < 0.01;
    let _ = fs::remove_file(&tmp_cfg_path);

    // Regulator verification
    let mut reg = NativeSmoothRegulator::new("target_65", 65.0, 3600, &default_curve(), "max", true);
    let mut rpm = 2100;
    for _ in 0..10 {
        rpm = reg.step(1.0, Some(42), Some(45), Some(0.0), Some(rpm), true).target_rpm;
    }
    let regulator_ok = rpm == 2100;

    // RTD3 non-waking verification
    let mut rtd3 = OptimusRtd3Detector::new();
    let p_before = rtd3.get_power_state();
    let _ = rtd3.get_wddm_activity();

    // CPU topology & non-elevated telemetry verification
    let mut cpu_eng = CpuTelemetryEngine::new();
    let cpu_tel = cpu_eng.poll(Some(44), true);
    let p_cnt = cpu_tel.cores.iter().filter(|c| c.kind == crate::cpu_msr::CoreKind::Performance).count();
    let e_cnt = cpu_tel.cores.iter().filter(|c| c.kind == crate::cpu_msr::CoreKind::Efficient).count();
    let lp_cnt = cpu_tel.cores.iter().filter(|c| c.kind == crate::cpu_msr::CoreKind::LowPower).count();

    let tray_gdi_ok = smoke_test_tray_icons();
    let p_after = rtd3.get_power_state();
    let d3cold_preserved = match (p_before, p_after) {
        (Some(POWER_DEVICE_D3), Some(after)) => after == POWER_DEVICE_D3,
        _ => true,
    };

    let (ws_mb, pf_mb) = process_memory_mb();
    let pass = config_ok
        && regulator_ok
        && tray_gdi_ok
        && !cpu_tel.cores.is_empty()
        && d3cold_preserved;

    let report = SelfTestReport {
        pass,
        version: env!("CARGO_PKG_VERSION"),
        pid: unsafe { GetCurrentProcessId() },
        working_set_mb: ws_mb,
        private_commit_mb: pf_mb,
        config_ok,
        regulator_ok,
        tray_gdi_ok,
        cpu_cores_detected: cpu_tel.cores.len(),
        cpu_p_cores: p_cnt,
        cpu_e_cores: e_cnt,
        cpu_lp_cores: lp_cnt,
        rtd3_power_state_before: p_before,
        rtd3_power_state_after: p_after,
        d3cold_preserved,
    };

    if let Ok(json) = serde_json::to_string_pretty(&report) {
        let dest = out_path
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| app_dir().join("self_test_result.json"));
        let _ = fs::write(&dest, &json);
        println!("{}", json);
    }

    if pass { 0 } else { 2 }
}

#[derive(Serialize)]
struct LiveStatusReport {
    pid: u32,
    version: &'static str,
    working_set_mb: f64,
    private_commit_mb: f64,
    window_visible: bool,
    wmi_ok: bool,
    msr_active: bool,
    mode: String,
    target_temp: f64,
    target_rpm: i32,
    fan1_rpm: Option<i32>,
    fan2_rpm: Option<i32>,
    ec_cpu_temp: Option<i32>,
    cpu_avg_temp: Option<f64>,
    cpu_max_temp: Option<f64>,
    cpu_pkg_temp: Option<f64>,
    cpu_power_w: Option<f64>,
    cpu_cores_count: usize,
    dgpu_state: &'static str,
    gpu_sleeping: bool,
    gpu_temp: Option<i32>,
    gpu_pwr: Option<f64>,
    safety_level: u8,
    mode_desc: String,
}

fn ipc_try_request(cmd: &str) -> Option<String> {
    let mut f = OpenOptions::new().read(true).write(true).open(PIPE_NAME).ok()?;
    let payload = format!("{}\n", cmd.trim());
    f.write_all(payload.as_bytes()).ok()?;
    let _ = f.flush();
    let mut resp = String::new();
    let _ = f.read_to_string(&mut resp);
    Some(resp)
}

fn start_ipc_server(bridge: Arc<ControllerBridge>, hwnd_holder: Arc<Mutex<Option<isize>>>) {
    std::thread::spawn(move || {
        let pipe_w = wide_null(PIPE_NAME);
        while bridge.running.load(Ordering::SeqCst) {
            unsafe {
                let mut sd = SECURITY_DESCRIPTOR::default();
                let p_sd = PSECURITY_DESCRIPTOR(&mut sd as *mut _ as *mut _);
                let _ = InitializeSecurityDescriptor(p_sd, 1);
                let _ = SetSecurityDescriptorDacl(p_sd, true, None, false);
                let sa = SECURITY_ATTRIBUTES {
                    nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
                    lpSecurityDescriptor: p_sd.0,
                    bInheritHandle: false.into(),
                };

                // PIPE_ACCESS_DUPLEX = 0x00000003
                let h_pipe = CreateNamedPipeW(
                    PCWSTR(pipe_w.as_ptr()),
                    FILE_FLAGS_AND_ATTRIBUTES(3),
                    NAMED_PIPE_MODE(
                        PIPE_TYPE_BYTE.0 | PIPE_READMODE_BYTE.0 | PIPE_WAIT.0 | PIPE_REJECT_REMOTE_CLIENTS.0,
                    ),
                    4,
                    8192,
                    1024,
                    500,
                    Some(&sa),
                );

                if h_pipe == INVALID_HANDLE_VALUE || h_pipe.is_invalid() {
                    std::thread::sleep(Duration::from_millis(500));
                    continue;
                }

                let connected = match ConnectNamedPipe(h_pipe, None) {
                    Ok(()) => true,
                    Err(_) => GetLastError() == ERROR_PIPE_CONNECTED,
                };

                if connected {
                    let mut buf = [0u8; 256];
                    let mut n_read = 0u32;
                    let _ = ReadFile(h_pipe, Some(&mut buf), Some(&mut n_read), None);
                    let req = String::from_utf8_lossy(&buf[..n_read as usize]).trim().to_string();

                    let hwnd_opt = hwnd_holder.lock().ok().and_then(|g| *g).map(|v| HWND(v as *mut _));
                    let reply = if req == "PING" {
                        "OK\n".to_string()
                    } else if req == "SHOW" {
                        if let Some(hw) = hwnd_opt {
                            let _ = PostMessageW(hw, WM_APP_SHOW_WINDOW, WPARAM(0), LPARAM(0));
                        }
                        "OK\n".to_string()
                    } else if req == "QUIT" {
                        if let Some(hw) = hwnd_opt {
                            let _ = PostMessageW(hw, WM_APP_QUIT_CLEAN, WPARAM(0), LPARAM(0));
                        } else {
                            bridge.running.store(false, Ordering::SeqCst);
                            bridge.notify_wake();
                        }
                        "OK\n".to_string()
                    } else if let Some(m) = req.strip_prefix("MODE:") {
                        let m_idx = match m.trim() {
                            "target_65" => Some(0usize),
                            "curve" => Some(1usize),
                            "manual" => Some(2usize),
                            "auto" => Some(3usize),
                            "boost" => Some(4usize),
                            _ => None,
                        };
                        if let (Some(idx), Some(hw)) = (m_idx, hwnd_opt) {
                            let _ = PostMessageW(hw, WM_APP_IPC_MODE, WPARAM(idx), LPARAM(0));
                            "OK\n".to_string()
                        } else {
                            "ERR:UNKNOWN_MODE\n".to_string()
                        }
                    } else if req == "STATUS" {
                        let (ws_mb, pf_mb) = process_memory_mb();
                        let vis = hwnd_opt.map(|h| IsWindowVisible(h).as_bool()).unwrap_or(false);
                        let cfg = bridge.config.get();
                        let tel = bridge.latest_telemetry.lock().ok().and_then(|g| g.clone());
                        if let Some(t) = tel {
                            let dgpu_s = match t.dgpu_state {
                                DgpuState::D3Cold => "D3cold",
                                DgpuState::D0Idle => "D0_Idle",
                                DgpuState::D0Active => "D0_Active",
                            };
                            let rep = LiveStatusReport {
                                pid: GetCurrentProcessId(),
                                version: env!("CARGO_PKG_VERSION"),
                                working_set_mb: ws_mb,
                                private_commit_mb: pf_mb,
                                window_visible: vis,
                                wmi_ok: t.wmi_ok,
                                msr_active: t.cpu.msr_active,
                                mode: t.mode,
                                target_temp: cfg.target_temp,
                                target_rpm: t.target_rpm,
                                fan1_rpm: t.fan1_rpm,
                                fan2_rpm: t.fan2_rpm,
                                ec_cpu_temp: t.ec_cpu_temp,
                                cpu_avg_temp: t.cpu.avg_temp_c,
                                cpu_max_temp: t.cpu.max_temp_c,
                                cpu_pkg_temp: t.cpu.pkg_temp_c,
                                cpu_power_w: t.cpu.power_w,
                                cpu_cores_count: t.cpu.cores.len(),
                                dgpu_state: dgpu_s,
                                gpu_sleeping: t.gpu_sleeping,
                                gpu_temp: t.gpu_temp,
                                gpu_pwr: t.gpu_pwr,
                                safety_level: t.safety_level,
                                mode_desc: t.mode_desc,
                            };
                            serde_json::to_string_pretty(&rep).unwrap_or_else(|_| "{}".into())
                        } else {
                            "{\"status\":\"initializing\"}".to_string()
                        }
                    } else {
                        "ERR\n".to_string()
                    };

                    let mut n_written = 0u32;
                    let _ = WriteFile(h_pipe, Some(reply.as_bytes()), Some(&mut n_written), None);
                    let _ = FlushFileBuffers(h_pipe);
                    let _ = DisconnectNamedPipe(h_pipe);
                }

                let _ = CloseHandle(h_pipe);
            }
        }
    });
}

unsafe extern "system" fn console_ctrl_handler(_ctrl_type: u32) -> BOOL {
    GLOBAL_RUNNING.store(false, Ordering::SeqCst);
    let mut wmi = LenovoWmiHardware::new();
    let _ = wmi.reset_to_auto();
    true.into()
}

fn spawn_controller_thread(
    bridge: Arc<ControllerBridge>,
    hwnd_holder: Arc<Mutex<Option<isize>>>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let init_cfg = bridge.config.get();
        let mut wmi = LenovoWmiHardware::new();
        let mut nvml = NvmlHardware::new();
        let mut cpu_engine = CpuTelemetryEngine::new();
        let mut reg = NativeSmoothRegulator::new(
            &init_cfg.mode,
            init_cfg.target_temp,
            init_cfg.manual_rpm,
            &init_cfg.curve_points,
            &init_cfg.curve_sensor,
            init_cfg.safety_override,
        );

        let mut prev_tick = Instant::now();
        let mut handshake_logged = false;
        let mut auto_applied_once = false;
        let mut last_applied_rpm: Option<i32> = None;
        let mut last_applied_boost = false;
        let mut last_hw_write: Option<Instant> = None;
        let mut startup_full_polls = 2u8;

        log_line(&format!(
            "ThinkBook Fan Control v{} (Native Rust) controller started [PID={}, mode={}, target={:.0}°C].",
            env!("CARGO_PKG_VERSION"),
            unsafe { GetCurrentProcessId() },
            init_cfg.mode,
            init_cfg.target_temp
        ));

        while bridge.running.load(Ordering::SeqCst) && GLOBAL_RUNNING.load(Ordering::SeqCst) {
            let now = Instant::now();
            let dt = now.duration_since(prev_tick).as_secs_f64();
            prev_tick = now;

            if dt > 5.0 {
                log_line(&format!(
                    "System sleep/standby resume detected (gap={:.1}s) — resetting regulator filters.",
                    dt
                ));
                reg.reset_on_resume();
                auto_applied_once = false;
                last_hw_write = None;
            }

            let cfg = bridge.config.get();
            reg.set_mode(&cfg.mode);
            reg.set_target_temp(cfg.target_temp);
            reg.set_manual_rpm(cfg.manual_rpm);
            reg.set_curve_points(&cfg.curve_points);
            reg.set_curve_sensor(&cfg.curve_sensor);
            reg.set_safety_enabled(cfg.safety_override);

            let hwnd_opt = hwnd_holder.lock().ok().and_then(|g| *g).map(|v| HWND(v as *mut _));
            let win_visible = unsafe { hwnd_opt.map(|h| IsWindowVisible(h).as_bool()).unwrap_or(false) };
            let full_cpu_poll = win_visible || startup_full_polls > 0;
            if startup_full_polls > 0 {
                startup_full_polls -= 1;
            }

            let gpu = nvml.poll_rtd3_safe(&mut |m| log_line(m));
            let ec_cpu_temp = wmi.get_cpu_temp();
            let fan1 = wmi.get_fan1_rpm();
            let fan2 = wmi.get_fan2_rpm();
            let wmi_ok = wmi.is_connected() && (ec_cpu_temp.is_some() || fan1.is_some() || fan2.is_some());

            let cpu = cpu_engine.poll(ec_cpu_temp, full_cpu_poll);

            if !handshake_logged && wmi_ok {
                handshake_logged = true;
                log_line(&format!(
                    "Hardware handshake OK: EC_CPU={:?}°C, MSR_Active={}, Cores={}, Fan1={:?} RPM, Fan2={:?} RPM, dGPU={:?}",
                    ec_cpu_temp,
                    cpu.msr_active,
                    cpu.cores.len(),
                    fan1,
                    fan2,
                    gpu.state
                ));
            }

            let cpu_for_reg = ec_cpu_temp
                .or_else(|| cpu.avg_temp_c.map(|v| v.round() as i32))
                .or_else(|| cpu.pkg_temp_c.map(|v| v.round() as i32));

            let actual_fan = match (fan1, fan2) {
                (Some(a), Some(b)) => Some((a + b) / 2),
                (Some(a), None) => Some(a),
                (None, Some(b)) => Some(b),
                (None, None) => None,
            };

            let step = reg.step(dt, gpu.temp_c, cpu_for_reg, gpu.power_w, actual_fan, gpu.sleeping);

            // Apply EC WMI fan command cleanly (no BIOS Auto spam, 3.5s watchdog refresh in active modes)
            if wmi_ok {
                if cfg.mode == "auto" && step.safety_level == 0 {
                    if !auto_applied_once {
                        let _ = wmi.reset_to_auto();
                        auto_applied_once = true;
                        last_applied_rpm = Some(0);
                        last_applied_boost = false;
                        last_hw_write = Some(Instant::now());
                        log_line("Lenovo EC fans handed over to BIOS Auto (single-shot reset).");
                    }
                } else {
                    auto_applied_once = false;
                    let watchdog_due = last_hw_write
                        .map(|t| t.elapsed().as_secs_f64() >= 3.5)
                        .unwrap_or(true);
                    let need_write = watchdog_due
                        || last_applied_boost != step.is_boost
                        || last_applied_rpm != Some(step.target_rpm);

                    if need_write {
                        if step.is_boost {
                            let _ = wmi.set_boost(true, step.target_rpm);
                        } else {
                            if last_applied_boost {
                                let _ = wmi.clear_boost();
                            }
                            let _ = wmi.set_both_fans(step.target_rpm);
                        }
                        last_applied_rpm = Some(step.target_rpm);
                        last_applied_boost = step.is_boost;
                        last_hw_write = Some(Instant::now());
                    }
                }
            }

            let shared = SharedTelemetry {
                dgpu_state: gpu.state,
                gpu_sleeping: gpu.sleeping,
                gpu_temp: gpu.temp_c,
                gpu_pwr: gpu.power_w,
                gpu_util: gpu.gpu_util,
                mem_util: gpu.mem_util,
                ec_cpu_temp,
                cpu,
                fan1_rpm: fan1,
                fan2_rpm: fan2,
                mode: cfg.mode.clone(),
                target_rpm: step.target_rpm,
                curve_ref_temp: step.curve_ref_temp,
                safety_level: step.safety_level,
                wmi_ok,
                mode_desc: step.mode_desc,
            };

            if let Ok(mut g) = bridge.latest_telemetry.lock() {
                *g = Some(shared);
            }
            if let Some(hw) = hwnd_opt {
                unsafe {
                    let _ = PostMessageW(hw, WM_APP_TELEMETRY, WPARAM(0), LPARAM(0));
                }
            }

            let wait_dur = Duration::from_secs_f64(cfg.poll_interval_sec.clamp(0.5, 2.0));
            if let Ok(mut guard) = bridge.wake_pair.0.lock() {
                if !*guard && bridge.running.load(Ordering::SeqCst) {
                    if let Ok((mut g2, _)) = bridge.wake_pair.1.wait_timeout(guard, wait_dur) {
                        *g2 = false;
                    }
                } else {
                    *guard = false;
                }
            }
        }

        let ok = wmi.reset_to_auto();
        log_line(&format!(
            "Controller stopped — Lenovo EC fans reset to BIOS Auto (ok={}).",
            ok
        ));
    })
}

fn relaunch_as_admin(raw_args: &[String]) -> i32 {
    let Ok(exe) = std::env::current_exe() else { return 1 };
    let wd = app_dir();
    let joined_args = raw_args
        .iter()
        .map(|a| {
            if a.contains(' ') {
                format!("\"{}\"", a.replace('"', "\\\""))
            } else {
                a.clone()
            }
        })
        .collect::<Vec<_>>()
        .join(" ");

    let verb_w = wide_null("runas");
    let exe_w = wide_null(&exe.to_string_lossy());
    let args_w = wide_null(&joined_args);
    let wd_w = wide_null(&wd.to_string_lossy());

    let res = unsafe {
        ShellExecuteW(
            HWND::default(),
            PCWSTR(verb_w.as_ptr()),
            PCWSTR(exe_w.as_ptr()),
            if joined_args.is_empty() {
                PCWSTR::null()
            } else {
                PCWSTR(args_w.as_ptr())
            },
            PCWSTR(wd_w.as_ptr()),
            SW_SHOWNORMAL,
        )
    };

    if (res.0 as isize) <= 32 {
        unsafe {
            let msg = wide_null(
                "Для управления вентиляторами Lenovo ThinkBook (интерфейс ACPI/WMI LENOVO_OTHER_METHOD и чтение ядер MSR) необходимы права Администратора.",
            );
            let cap = wide_null("ThinkBook Fan Control");
            let _ = MessageBoxW(
                HWND::default(),
                PCWSTR(msg.as_ptr()),
                PCWSTR(cap.as_ptr()),
                MB_OK | MB_ICONWARNING,
            );
        }
        1
    } else {
        0
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    let mut self_test = false;
    let mut self_test_out: Option<String> = None;
    let mut want_status = false;
    let mut status_out: Option<String> = None;
    let mut want_quit = false;
    let mut cli_mode: Option<String> = None;
    let mut force_minimized = false;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--self-test" => self_test = true,
            "--self-test-out" if i + 1 < args.len() => {
                self_test_out = Some(args[i + 1].clone());
                i += 1;
            }
            "--status" => want_status = true,
            "--status-out" if i + 1 < args.len() => {
                status_out = Some(args[i + 1].clone());
                i += 1;
            }
            "--quit" | "--stop" => want_quit = true,
            "--minimized" | "--silent" => force_minimized = true,
            "--mode" if i + 1 < args.len() => {
                cli_mode = Some(args[i + 1].clone());
                i += 1;
            }
            _ => {}
        }
        i += 1;
    }

    if self_test {
        std::process::exit(run_self_test(self_test_out));
    }

    // 1. Check IPC to already-running instance BEFORE requesting UAC elevation!
    if want_status {
        if let Some(resp) = ipc_try_request("STATUS") {
            if let Some(p) = status_out {
                let _ = fs::write(p, &resp);
            }
            println!("{}", resp);
            std::process::exit(0);
        } else {
            if let Some(p) = status_out {
                let _ = fs::write(p, "{\"running\":false}");
            }
            std::process::exit(3);
        }
    }

    if want_quit {
        if ipc_try_request("QUIT").is_some() {
            std::process::exit(0);
        }
        std::process::exit(0);
    }

    if let Some(ref m) = cli_mode {
        if ipc_try_request(&format!("MODE:{}", m)).is_some() {
            std::process::exit(0);
        }
    }

    let launch_ipc_cmd = if force_minimized { "PING" } else { "SHOW" };
    if ipc_try_request(launch_ipc_cmd).is_some() {
        std::process::exit(0);
    }

    // 2. Not running yet -> ensure Administrator privileges for Lenovo EC WMI & MSR
    let is_admin = unsafe { IsUserAnAdmin().as_bool() };
    if !is_admin {
        std::process::exit(relaunch_as_admin(&args));
    }

    // 3. Single-instance mutex guard
    let mtx_w = wide_null(MUTEX_NAME);
    let h_mutex: HANDLE = unsafe {
        CreateMutexW(None, false, PCWSTR(mtx_w.as_ptr())).unwrap_or_default()
    };
    if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
        let _ = ipc_try_request(launch_ipc_cmd);
        std::process::exit(0);
    }

    // 3b. Gracefully stop legacy Python instance if still running on old QLocalServer pipe
    if let Ok(mut old_pipe) = OpenOptions::new().read(true).write(true).open(LEGACY_PY_PIPE_NAME) {
        log_line("Detected running legacy Python instance — sending clean QUIT over QLocalServer pipe...");
        let _ = old_pipe.write_all(b"QUIT\n");
        let _ = old_pipe.flush();
        drop(old_pipe);
        std::thread::sleep(Duration::from_millis(850));
    }

    // 4. Safety hooks: always restore BIOS Auto on panic or console/shutdown signal
    let default_panic = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        log_line(&format!("PANIC intercepted — resetting EC fans to BIOS Auto: {}", info));
        let mut wmi = LenovoWmiHardware::new();
        let _ = wmi.reset_to_auto();
        default_panic(info);
    }));
    unsafe {
        let _ = SetConsoleCtrlHandler(Some(console_ctrl_handler), true);
    }

    let config = ConfigManager::new(None);
    if let Some(m) = cli_mode {
        config.update(|c| c.mode = m, true);
    }
    let start_min = force_minimized || config.get().start_minimized;

    let bridge = Arc::new(ControllerBridge {
        config,
        latest_telemetry: Mutex::new(None),
        wake_pair: (Mutex::new(false), Condvar::new()),
        running: AtomicBool::new(true),
    });

    let hwnd_holder: Arc<Mutex<Option<isize>>> = Arc::new(Mutex::new(None));
    start_ipc_server(bridge.clone(), hwnd_holder.clone());
    let ctrl_handle = spawn_controller_thread(bridge.clone(), hwnd_holder.clone());

    create_main_window_and_run(bridge.clone(), start_min, hwnd_holder);

    bridge.running.store(false, Ordering::SeqCst);
    bridge.notify_wake();
    let _ = ctrl_handle.join();

    if !h_mutex.is_invalid() {
        unsafe {
            let _ = CloseHandle(h_mutex);
        }
    }
}
