"""
Configuration manager for ThinkBookFanControl.
Handles thread-safe, atomic JSON persistence and Windows 11 elevated logon autostart.
"""

import copy
import json
import os
import sys
import logging
import subprocess
import threading
import winreg

logger = logging.getLogger("ThinkBookConfig")


def get_app_dir() -> str:
    """Returns application directory whether running as script or frozen PyInstaller exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(get_app_dir(), "config.json")
RUN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "ThinkBookFanControl"

DEFAULT_CURVE = [
    {"temp": 45, "rpm": 2200},
    {"temp": 55, "rpm": 2800},
    {"temp": 62, "rpm": 3400},
    {"temp": 68, "rpm": 4000},
    {"temp": 75, "rpm": 4800},
    {"temp": 82, "rpm": 5600},
]

PRESETS = {
    "Тихий (Quiet)": [
        {"temp": 45, "rpm": 2100},
        {"temp": 55, "rpm": 2500},
        {"temp": 65, "rpm": 3200},
        {"temp": 75, "rpm": 4000},
        {"temp": 85, "rpm": 4800},
    ],
    "Сбалансированный": [
        {"temp": 45, "rpm": 2200},
        {"temp": 55, "rpm": 2800},
        {"temp": 65, "rpm": 3600},
        {"temp": 75, "rpm": 4500},
        {"temp": 85, "rpm": 5400},
    ],
    "Удержание 65°C": [
        {"temp": 45, "rpm": 2200},
        {"temp": 55, "rpm": 2900},
        {"temp": 62, "rpm": 3500},
        {"temp": 65, "rpm": 4050},
        {"temp": 72, "rpm": 4800},
        {"temp": 80, "rpm": 5600},
    ],
    "Максимальное охлаждение": [
        {"temp": 40, "rpm": 3400},
        {"temp": 50, "rpm": 4400},
        {"temp": 60, "rpm": 5500},
        {"temp": 68, "rpm": 6600},
        {"temp": 76, "rpm": 7500},
    ],
}

DEFAULT_CONFIG = {
    "mode": "target_65",            # "target_65", "curve", "manual", "auto", "boost"
    "target_temp": 65,              # Target °C for closed-loop PID mode
    "manual_rpm": 3600,             # Manual RPM setting
    "curve_points": DEFAULT_CURVE,
    "curve_sensor": "max",          # "max", "gpu", "cpu"
    "safety_override": True,        # Progressive thermal protection
    "minimize_to_tray_on_close": True,
    "start_minimized": False,
    "autostart": False,
    "poll_interval_sec": 1.0,
}


def clone_curve_points(points: list[dict]) -> list[dict]:
    """Returns a sanitized, sorted deep copy of curve points."""
    if not points:
        return copy.deepcopy(DEFAULT_CURVE)
    cleaned = []
    for pt in points:
        if isinstance(pt, dict) and "temp" in pt and "rpm" in pt:
            cleaned.append({
                "temp": max(25, min(95, int(round(float(pt["temp"]))))),
                "rpm": max(2000, min(7700, int(round(float(pt["rpm"]))))),
            })
    return sorted(cleaned, key=lambda p: p["temp"]) if cleaned else copy.deepcopy(DEFAULT_CURVE)


class ConfigManager:
    """Manages application settings file and elevated Windows autostart registration."""

    def __init__(self, file_path=None):
        self.file_path = file_path if file_path else CONFIG_PATH
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self):
        with self._lock:
            if not os.path.exists(self.file_path):
                self.save()
                return
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    merged = copy.deepcopy(DEFAULT_CONFIG)
                    merged.update(loaded)
                    merged["curve_points"] = clone_curve_points(merged.get("curve_points", DEFAULT_CURVE))
                    self.data = merged
            except Exception as e:
                logger.warning(f"Failed to read config, using defaults: {e}")
                self.data = copy.deepcopy(DEFAULT_CONFIG)

    def save(self):
        with self._lock:
            tmp_path = f"{self.file_path}.tmp"
            try:
                parent = os.path.dirname(os.path.abspath(self.file_path))
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=4, ensure_ascii=False)
                os.replace(tmp_path, self.file_path)
            except Exception as e:
                logger.error(f"Failed to save config: {e}")
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

    def get(self, key, default=None):
        with self._lock:
            val = self.data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))
            return copy.deepcopy(val) if isinstance(val, (list, dict)) else val

    def set(self, key, value, persist: bool = True):
        with self._lock:
            if key == "curve_points":
                self.data[key] = clone_curve_points(value)
            else:
                self.data[key] = copy.deepcopy(value) if isinstance(value, (list, dict)) else value
            if persist:
                self.save()

    @staticmethod
    def _has_hkcu_run_entry() -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(val)
        except Exception:
            return False

    @staticmethod
    def _delete_hkcu_run_entry():
        """Removes legacy HKCU\\...\\Run entry (Windows blocks requireAdministrator EXEs there)."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except Exception:
            pass

    def ensure_autostart_migrated(self, is_admin: bool):
        """Automatically migrates legacy HKCU Run autostart to an elevated battery-safe Scheduled Task."""
        if not is_admin:
            return
        has_legacy_run = self._has_hkcu_run_entry()
        wants_autostart = bool(self.get("autostart", False)) or has_legacy_run
        if wants_autostart and (has_legacy_run or not self.is_autostart_enabled()):
            logger.info("Migrating/repairing elevated Scheduled Task autostart...")
            self.set_autostart(True)

    def set_autostart(self, enable: bool) -> bool:
        """Configures Windows 11 elevated logon autostart via Task Scheduler (AC + Battery safe, unlimited runtime)."""
        task_name = APP_NAME
        app_dir = get_app_dir()
        if getattr(sys, "frozen", False):
            exe_path = sys.executable
            arg_str = "--minimized"
        else:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            exe_path = pyw if os.path.exists(pyw) else sys.executable
            main_py = os.path.join(app_dir, "main.py")
            arg_str = f'"{main_py}" --minimized'

        no_window = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        try:
            if enable:
                # Register Scheduled Task with battery-safe settings and unlimited execution time
                ps_exe = exe_path.replace("'", "''")
                ps_args = arg_str.replace("'", "''")
                ps_wd = app_dir.replace("'", "''")
                ps_cmd = (
                    f"$act = New-ScheduledTaskAction -Execute '{ps_exe}' -Argument '{ps_args}' -WorkingDirectory '{ps_wd}'; "
                    f"$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; "
                    f"$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                    f"-ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; "
                    f"$prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest; "
                    f"Register-ScheduledTask -TaskName '{task_name}' -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null"
                )
                res = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                    capture_output=True,
                    text=True,
                    creationflags=no_window,
                )
                if res.returncode != 0:
                    logger.warning(f"Register-ScheduledTask returned {res.returncode}: {res.stderr.strip()}")
                    return False

                # Remove legacy HKCU Run entry so Windows Explorer does not show blocked UAC startup warning
                self._delete_hkcu_run_entry()
            else:
                subprocess.run(
                    ["schtasks", "/Delete", "/TN", task_name, "/F"],
                    capture_output=True,
                    creationflags=no_window,
                )
                self._delete_hkcu_run_entry()

            self.set("autostart", bool(enable))
            return True
        except Exception as e:
            logger.error(f"Failed to configure autostart: {e}")
            return False

    def is_autostart_enabled(self) -> bool:
        no_window = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            res = subprocess.run(
                ["schtasks", "/Query", "/TN", APP_NAME],
                capture_output=True,
                creationflags=no_window,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
        return False
