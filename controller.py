"""
Core background fan controller and telemetry thread.
Handles:
- Periodic telemetry reading (GPU temp/power/util, CPU temp, Fan 1 & 2 RPM)
- Mode logic (Target 65°C regulator, Fan Curve interpolation, Manual RPM, Auto, Boost)
- Safe shutdown and BIOS restore
"""

import time
import logging
from PySide6.QtCore import QThread, Signal
from hardware import LenovoWmiHardware, NvmlHardware

logger = logging.getLogger("ThinkBookController")


def interpolate_curve(points: list[dict], temp: float) -> int:
    """Linearly interpolates RPM for a given temperature from curve points."""
    if not points:
        return 4150
    sorted_pts = sorted(points, key=lambda x: x["temp"])
    if temp <= sorted_pts[0]["temp"]:
        return sorted_pts[0]["rpm"]
    if temp >= sorted_pts[-1]["temp"]:
        return sorted_pts[-1]["rpm"]

    for i in range(len(sorted_pts) - 1):
        p0 = sorted_pts[i]
        p1 = sorted_pts[i + 1]
        if p0["temp"] <= temp <= p1["temp"]:
            span = p1["temp"] - p0["temp"]
            if span == 0:
                return p0["rpm"]
            ratio = (temp - p0["temp"]) / span
            return int(p0["rpm"] + ratio * (p1["rpm"] - p0["rpm"]))
    return sorted_pts[-1]["rpm"]


class FanControllerThread(QThread):
    """Background thread for non-blocking telemetry acquisition and fan control."""

    telemetry_updated = Signal(dict)
    status_message = Signal(str)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config = config_manager
        self.wmi = LenovoWmiHardware()
        self.nvml = NvmlHardware()
        self.running = True

        # Regulator state
        self._current_mode = self.config.get("mode", "target_65")
        self._target_temp = float(self.config.get("target_temp", 65))
        self._manual_rpm = int(self.config.get("manual_rpm", 4150))
        self._curve_points = list(self.config.get("curve_points"))

        self._last_applied_rpm = None
        self._last_applied_mode = None
        self._force_apply = True
        self._filtered_gpu_temp = None
        self._filtered_cpu_temp = None
        self._prev_primary_temp = None
        self._mining_integral = 0.0
        init_target = float(self.config.get("target_temp", 68))
        self._current_stepped_rpm = max(2600.0, min(5000.0, 4000.0 - (init_target - 65.0) * 235.0))
        self._safety_active = False
        self._boost_active_in_hw = False

    def set_mode(self, mode: str):
        """Switches active control mode: 'target_65', 'curve', 'manual', 'auto', 'boost'."""
        self._current_mode = mode
        self.config.set("mode", mode)
        # Reset integral when switching modes
        self._mining_integral = 0.0
        self._prev_primary_temp = None
        if mode == "target_65":
            self._current_stepped_rpm = max(2600.0, min(5000.0, 4000.0 - (self._target_temp - 65.0) * 235.0))
        # Force immediate re-application while preserving prev_mode
        self._force_apply = True

    def set_target_temp(self, temp: float):
        self._target_temp = float(temp)
        self.config.set("target_temp", self._target_temp)
        self._mining_integral = 0.0
        self._prev_primary_temp = None
        self._current_stepped_rpm = max(2600.0, min(5000.0, 4000.0 - (self._target_temp - 65.0) * 235.0))
        self._force_apply = True

    def set_manual_rpm(self, rpm: int):
        self._manual_rpm = int(rpm)
        self.config.set("manual_rpm", self._manual_rpm)
        if self._current_mode == "manual":
            self._force_apply = True

    def set_curve_points(self, points: list[dict]):
        self._curve_points = sorted(points, key=lambda x: x["temp"])
        self.config.set("curve_points", self._curve_points)
        if self._current_mode == "curve":
            self._force_apply = True

    def stop(self):
        """Stops thread and resets fans to auto."""
        self.running = False
        self.wait(3000)

    def run(self):
        self.wmi.init_com()
        self.nvml.init()

        logger.info("FanControllerThread started.")

        apply_counter = 0
        last_tick_time = time.time()

        try:
            while self.running:
                now = time.time()
                dt = max(0.5, min(3.0, now - last_tick_time))
                last_tick_time = now

                # 1. Read Telemetry
                gpu_temp = self.nvml.get_gpu_temp()
                gpu_pwr = self.nvml.get_gpu_power()
                gpu_util = self.nvml.get_gpu_utilization()
                cpu_temp = self.wmi.get_cpu_temp()
                fan1_rpm = self.wmi.get_fan1_rpm()
                fan2_rpm = self.wmi.get_fan2_rpm()
                boost_state = self.wmi.get_boost_state()

                # Exponential moving average filter for smooth control without jitter
                if gpu_temp is not None:
                    if self._filtered_gpu_temp is None:
                        self._filtered_gpu_temp = float(gpu_temp)
                    else:
                        self._filtered_gpu_temp = 0.65 * self._filtered_gpu_temp + 0.35 * float(gpu_temp)
                else:
                    self._filtered_gpu_temp = float(cpu_temp) if cpu_temp is not None else 65.0

                if cpu_temp is not None:
                    if self._filtered_cpu_temp is None:
                        self._filtered_cpu_temp = float(cpu_temp)
                    else:
                        self._filtered_cpu_temp = 0.7 * self._filtered_cpu_temp + 0.3 * float(cpu_temp)

                primary_temp = self._filtered_gpu_temp

                # 2. Compute Target Fan RPM based on active mode
                target_rpm = 0
                mode_desc = ""

                if self._current_mode == "target_65":
                    # Closed-loop PID regulation with damping and dynamic baseline
                    target = self._target_temp
                    error = primary_temp - target

                    # 1. Dynamic baseline calibrated to target temperature
                    # 60°C -> ~4800 RPM, 65°C -> ~4000 RPM, 68°C -> ~3300 RPM, 72°C -> ~2800 RPM
                    baseline = max(2600.0, min(5000.0, 4000.0 - (target - 65.0) * 235.0))

                    # 2. Deadband of ±0.65°C: inside deadband, hold steady to prevent fan hunting
                    if abs(error) < 0.65:
                        effective_error = 0.0
                    else:
                        effective_error = error - (0.65 if error > 0 else -0.65)

                    # 3. Derivative term (d_temp/dt) on measurement to anticipate and eliminate overshoot
                    if self._prev_primary_temp is None:
                        d_temp = 0.0
                    else:
                        d_temp = (primary_temp - self._prev_primary_temp) / dt
                    self._prev_primary_temp = primary_temp

                    # 4. Integral term with anti-windup (accumulates steady-state offset smoothly)
                    self._mining_integral += effective_error * 3.5 * dt
                    self._mining_integral = max(-500.0, min(600.0, self._mining_integral))

                    # 5. PID Calculation: Baseline + Proportional + Integral + Derivative damping
                    kd = 40.0
                    raw_target = baseline + (effective_error * 95.0) + self._mining_integral + (d_temp * kd)

                    # 6. Smooth slew-rate limiting: max ±35 RPM per second (smooth acoustic glide, no abrupt steps)
                    max_step = 35.0 * dt
                    diff = raw_target - self._current_stepped_rpm
                    if abs(diff) > max_step:
                        self._current_stepped_rpm += max_step if diff > 0 else -max_step
                    else:
                        self._current_stepped_rpm = raw_target

                    # 7. Clamp within safe range without harsh 25-RPM quantization
                    calc_rpm = int(round(self._current_stepped_rpm))
                    calc_rpm = max(2400, min(5500, calc_rpm))
                    target_rpm = calc_rpm
                    mode_desc = f"Цель: {target:.0f}°C (Текущая: {primary_temp:.1f}°C, Δ {error:+.1f}°C) ➔ {target_rpm} RPM"

                elif self._current_mode == "curve":
                    # Sensor selection for curve mode
                    sensor_mode = self.config.get("curve_sensor", "max")
                    if sensor_mode == "gpu":
                        ref_temp = gpu_temp if gpu_temp is not None else primary_temp
                    elif sensor_mode == "cpu":
                        ref_temp = cpu_temp if cpu_temp is not None else primary_temp
                    else:  # max (safe shared heatpipe cooling)
                        g = float(gpu_temp) if gpu_temp is not None else 0.0
                        c = float(cpu_temp) - 3.0 if cpu_temp is not None else 0.0
                        ref_temp = max(g, c) if (g or c) else primary_temp

                    target_rpm = interpolate_curve(self._curve_points, ref_temp)
                    target_rpm = max(2200, min(7000, target_rpm))
                    mode_desc = f"Кривая: {ref_temp:.0f}°C ➔ {target_rpm} RPM"

                elif self._current_mode == "manual":
                    target_rpm = self._manual_rpm
                    mode_desc = f"Ручной: {target_rpm} RPM"

                elif self._current_mode == "auto":
                    target_rpm = 0
                    mode_desc = "Штатный BIOS (Auto)"

                elif self._current_mode == "boost":
                    target_rpm = 7500
                    mode_desc = "100% Турбо (Максимум ~7300–7700 RPM)"

                # 3. Emergency Thermal Safety Protection
                # If either chip approaches unsafe limits, automatically ramp fans
                safety_enabled = bool(self.config.get("safety_override", True))
                curr_g = gpu_temp if gpu_temp is not None else 0
                curr_c = cpu_temp if cpu_temp is not None else 0

                if safety_enabled and (curr_g >= 80 or curr_c >= 85):
                    self._safety_active = True
                    if curr_g >= 84 or curr_c >= 90:
                        target_rpm = 7500
                        mode_desc = f"⚠️ ЗАЩИТА (100% ТУРБО): GPU {curr_g}°C, CPU {curr_c}°C"
                    else:
                        target_rpm = max(target_rpm, 4900)
                        mode_desc = f"⚠️ ЗАЩИТА ОТ НАГРЕВА: GPU {curr_g}°C, CPU {curr_c}°C ➔ {target_rpm} RPM"
                else:
                    self._safety_active = False

                # 4. Apply Fan Command to Hardware
                # Keep writes smooth and continuous to avoid stepped jumps
                apply_counter += 1
                rpm_diff = abs(target_rpm - (self._last_applied_rpm or 0))
                is_boost = (self._current_mode == "boost" or (self._safety_active and target_rpm >= 5400))

                should_apply = (
                    getattr(self, "_force_apply", False)
                    or self._current_mode != self._last_applied_mode
                    or is_boost
                    or rpm_diff >= 15
                    or apply_counter >= 2
                )

                if should_apply:
                    self._force_apply = False
                    apply_counter = 0
                    prev_mode = self._last_applied_mode
                    self._last_applied_mode = self._current_mode
                    self._last_applied_rpm = target_rpm

                    if is_boost:
                        self.wmi.set_boost(True, target_rpm)
                        self._boost_active_in_hw = True
                    else:
                        if self._boost_active_in_hw or prev_mode == "boost":
                            self.wmi.clear_boost()
                            self._boost_active_in_hw = False
                        if self._current_mode == "auto" and not self._safety_active:
                            self.wmi.reset_to_auto()
                        else:
                            self.wmi.set_both_fans(target_rpm)

                # 4. Emit Telemetry to GUI
                telemetry_data = {
                    "gpu_temp": gpu_temp,
                    "gpu_pwr": gpu_pwr,
                    "gpu_util": gpu_util[0] if gpu_util else None,
                    "mem_util": gpu_util[1] if gpu_util else None,
                    "cpu_temp": cpu_temp,
                    "fan1_rpm": fan1_rpm,
                    "fan2_rpm": fan2_rpm,
                    "boost_state": boost_state,
                    "mode": self._current_mode,
                    "target_rpm": target_rpm,
                    "mode_desc": mode_desc,
                }
                self.telemetry_updated.emit(telemetry_data)

                # Sleep interval (1.2 seconds)
                time.sleep(1.2)

        finally:
            logger.info("Controller exiting, restoring BIOS auto fan mode...")
            try:
                self.wmi.reset_to_auto()
            except Exception:
                pass
            self.nvml.shutdown()
            self.wmi.uninit_com()
            logger.info("Controller cleanup complete.")
