"""
Physics-coupled closed-loop simulation tests for NativeSmoothRegulator and FanControllerThread.
Exercises the REAL production regulator code across idle, bursty CPU spikes, heavy GPU+CPU loads,
integer quantization noise, curve flicker, bumpless mode transitions, and safety hysteresis.
"""

import os
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from config import ConfigManager
from controller import NativeSmoothRegulator, FanControllerThread


class TestNativeSmoothRegulatorSimulation(unittest.TestCase):

    def test_cold_idle_startup_no_rpm_surge(self):
        """At 41°C idle, regulator must stay whisper-quiet (~2100 RPM), never jumping to 4000 RPM."""
        reg = NativeSmoothRegulator(mode="target_65", target_temp=65.0)
        rpms = []
        fan_rpm = 2100
        for _ in range(30):
            res = reg.step(1.0, gpu_temp=41, cpu_temp=44, gpu_pwr=5.0, actual_fan_rpm=fan_rpm)
            fan_rpm = res.target_rpm
            rpms.append(fan_rpm)

        self.assertLessEqual(max(rpms), 2150, f"Unexpected idle startup surge: {rpms}")
        self.assertEqual(rpms[-1], 2100)

    def test_transient_cpu_spike_rejected_without_roar(self):
        """A 2-second 85°C Meteor Lake single-core spike must not jerk fan speed."""
        reg = NativeSmoothRegulator(mode="target_65", target_temp=65.0)
        fan_rpm = 2100
        for _ in range(15):
            fan_rpm = reg.step(1.0, gpu_temp=46, cpu_temp=48, gpu_pwr=8.0, actual_fan_rpm=fan_rpm).target_rpm

        spike_rpms = []
        for s in range(15):
            cpu_t = 85 if s in (1, 2) else 48
            res = reg.step(1.0, gpu_temp=46, cpu_temp=cpu_t, gpu_pwr=8.0, actual_fan_rpm=fan_rpm)
            fan_rpm = res.target_rpm
            spike_rpms.append(fan_rpm)

        self.assertLessEqual(max(spike_rpms) - min(spike_rpms), 60)

    def test_sustained_heavy_load_holds_target_with_zero_steady_hunting(self):
        """Under 75W GPU + 25W CPU load, regulator smoothly converges to 65°C with 0 RPM steady chatter."""
        reg = NativeSmoothRegulator(mode="target_65", target_temp=65.0)
        dt = 1.0
        t_amb, t_pipe, t_gpu, t_cpu = 24.0, 40.0, 41.0, 44.0
        fan_rpm = 2100
        prev_rpm = fan_rpm
        max_single_step = 0
        peak_gpu = 0.0
        steady_rpms = []

        for s in range(280):
            p_gpu, p_cpu = 75.0, 25.0
            g_cool = 0.65 + (fan_rpm / 1000.0) * 0.48
            t_pipe += (((p_gpu + p_cpu) - g_cool * (t_pipe - t_amb)) / 95.0) * dt
            t_gpu += (((t_pipe + p_gpu * 0.11) - t_gpu) / 3.5) * dt
            t_cpu += (((t_pipe + p_cpu * 0.32) - t_cpu) / 2.5) * dt

            res = reg.step(dt, int(round(t_gpu)), int(round(t_cpu)), p_gpu, fan_rpm)
            max_single_step = max(max_single_step, abs(res.target_rpm - prev_rpm))
            prev_rpm = res.target_rpm
            fan_rpm = res.target_rpm
            peak_gpu = max(peak_gpu, t_gpu)
            if s >= 215:
                steady_rpms.append(fan_rpm)

        # Smooth slew rate (never > 130 RPM per 1s tick)
        self.assertLessEqual(max_single_step, 130)
        # Controlled overshoot (< 3.2°C on sudden 100W step from cold)
        self.assertLess(peak_gpu, 68.2)
        # Accurate steady-state temperature within ±0.5°C of 65°C
        self.assertAlmostEqual(t_gpu, 65.0, delta=0.5)
        # Zero steady-state fan hunting despite 1°C integer quantization!
        self.assertEqual(max(steady_rpms) - min(steady_rpms), 0)

    def test_coupled_cpu_sustained_heat_scales_cooling(self):
        """Sustained high CPU heat (GPU cool at 55°C, CPU soaked at 82°C) proactively ramps fans."""
        reg = NativeSmoothRegulator(mode="target_65", target_temp=65.0)
        fan_rpm = 2200
        last_res = None
        for _ in range(45):
            last_res = reg.step(1.0, gpu_temp=55, cpu_temp=82, gpu_pwr=10.0, actual_fan_rpm=fan_rpm)
            fan_rpm = last_res.target_rpm

        self.assertEqual(last_res.lead_sensor, "CPU")
        self.assertGreater(fan_rpm, 3800)

    def test_bumpless_mode_and_target_transitions(self):
        """Changing target_temp or switching modes must not cause instantaneous RPM cliffs."""
        reg = NativeSmoothRegulator(mode="target_65", target_temp=65.0)
        fan_rpm = 3800
        for _ in range(15):
            fan_rpm = reg.step(1.0, gpu_temp=65, cpu_temp=68, gpu_pwr=55.0, actual_fan_rpm=fan_rpm).target_rpm

        before = fan_rpm
        reg.set_target_temp(60.0)
        after_target_change = reg.step(1.0, gpu_temp=65, cpu_temp=68, gpu_pwr=55.0, actual_fan_rpm=fan_rpm).target_rpm
        self.assertLessEqual(abs(after_target_change - before), 130)

        # Switch from Boost (7500) back to Target 65°C -> smooth ramp down, not an instant -4000 RPM cliff
        reg.set_mode("boost")
        boost_rpm = reg.step(1.0, gpu_temp=62, cpu_temp=65, gpu_pwr=40.0, actual_fan_rpm=after_target_change).target_rpm
        self.assertEqual(boost_rpm, 7500)

        reg.set_mode("target_65")
        first_post_boost = reg.step(1.0, gpu_temp=62, cpu_temp=65, gpu_pwr=40.0, actual_fan_rpm=7500).target_rpm
        self.assertGreater(first_post_boost, 7350, "Must glide smoothly down from Boost, not drop off a cliff!")

    def test_curve_mode_directional_hysteresis_stops_flicker(self):
        """Alternating 64°C <-> 65°C every second in Curve mode must produce 0 RPM hunting."""
        reg = NativeSmoothRegulator(mode="curve", curve_sensor="gpu")
        fan_rpm = 3500
        history = []
        for s in range(40):
            g_in = 65 if (s % 2 == 0) else 64
            fan_rpm = reg.step(1.0, gpu_temp=g_in, cpu_temp=60, gpu_pwr=40.0, actual_fan_rpm=fan_rpm).target_rpm
            history.append(fan_rpm)

        # Last 15 seconds must hold completely steady
        self.assertEqual(len(set(history[-15:])), 1, f"Curve hunted on 1°C flicker: {history[-15:]}")

    def test_debounced_safety_hysteresis_and_recovery(self):
        """Safety override triggers after sustained overheat, latches until cool, and recovers smoothly."""
        reg = NativeSmoothRegulator(mode="manual", manual_rpm=2800, safety_enabled=True)
        fan_rpm = 2800
        for _ in range(5):
            fan_rpm = reg.step(1.0, gpu_temp=70, cpu_temp=72, gpu_pwr=50.0, actual_fan_rpm=fan_rpm).target_rpm

        # 1. Sustained critical GPU 86°C for 6s -> enters level 2 (7500 RPM)
        for _ in range(6):
            res = reg.step(1.0, gpu_temp=86, cpu_temp=80, gpu_pwr=90.0, actual_fan_rpm=fan_rpm)
            fan_rpm = res.target_rpm
        self.assertEqual(res.safety_level, 2)
        self.assertEqual(fan_rpm, 7500)

        # 2. Cools to 76°C (still above 74°C release) -> remains latched
        for _ in range(8):
            res = reg.step(1.0, gpu_temp=76, cpu_temp=76, gpu_pwr=40.0, actual_fan_rpm=fan_rpm)
            fan_rpm = res.target_rpm
        self.assertGreater(res.safety_level, 0)

        # 3. Cools to 68°C -> clears safety latch and glides down smoothly
        for _ in range(15):
            res = reg.step(1.0, gpu_temp=68, cpu_temp=70, gpu_pwr=20.0, actual_fan_rpm=fan_rpm)
            fan_rpm = res.target_rpm
        self.assertEqual(res.safety_level, 0)

    def test_thread_wrapper_mode_switch_clears_boost(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            cfg_path = tf.name
        os.remove(cfg_path)
        try:
            cfg = ConfigManager(file_path=cfg_path)
            ctrl = FanControllerThread(cfg)
            ctrl.set_mode("boost")
            self.assertEqual(ctrl._current_mode, "boost")
            ctrl.set_mode("target_65")
            self.assertEqual(ctrl._current_mode, "target_65")
        finally:
            if os.path.exists(cfg_path):
                os.remove(cfg_path)


if __name__ == "__main__":
    unittest.main()
