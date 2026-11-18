use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::os::windows::process::CommandExt;
use std::sync::{Arc, RwLock};

const CREATE_NO_WINDOW: u32 = 0x08000000;
pub const APP_NAME: &str = "ThinkBookFanControl";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CurvePoint {
    pub temp: i32,
    pub rpm: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    pub mode: String,
    pub target_temp: f64,
    pub manual_rpm: i32,
    pub curve_points: Vec<CurvePoint>,
    pub curve_sensor: String,
    pub safety_override: bool,
    pub minimize_to_tray_on_close: bool,
    pub start_minimized: bool,
    pub autostart: bool,
    pub poll_interval_sec: f64,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            mode: "target_65".to_string(),
            target_temp: 65.0,
            manual_rpm: 3600,
            curve_points: default_curve(),
            curve_sensor: "max".to_string(),
            safety_override: true,
            minimize_to_tray_on_close: true,
            start_minimized: false,
            autostart: true,
            poll_interval_sec: 1.0,
        }
    }
}

pub fn default_curve() -> Vec<CurvePoint> {
    vec![
        CurvePoint { temp: 45, rpm: 2200 },
        CurvePoint { temp: 55, rpm: 2800 },
        CurvePoint { temp: 62, rpm: 3400 },
        CurvePoint { temp: 68, rpm: 4000 },
        CurvePoint { temp: 75, rpm: 4800 },
        CurvePoint { temp: 82, rpm: 5600 },
    ]
}

pub fn curve_presets() -> Vec<(&'static str, Vec<CurvePoint>)> {
    vec![
        (
            "Тихий",
            vec![
                CurvePoint { temp: 45, rpm: 2100 },
                CurvePoint { temp: 55, rpm: 2500 },
                CurvePoint { temp: 65, rpm: 3200 },
                CurvePoint { temp: 75, rpm: 4000 },
                CurvePoint { temp: 85, rpm: 4800 },
            ],
        ),
        (
            "Сбаланс.",
            vec![
                CurvePoint { temp: 45, rpm: 2200 },
                CurvePoint { temp: 55, rpm: 2800 },
                CurvePoint { temp: 65, rpm: 3600 },
                CurvePoint { temp: 75, rpm: 4500 },
                CurvePoint { temp: 85, rpm: 5400 },
            ],
        ),
        (
            "Цель 65°C",
            vec![
                CurvePoint { temp: 45, rpm: 2200 },
                CurvePoint { temp: 55, rpm: 2900 },
                CurvePoint { temp: 62, rpm: 3500 },
                CurvePoint { temp: 65, rpm: 4050 },
                CurvePoint { temp: 72, rpm: 4800 },
                CurvePoint { temp: 80, rpm: 5600 },
            ],
        ),
        (
            "Макс. охл.",
            vec![
                CurvePoint { temp: 40, rpm: 3400 },
                CurvePoint { temp: 50, rpm: 4400 },
                CurvePoint { temp: 60, rpm: 5500 },
                CurvePoint { temp: 68, rpm: 6600 },
                CurvePoint { temp: 76, rpm: 7500 },
            ],
        ),
    ]
}

pub fn sanitize_curve(pts: &[CurvePoint]) -> Vec<CurvePoint> {
    if pts.is_empty() {
        return default_curve();
    }
    let mut cleaned: Vec<CurvePoint> = pts
        .iter()
        .map(|p| CurvePoint {
            temp: p.temp.clamp(25, 95),
            rpm: p.rpm.clamp(2000, 7700),
        })
        .collect();
    cleaned.sort_by_key(|p| p.temp);
    cleaned
}

pub fn app_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."))
}

#[derive(Clone)]
pub struct ConfigManager {
    path: PathBuf,
    inner: Arc<RwLock<AppConfig>>,
}

impl ConfigManager {
    pub fn new(custom_path: Option<PathBuf>) -> Self {
        let path = custom_path.unwrap_or_else(|| app_dir().join("config.json"));
        let mgr = Self {
            path,
            inner: Arc::new(RwLock::new(AppConfig::default())),
        };
        mgr.load();
        mgr
    }

    pub fn load(&self) {
        if !self.path.exists() {
            self.save();
            return;
        }
        if let Ok(raw) = fs::read_to_string(&self.path) {
            if let Ok(mut parsed) = serde_json::from_str::<AppConfig>(&raw) {
                parsed.curve_points = sanitize_curve(&parsed.curve_points);
                parsed.poll_interval_sec = parsed.poll_interval_sec.clamp(0.5, 2.0);
                if let Ok(mut guard) = self.inner.write() {
                    *guard = parsed;
                }
            }
        }
    }

    pub fn save(&self) {
        let snapshot = match self.inner.read() {
            Ok(g) => g.clone(),
            Err(_) => return,
        };
        if let Some(parent) = self.path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let tmp = self.path.with_extension("json.tmp");
        if let Ok(json) = serde_json::to_string_pretty(&snapshot) {
            if fs::write(&tmp, json).is_ok() {
                let _ = fs::rename(&tmp, &self.path);
            }
        }
    }

    pub fn get(&self) -> AppConfig {
        self.inner.read().map(|g| g.clone()).unwrap_or_default()
    }

    pub fn update<F: FnOnce(&mut AppConfig)>(&self, f: F, persist: bool) {
        if let Ok(mut g) = self.inner.write() {
            f(&mut g);
            g.curve_points = sanitize_curve(&g.curve_points);
        }
        if persist {
            self.save();
        }
    }

    #[allow(dead_code)]
    pub fn is_autostart_enabled(&self) -> bool {
        Command::new("schtasks.exe")
            .args(["/Query", "/TN", APP_NAME])
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    pub fn set_autostart(&self, enable: bool) -> bool {
        let exe = match std::env::current_exe() {
            Ok(p) => p,
            Err(_) => return false,
        };
        let wd = app_dir();

        let ok = if enable {
            let ps_exe = exe.to_string_lossy().replace('\'', "''");
            let ps_wd = wd.to_string_lossy().replace('\'', "''");
            let cmd = format!(
                "$act = New-ScheduledTaskAction -Execute '{}' -Argument '--minimized' -WorkingDirectory '{}'; \
                 $trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; \
                 $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries \
                 -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; \
                 $prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest; \
                 Register-ScheduledTask -TaskName '{}' -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null; \
                 Remove-ItemProperty -LiteralPath 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run' -Name '{}' -ErrorAction SilentlyContinue",
                ps_exe, ps_wd, APP_NAME, APP_NAME
            );
            Command::new("powershell.exe")
                .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &cmd])
                .creation_flags(CREATE_NO_WINDOW)
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
        } else {
            let _ = Command::new("schtasks.exe")
                .args(["/Delete", "/TN", APP_NAME, "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .status();
            false
        };

        self.update(|c| c.autostart = enable && ok, true);
        if enable { ok } else { true }
    }
}

#[allow(dead_code)]
pub fn check_path_exists(p: &Path) -> bool {
    p.exists()
}
