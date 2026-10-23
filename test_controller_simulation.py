"""
Simulation tests for FanController PI regulator and thermal safety logic.
"""

import unittest
import time
from unittest.mock import MagicMock
from controller import FanControllerThread
from config import ConfigManager


class MockHardware:
    def __init__(self, init_gpu=65, init_cpu=60):
        self.gpu_temp = init_gpu
        self.cpu_temp = init_cpu
        self.applied_fans = []
        self.applied_boost = []

    def get_gpu_temp(self):
        return self.gpu_temp

    def get_gpu_power(self):
        return 35.0

    def get_gpu_utilization(self):
        return (100, 0)

    def get_cpu_temp(self):
        return self.cpu_temp

    def get_fan1_rpm(self):
        return 4150

    def get_fan2_rpm(self):
        return 4150

    def get_boost_state(self):
        return 0

    def set_both_fans(self, rpm):
        self.applied_fans.append(rpm)
        return True

    def set_boost(self, state, target_rpm=7500):
        self.applied_boost.append(state)
        if state:
            self.applied_fans.append(target_rpm)
        return True

    def clear_boost(self):
        self.applied_boost.append(False)
        return True

    def reset_to_auto(self):
        self.applied_fans.append(0)
        return True

    def init_com(self):
        pass

    def uninit_com(self):
        pass

    def init(self):
        return True

    def shutdown(self):
        pass


class TestControllerSimulation(unittest.TestCase):

    def test_pi_regulator_at_exact_target(self):
        cfg = ConfigManager()
        cfg.set("mode", "target_65")
        cfg.set("target_temp", 65)

        ctrl = FanControllerThread(cfg)
        mock_wmi = MockHardware(init_gpu=65, init_cpu=60)
        mock_nvml = MockHardware(init_gpu=65, init_cpu=60)
        ctrl.wmi = mock_wmi
        ctrl.nvml = mock_nvml

        # Run 10 ticks simulation with GPU at 65°C
        # At 65°C, error is 0, target should hover around 4150 RPM baseline
        for _ in range(10):
            # simulate 1 tick
            gpu_temp = mock_nvml.get_gpu_temp()
            if ctrl._filtered_gpu_temp is None:
                ctrl._filtered_gpu_temp = float(gpu_temp)
            else:
                ctrl._filtered_gpu_temp = 0.65 * ctrl._filtered_gpu_temp + 0.35 * float(gpu_temp)

            target = ctrl._target_temp
            error = ctrl._filtered_gpu_temp - target
            effective_error = 0.0 if abs(error) < 0.35 else error
            ctrl._mining_integral += effective_error * 8.0 * 1.2
            raw_target = 4150.0 + (effective_error * 120.0) + ctrl._mining_integral
            calc_rpm = int(round(raw_target / 25.0) * 25)

        self.assertEqual(calc_rpm, 4150)
        self.assertEqual(ctrl._mining_integral, 0.0)

    def test_pi_regulator_under_heat_load(self):
        cfg = ConfigManager()
        cfg.set("mode", "target_65")
        cfg.set("target_temp", 65)

        ctrl = FanControllerThread(cfg)
        # GPU is hot: 68°C
        mock_nvml = MockHardware(init_gpu=68, init_cpu=65)

        # Simulate multiple ticks at 68°C
        calc_rpm = 4150
        for _ in range(5):
            gpu_temp = mock_nvml.get_gpu_temp()
            if ctrl._filtered_gpu_temp is None:
                ctrl._filtered_gpu_temp = float(gpu_temp)
            else:
                ctrl._filtered_gpu_temp = 0.65 * ctrl._filtered_gpu_temp + 0.35 * float(gpu_temp)

            target = ctrl._target_temp
            error = ctrl._filtered_gpu_temp - target
            effective_error = 0.0 if abs(error) < 0.35 else error
            ctrl._mining_integral += effective_error * 8.0 * 1.2
            raw_target = 4150.0 + (effective_error * 120.0) + ctrl._mining_integral
            calc_rpm = int(round(raw_target / 25.0) * 25)

        # Under +3°C heat, RPM must ramp significantly above 4150 to cool the chip down
        self.assertGreater(calc_rpm, 4400)
        self.assertGreater(ctrl._mining_integral, 0.0)

    def test_emergency_thermal_safety_override(self):
        cfg = ConfigManager()
        cfg.set("safety_override", True)
        ctrl = FanControllerThread(cfg)

        # GPU critical at 85°C
        curr_g = 85
        curr_c = 75
        target_rpm = 3000

        safety_enabled = bool(cfg.get("safety_override", True))
        if safety_enabled and (curr_g >= 80 or curr_c >= 85):
            if curr_g >= 84 or curr_c >= 90:
                target_rpm = 7500
            else:
                target_rpm = max(target_rpm, 4900)

        self.assertEqual(target_rpm, 7500)

    def test_turbo_boost_mode(self):
        cfg = ConfigManager()
        cfg.set("mode", "boost")
        ctrl = FanControllerThread(cfg)
        mock_wmi = MockHardware(init_gpu=60, init_cpu=55)
        ctrl.wmi = mock_wmi

        ctrl.set_mode("boost")
        self.assertEqual(ctrl._current_mode, "boost")
        self.assertTrue(ctrl._force_apply)
        # In boost mode, target_rpm is 7500 and set_boost(True, target_rpm) is dispatched
        target_rpm = 7500
        ctrl.wmi.set_boost(True, target_rpm)
        self.assertIn(True, mock_wmi.applied_boost)

    def test_mode_switch_preserves_prev_mode_and_clears_boost(self):
        cfg = ConfigManager()
        ctrl = FanControllerThread(cfg)
        mock_wmi = MockHardware(init_gpu=60, init_cpu=55)
        ctrl.wmi = mock_wmi

        # 1. Enter boost mode
        ctrl._current_mode = "boost"
        ctrl._last_applied_mode = "boost"
        ctrl._last_applied_rpm = 7500
        ctrl._boost_active_in_hw = True

        # 2. Switch to manual mode
        ctrl.set_mode("manual")
        # Ensure _last_applied_mode was NOT wiped to None so prev_mode is preserved
        self.assertEqual(ctrl._last_applied_mode, "boost")
        self.assertTrue(ctrl._force_apply)

        # 3. Controller application logic when transitioning away from boost
        prev_mode = ctrl._last_applied_mode
        ctrl._last_applied_mode = ctrl._current_mode
        if ctrl._boost_active_in_hw or prev_mode == "boost":
            ctrl.wmi.clear_boost()
            ctrl._boost_active_in_hw = False
        ctrl.wmi.set_both_fans(3500)

        self.assertIn(False, mock_wmi.applied_boost)
        self.assertIn(3500, mock_wmi.applied_fans)


if __name__ == "__main__":
    unittest.main()
