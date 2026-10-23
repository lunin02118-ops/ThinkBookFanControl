"""
Configuration manager for ThinkBookFanControl
Handles persistent JSON settings.
"""

import json
import os
import sys
import logging
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
    {"temp": 45, "rpm": 2400},
    {"temp": 55, "rpm": 3000},
    {"temp": 62, "rpm": 3600},
    {"temp": 65, "rpm": 4150},
    {"temp": 72, "rpm": 4600},
    {"temp": 80, "rpm": 5000},
]

PRESETS = {
    "Тихий (Quiet)": [
        {"temp": 45, "rpm": 2200},
        {"temp": 55, "rpm": 2600},
        {"temp": 65, "rpm": 3400},
        {"temp": 75, "rpm": 4200},
        {"temp": 85, "rpm": 4800},
    ],
    "Сбалансированный": [
        {"temp": 45, "rpm": 2400},
        {"temp": 55, "rpm": 3000},
        {"temp": 65, "rpm": 3900},
        {"temp": 75, "rpm": 4500},
        {"temp": 85, "rpm": 5000},
    ],
    "Майнинг (Цель 65°C)": [
        {"temp": 45, "rpm": 2500},
        {"temp": 55, "rpm": 3200},
        {"temp": 62, "rpm": 3800},
        {"temp": 65, "rpm": 4150},
        {"temp": 70, "rpm": 4600},
        {"temp": 78, "rpm": 5000},
    ],
    "Максимальное охлаждение": [
        {"temp": 40, "rpm": 3800},
        {"temp": 50, "rpm": 4600},
        {"temp": 60, "rpm": 5600},
        {"temp": 65, "rpm": 6600},
        {"temp": 75, "rpm": 7500},
    ],
}

DEFAULT_CONFIG = {
    "mode": "target_65",            # "target_65", "curve", "manual", "auto", "boost"
    "target_temp": 65,              # Target °C for mining mode
    "manual_rpm": 4150,             # Manual RPM setting
    "curve_points": DEFAULT_CURVE,
    "curve_sensor": "max",          # "max", "gpu", "cpu"
    "safety_override": True,        # Emergency fan boost if GPU >= 80°C or CPU >= 85°C
    "minimize_to_tray_on_close": True,
    "start_minimized": False,
    "autostart": False,
    "poll_interval_sec": 1.2,
}


class ConfigManager:
    """Manages application settings file and autostart registration."""

    def __init__(self, file_path=None):
        self.file_path = file_path if file_path else CONFIG_PATH
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if not os.path.exists(self.file_path):
            self.save()
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                self.data.update(loaded)
        except Exception as e:
            logger.warning(f"Failed to read config, using defaults: {e}")
            self.data = dict(DEFAULT_CONFIG)

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def get(self, key, default=None):
        return self.data.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def set_autostart(self, enable: bool) -> bool:
        """Configures Windows autostart on logon via Scheduled Task (highest privileges) and Registry Run key."""
        import subprocess

        task_name = "ThinkBookFanControl"
        if getattr(sys, "frozen", False):
            target = f'"{sys.executable}" --minimized'
        else:
            main_py = os.path.join(get_app_dir(), "main.py")
            target = f'"{sys.executable}" "{main_py}" --minimized'

        try:
            if enable:
                # 1. Scheduled Task for elevated launch on logon without UAC block
                subprocess.run(
                    ["schtasks", "/Create", "/TN", task_name, "/TR", target, "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
                # 2. Auxiliary Registry Run key
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, target)
                except Exception:
                    pass
            else:
                subprocess.run(
                    ["schtasks", "/Delete", "/TN", task_name, "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, APP_NAME)
                except Exception:
                    pass

            self.set("autostart", bool(enable))
            return True
        except Exception as e:
            logger.error(f"Failed to configure autostart: {e}")
            return False

    def is_autostart_enabled(self) -> bool:
        import subprocess
        task_name = "ThinkBookFanControl"
        try:
            res = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(val)
        except Exception:
            return bool(self.get("autostart", False))
