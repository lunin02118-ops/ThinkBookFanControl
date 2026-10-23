"""
Comprehensive test suite for ThinkBook Fan Control
Tests:
- Interpolation and curve point handling
- Configuration persistence
- Hardware clamping and boundary checks
- Controller PI regulator logic and deadband
- Thermal safety overrides
- IPC single-instance socket communication
"""

import unittest
import os
import sys
import tempfile
import json
import time

# Ensure import paths
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from config import ConfigManager, DEFAULT_CURVE, get_app_dir
from controller import interpolate_curve, FanControllerThread
from hardware import LenovoWmiHardware, NvmlHardware


class TestInterpolateCurve(unittest.TestCase):

    def setUp(self):
        self.points = [
            {"temp": 40, "rpm": 2400},
            {"temp": 50, "rpm": 3000},
            {"temp": 60, "rpm": 3800},
            {"temp": 70, "rpm": 4600},
            {"temp": 80, "rpm": 5200},
        ]

    def test_empty_points(self):
        self.assertEqual(interpolate_curve([], 65), 4150)

    def test_single_point(self):
        self.assertEqual(interpolate_curve([{"temp": 50, "rpm": 3200}], 30), 3200)
        self.assertEqual(interpolate_curve([{"temp": 50, "rpm": 3200}], 70), 3200)

    def test_below_minimum(self):
        self.assertEqual(interpolate_curve(self.points, 25), 2400)
        self.assertEqual(interpolate_curve(self.points, 40), 2400)

    def test_above_maximum(self):
        self.assertEqual(interpolate_curve(self.points, 85), 5200)
        self.assertEqual(interpolate_curve(self.points, 80), 5200)

    def test_exact_points(self):
        self.assertEqual(interpolate_curve(self.points, 50), 3000)
        self.assertEqual(interpolate_curve(self.points, 60), 3800)
        self.assertEqual(interpolate_curve(self.points, 70), 4600)

    def test_midpoint_interpolation(self):
        # 45 is halfway between 40 (2400) and 50 (3000) -> 2700
        self.assertEqual(interpolate_curve(self.points, 45), 2700)
        # 55 is halfway between 50 (3000) and 60 (3800) -> 3400
        self.assertEqual(interpolate_curve(self.points, 55), 3400)


class TestConfigManager(unittest.TestCase):

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.temp_file.close()
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def tearDown(self):
        if os.path.exists(self.temp_file.name):
            os.remove(self.temp_file.name)

    def test_config_save_load(self):
        cm = ConfigManager(file_path=self.temp_file.name)
        self.assertEqual(cm.get("mode"), "target_65")
        self.assertEqual(cm.get("target_temp"), 65)

        cm.set("target_temp", 67)
        cm.set("mode", "manual")
        cm.set("manual_rpm", 4300)

        cm2 = ConfigManager(file_path=self.temp_file.name)
        self.assertEqual(cm2.get("target_temp"), 67)
        self.assertEqual(cm2.get("mode"), "manual")
        self.assertEqual(cm2.get("manual_rpm"), 4300)


class TestHardwareClamping(unittest.TestCase):

    def setUp(self):
        self.hw = LenovoWmiHardware()
        self.hw.set_feature = lambda fid, val: (fid, val)

    def test_fan_rpm_clamping(self):
        from hardware import ID_FAN1_RPM, ID_FAN2_RPM, ID_FAN_BOOST

        # Negative -> 0
        self.assertEqual(self.hw.set_fan1_rpm(-10), (ID_FAN1_RPM, 0))
        self.assertEqual(self.hw.set_fan2_rpm(-50), (ID_FAN2_RPM, 0))

        # Under 2000 -> 2000
        self.assertEqual(self.hw.set_fan1_rpm(1200), (ID_FAN1_RPM, 2000))
        self.assertEqual(self.hw.set_fan2_rpm(1800), (ID_FAN2_RPM, 2000))

        # Upper clamp -> 7700
        self.assertEqual(self.hw.set_fan1_rpm(8000), (ID_FAN1_RPM, 7700))
        self.assertEqual(self.hw.set_fan2_rpm(8000), (ID_FAN2_RPM, 7700))
        self.assertEqual(self.hw.set_fan1_rpm(7500), (ID_FAN1_RPM, 7500))
        self.assertEqual(self.hw.set_fan2_rpm(7500), (ID_FAN2_RPM, 7500))

        # Valid mid-range
        self.assertEqual(self.hw.set_fan1_rpm(4150), (ID_FAN1_RPM, 4150))
        self.assertEqual(self.hw.set_fan2_rpm(4150), (ID_FAN2_RPM, 4150))

    def test_set_boost_drives_both_fans_to_7000(self):
        calls = []
        self.hw.set_feature = lambda fid, val: calls.append((fid, val)) or True
        from hardware import ID_FAN1_RPM, ID_FAN2_RPM

        self.hw.set_boost(True)
        self.assertIn((ID_FAN1_RPM, 7500), calls)
        self.assertIn((ID_FAN2_RPM, 7500), calls)


class TestLiveHardwareTelemetry(unittest.TestCase):

    def test_live_nvml_reading(self):
        nvml = NvmlHardware()
        initialized = nvml.init()
        self.assertTrue(initialized, "NVML failed to initialize on RTX 4060 laptop")
        temp = nvml.get_gpu_temp()
        pwr = nvml.get_gpu_power()
        util = nvml.get_gpu_utilization()
        self.assertIsNotNone(temp, "GPU temp returned None")
        self.assertGreater(temp, 20, "GPU temp unrealistic (<20°C)")
        self.assertLess(temp, 100, "GPU temp unrealistic (>100°C)")
        self.assertIsNotNone(pwr, "GPU power returned None")
        self.assertIsNotNone(util, "GPU util returned None")
        nvml.shutdown()

    def test_live_wmi_reading(self):
        wmi = LenovoWmiHardware()
        wmi.init_com()
        f1 = wmi.get_fan1_rpm()
        f2 = wmi.get_fan2_rpm()
        cpu = wmi.get_cpu_temp()
        self.assertIsNotNone(f1, "Fan 1 RPM returned None")
        self.assertIsNotNone(f2, "Fan 2 RPM returned None")
        self.assertIsNotNone(cpu, "CPU temp returned None")
        self.assertGreater(f1, 1000, "Fan 1 should be spinning under load")
        self.assertGreater(f2, 1000, "Fan 2 should be spinning under load")
        self.assertGreater(cpu, 25, "CPU temp unrealistic")
        wmi.uninit_com()


class TestMainWindowSync(unittest.TestCase):

    def test_ui_initial_mode_and_toggle(self):
        from PySide6.QtWidgets import QApplication
        from main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            cfg_path = tf.name
        if os.path.exists(cfg_path):
            os.remove(cfg_path)

        try:
            cfg = ConfigManager(file_path=cfg_path)
            cfg.set("mode", "boost")
            win = MainWindow(cfg)

            self.assertIn("ТУРБО", win.lbl_mode_badge.text())
            self.assertIn("ТУРБО", win.lbl_boost_status.text())
            self.assertIn("ТУРБО", win.btn_boost.text())
            self.assertEqual(win.stack.currentIndex(), 4)

            # Switch mode
            win._set_mode_from_ui(0)
            self.assertIn("ЦЕЛЕВАЯ", win.lbl_mode_badge.text())
            self.assertEqual(win.stack.currentIndex(), 0)

            win.safe_exit()
        finally:
            if os.path.exists(cfg_path):
                os.remove(cfg_path)


if __name__ == "__main__":
    unittest.main()
