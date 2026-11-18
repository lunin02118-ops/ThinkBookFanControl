"""
Comprehensive automated test suite for ThinkBook Fan Control.
Tests:
- Curve interpolation and edge cases
- Atomic, deep-copied configuration persistence
- Hardware RPM clamping and Boost dispatch
- Curve widget deep-copy immutability and Reset-to-Default behavior
- Live NVML GPU telemetry & WMI EC telemetry (when elevated)
- MainWindow UI mode synchronization
"""

import ctypes
import os
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from config import ConfigManager, DEFAULT_CURVE, PRESETS
from controller import interpolate_curve
from hardware import LenovoWmiHardware, NvmlHardware, ID_FAN1_RPM, ID_FAN2_RPM


def _is_elevated() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


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
        self.assertEqual(interpolate_curve([], 65), 3600)

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
        self.assertEqual(interpolate_curve(self.points, 45), 2700)
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

    def test_config_save_load_and_deep_copy(self):
        cm = ConfigManager(file_path=self.temp_file.name)
        self.assertEqual(cm.get("mode"), "target_65")
        self.assertEqual(cm.get("target_temp"), 65)

        pts = cm.get("curve_points")
        pts[0]["rpm"] = 7000  # mutating returned copy must NOT mutate internal state!
        self.assertNotEqual(cm.get("curve_points")[0]["rpm"], 7000)

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
        self.assertEqual(self.hw.set_fan1_rpm(-10), (ID_FAN1_RPM, 0))
        self.assertEqual(self.hw.set_fan2_rpm(-50), (ID_FAN2_RPM, 0))

        self.assertEqual(self.hw.set_fan1_rpm(1200), (ID_FAN1_RPM, 2000))
        self.assertEqual(self.hw.set_fan2_rpm(1800), (ID_FAN2_RPM, 2000))

        self.assertEqual(self.hw.set_fan1_rpm(8000), (ID_FAN1_RPM, 7700))
        self.assertEqual(self.hw.set_fan2_rpm(8000), (ID_FAN2_RPM, 7700))
        self.assertEqual(self.hw.set_fan1_rpm(7500), (ID_FAN1_RPM, 7500))
        self.assertEqual(self.hw.set_fan2_rpm(7500), (ID_FAN2_RPM, 7500))

        self.assertEqual(self.hw.set_fan1_rpm(4150), (ID_FAN1_RPM, 4150))
        self.assertEqual(self.hw.set_fan2_rpm(4150), (ID_FAN2_RPM, 4150))

    def test_set_boost_drives_both_fans_to_7500(self):
        calls = []
        self.hw.set_feature = lambda fid, val: calls.append((fid, val)) or True
        self.hw.set_boost(True)
        self.assertIn((ID_FAN1_RPM, 7500), calls)
        self.assertIn((ID_FAN2_RPM, 7500), calls)


class TestLiveHardwareTelemetry(unittest.TestCase):

    def test_live_nvml_reading(self):
        from hardware import POWER_DEVICE_D3
        nvml = NvmlHardware()
        if nvml._rtd3.get_power_state() == POWER_DEVICE_D3:
            res = nvml.poll_telemetry_rtd3_safe()
            self.assertEqual(res["dgpu_state"], "D3cold")
            self.assertTrue(res["gpu_sleeping"])
            nvml.shutdown()
            return
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

    def test_cpu_core_telemetry_provider(self):
        from hardware import CpuCoreTelemetryProvider
        prov = CpuCoreTelemetryProvider()
        try:
            d = prov.poll(ec_cpu_temp=54)
            self.assertIn("avg_temp", d)
            self.assertIsNotNone(d["avg_temp"])
            self.assertEqual(len(d["cores"]), 16)
            labels = [c["label"] for c in d["cores"]]
            self.assertEqual(labels[:6], ["P1", "P2", "P3", "P4", "P5", "P6"])
        finally:
            prov.shutdown()

    def test_optimus_rtd3_idle_release_and_d3cold_no_wake(self):
        """Verifies NVML handle is closed after 4 idle ticks and never called in D3cold."""
        from hardware import POWER_DEVICE_D0, POWER_DEVICE_D3

        nvml = NvmlHardware()
        # Mock RTD3 detector: idle in D0 -> releases NVML after 4 ticks
        nvml._rtd3.get_power_state = lambda: POWER_DEVICE_D0
        nvml._rtd3.get_wddm_dgpu_activity = lambda: (0.0, 4.0)
        nvml.get_gpu_temp = lambda: 46
        nvml.get_gpu_power = lambda: 1.5
        nvml.get_gpu_utilization = lambda: (0, 0)
        nvml._initialized = True

        for _ in range(4):
            res = nvml.poll_telemetry_rtd3_safe()
        self.assertTrue(nvml._idle_released)
        self.assertFalse(nvml._initialized, "NVML must be shut down on idle so dGPU can sleep!")

        # Next tick in D0_idle must NOT call get_gpu_temp
        nvml.get_gpu_temp = lambda: (_ for _ in ()).throw(AssertionError("Must NOT wake NVML when idle!"))
        res_idle = nvml.poll_telemetry_rtd3_safe()
        self.assertEqual(res_idle["dgpu_state"], "D0_idle")
        self.assertTrue(res_idle["gpu_sleeping"])

        # Transition to D3cold -> still zero NVML calls, reports D3cold 0.0W
        nvml._rtd3.get_power_state = lambda: POWER_DEVICE_D3
        res_d3 = nvml.poll_telemetry_rtd3_safe()
        self.assertEqual(res_d3["dgpu_state"], "D3cold")
        self.assertEqual(res_d3["gpu_pwr"], 0.0)
        self.assertIsNone(res_d3["gpu_temp"])
        nvml.shutdown()

    @unittest.skipUnless(_is_elevated(), "Lenovo WMI requires Administrator privileges")
    def test_live_wmi_reading(self):
        wmi = LenovoWmiHardware()
        wmi.init_com()
        f1 = wmi.get_fan1_rpm()
        f2 = wmi.get_fan2_rpm()
        cpu = wmi.get_cpu_temp()
        self.assertIsNotNone(f1, "Fan 1 RPM returned None")
        self.assertIsNotNone(f2, "Fan 2 RPM returned None")
        self.assertIsNotNone(cpu, "CPU temp returned None")
        self.assertGreaterEqual(f1, 0)
        self.assertGreaterEqual(f2, 0)
        self.assertGreater(cpu, 25, "CPU temp unrealistic")
        wmi.uninit_com()


class TestMainWindowSync(unittest.TestCase):

    def test_ui_initial_mode_and_curve_preset_immutability(self):
        from PySide6.QtWidgets import QApplication
        from main_window import MainWindow

        _app = QApplication.instance() or QApplication([])
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

            # Switch to Target mode and change target temp -> badge updates dynamically
            win._set_mode_from_ui(0)
            self.assertIn("ЦЕЛЕВАЯ", win.lbl_mode_badge.text())
            win.spin_target_temp.setValue(68)
            self.assertIn("68°C", win.lbl_mode_badge.text())
            self.assertEqual(win.stack.currentIndex(), 0)

            # Verify preset immutability and reset to DEFAULT_CURVE
            quiet_orig = [dict(p) for p in PRESETS["Тихий (Quiet)"]]
            win.apply_curve_preset(PRESETS["Тихий (Quiet)"])
            win.curve_widget.points[0]["rpm"] = 6900
            self.assertEqual(PRESETS["Тихий (Quiet)"], quiet_orig)

            win.apply_curve_preset(DEFAULT_CURVE)
            self.assertEqual(win.curve_widget.get_points(), DEFAULT_CURVE)

            # Verify dual labeled tray icons (CPU & GPU) and 16-core telemetry UI update
            self.assertEqual(len(win._core_tile_widgets), 16)
            ic_cpu = win._create_labeled_tray_icon("CPU", "52", "#0284c7", "#0f766e")
            ic_gpu = win._create_labeled_tray_icon("GPU", "OFF", "#059669", "#064e3b")
            self.assertFalse(ic_cpu.isNull())
            self.assertFalse(ic_gpu.isNull())

            mock_cores = [
                {"label": f"P{i}", "kind": "P", "temp": 50.0 + i, "load": 12.0, "mhz": 3600}
                for i in range(1, 7)
            ] + [
                {"label": f"E{i}", "kind": "E", "temp": 48.0, "load": 5.0, "mhz": 2200}
                for i in range(1, 9)
            ] + [
                {"label": "LP1", "kind": "LP", "temp": 46.0, "load": 1.0, "mhz": 900},
                {"label": "LP2", "kind": "LP", "temp": 46.0, "load": 1.0, "mhz": 900},
            ]
            win.on_telemetry_updated({
                "dgpu_state": "D3cold",
                "gpu_sleeping": True,
                "gpu_temp": None,
                "gpu_pwr": 0.0,
                "gpu_util": 0,
                "mem_util": 0,
                "cpu_temp": 51,
                "cpu_details": {
                    "cpu_name": "Intel Core Ultra 7 155H",
                    "msr_active": True,
                    "avg_temp": 49.5,
                    "max_temp": 56.0,
                    "pkg_temp": 52.0,
                    "power_w": 11.4,
                    "total_load": 8.5,
                    "cores": mock_cores,
                },
                "fan1_rpm": 2200,
                "fan2_rpm": 2200,
                "mode": "target_65",
                "target_rpm": 2200,
                "wmi_ok": True,
                "safety_level": 0,
                "mode_desc": "Тихий режим",
            })
            self.assertIn("49.5", win.lbl_cpu_avg_pill.text())
            self.assertEqual(win._core_tile_widgets[0]["temp"].text(), "51°C")
            self.assertEqual(win.lbl_gpu_temp.text(), "ОТКЛ.")

            win.safe_exit()
        finally:
            if os.path.exists(cfg_path):
                os.remove(cfg_path)


if __name__ == "__main__":
    unittest.main()
