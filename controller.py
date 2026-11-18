"""
Core background fan controller and native-smooth thermal regulator.
Handles:
- Multi-timescale filtered telemetry (GPU temp/power/util, CPU die & heatpipe soak temp, Fan 1 & 2 RPM)
- Feedforward Smoothstep + Bumpless Wide-Range Anti-Windup I-PD target temperature regulation
- Hysteresis-stabilized Fan Curve interpolation, tactile Manual RPM, BIOS Auto, and Full Boost
- Debounced progressive thermal safety protection and sleep/resume recovery
- Safe shutdown and guaranteed BIOS Auto restore
"""

from dataclasses import dataclass
import math
import threading
import time
import logging
from PySide6.QtCore import QThread, Signal

from config import clone_curve_points
from hardware import LenovoWmiHardware, NvmlHardware, CpuCoreTelemetryProvider

logger = logging.getLogger("ThinkBookController")


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Hermite C1 smoothstep interpolation on [0, 1]."""
    if edge1 <= edge0:
        return 1.0 if x >= edge1 else 0.0
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def interpolate_curve(points: list[dict], temp: float) -> int:
    """Linearly interpolates RPM for a given temperature from curve points."""
    if not points:
        return 3600
    sorted_pts = sorted(points, key=lambda x: x["temp"])
    if temp <= sorted_pts[0]["temp"]:
        return int(sorted_pts[0]["rpm"])
    if temp >= sorted_pts[-1]["temp"]:
        return int(sorted_pts[-1]["rpm"])

    for i in range(len(sorted_pts) - 1):
        p0 = sorted_pts[i]
        p1 = sorted_pts[i + 1]
        if p0["temp"] <= temp <= p1["temp"]:
            span = p1["temp"] - p0["temp"]
            if span <= 0:
                return int(p0["rpm"])
            ratio = (temp - p0["temp"]) / span
            return int(round(p0["rpm"] + ratio * (p1["rpm"] - p0["rpm"])))
    return int(sorted_pts[-1]["rpm"])


@dataclass
class RegulatorStepResult:
    target_rpm: int
    desired_rpm: int
    primary_temp: float
    curve_ref_temp: float
    lead_sensor: str
    safety_level: int
    is_boost: bool
    mode_desc: str


class NativeSmoothRegulator:
    """
    Pure, deterministic thermal control engine for Lenovo ThinkBook 14 G6+ IMH.
    Eliminates fan jerks, sensor quantization chatter, startup surges, and steady-state droop.
    """

    RPM_FLOOR = 2100.0
    RPM_CEIL = 6800.0
    RPM_BOOST = 7500

    def __init__(
        self,
        mode: str = "target_65",
        target_temp: float = 65.0,
        manual_rpm: int = 3600,
        curve_points: list[dict] | None = None,
        curve_sensor: str = "max",
        safety_enabled: bool = True,
    ):
        self.mode = mode
        self.target_temp = float(target_temp)
        self.manual_rpm = int(manual_rpm)
        self.curve_points = clone_curve_points(curve_points or [])
        self.curve_sensor = curve_sensor
        self.safety_enabled = bool(safety_enabled)

        # Multi-timescale filter states
        self.gpu_ema: float | None = None
        self.cpu_fast_ema: float | None = None
        self.cpu_soak_ema: float | None = None
        self.pwr_ema: float | None = None
        self.gpu_rate_ema: float = 0.0
        self.cpu_rate_ema: float = 0.0

        # Regulator internal states
        self.trim_rpm: float = 0.0
        self.ramped_rpm: float | None = None
        self.latched_hw_rpm: int | None = None
        self.curve_sticky_temp: float | None = None

        # Safety debounce & hysteresis state
        self.safety_level: int = 0  # 0 = normal, 1 = high (>=5400 RPM), 2 = critical (7500 RPM boost)
        self.high_temp_hold_sec: float = 0.0
        self.crit_temp_hold_sec: float = 0.0

    @staticmethod
    def _ema(prev: float | None, sample: float, dt: float, tau: float) -> float:
        if prev is None:
            return sample
        alpha = 1.0 - math.exp(-dt / max(0.1, tau))
        return prev + alpha * (sample - prev)

    @staticmethod
    def _soft_deadband(error: float, deadband: float = 0.45, blend: float = 0.65) -> float:
        """C1-smooth deadband: strictly 0 inside ±deadband, parabolic blend into linear outside."""
        ae = abs(error)
        if ae <= deadband:
            return 0.0
        sign = 1.0 if error > 0 else -1.0
        excess = ae - deadband
        if excess < blend:
            return sign * (excess * excess) / (2.0 * blend)
        return sign * (excess - 0.5 * blend)

    def _compute_feedforward(self, primary_temp: float, target: float, pwr_soak: float | None) -> float:
        """
        Whisper-quiet 2100 RPM when cool; smoothly glides via Hermite smoothstep toward
        nominal target baseline as temperature approaches target, assisted by soaked GPU power.
        """
        nominal_at_target = max(2700.0, min(4600.0, 3550.0 - (target - 65.0) * 140.0))
        quiet_temp = min(47.0, target - 16.0)
        s = smoothstep(quiet_temp, target - 1.0, primary_temp)
        ff = self.RPM_FLOOR + s * (nominal_at_target - self.RPM_FLOOR)

        if pwr_soak is not None and pwr_soak > 18.0:
            pwr_gate = smoothstep(quiet_temp - 2.0, target - 3.0, primary_temp)
            ff += pwr_gate * min(1600.0, (pwr_soak - 18.0) * 21.0)

        return max(self.RPM_FLOOR, min(self.RPM_CEIL, ff))

    def _primary_temp_and_rate(self) -> tuple[float, float, str]:
        """
        Coupled shared-heatpipe thermal index:
        Meteor Lake CPU package naturally sits ~8°C warmer than GPU die for equivalent heatpipe soak.
        """
        g_temp = self.gpu_ema if self.gpu_ema is not None else 50.0
        if self.cpu_soak_ema is not None:
            c_equiv = self.cpu_soak_ema - 8.0
            if self.gpu_ema is None or c_equiv > g_temp:
                return c_equiv, self.cpu_rate_ema, "CPU"
        return g_temp, self.gpu_rate_ema, "GPU"

    def _align_bumpless(self):
        """Solves for trim_rpm so total desired RPM equals current ramped_rpm (0 RPM jump)."""
        if self.ramped_rpm is None or self.ramped_rpm <= 0:
            return
        p_temp, _, _ = self._primary_temp_and_rate()
        ff = self._compute_feedforward(p_temp, self.target_temp, self.pwr_ema)
        error = p_temp - self.target_temp
        eff_err = self._soft_deadband(error)
        kp = 105.0 if eff_err >= 0 else 45.0
        clamped_ramped = max(self.RPM_FLOOR, min(self.RPM_CEIL, self.ramped_rpm))
        self.trim_rpm = max(
            self.RPM_FLOOR - ff,
            min(self.RPM_CEIL - ff, clamped_ramped - ff - (eff_err * kp)),
        )

    def reset_on_resume(self):
        """Clears stale derivatives and re-seeds filters after Windows Sleep / Modern Standby."""
        self.gpu_ema = None
        self.cpu_fast_ema = None
        self.cpu_soak_ema = None
        self.pwr_ema = None
        self.gpu_rate_ema = 0.0
        self.cpu_rate_ema = 0.0
        self.ramped_rpm = None
        self.curve_sticky_temp = None

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        self.mode = mode
        self.curve_sticky_temp = None
        if mode == "target_65":
            self._align_bumpless()

    def set_target_temp(self, temp: float):
        self.target_temp = float(temp)
        if self.mode == "target_65":
            self._align_bumpless()

    def set_manual_rpm(self, rpm: int):
        self.manual_rpm = max(2000, min(7500, int(rpm)))

    def set_curve_points(self, points: list[dict]):
        self.curve_points = clone_curve_points(points)

    def set_curve_sensor(self, sensor: str):
        if sensor != self.curve_sensor:
            self.curve_sensor = sensor
            self.curve_sticky_temp = None

    def set_safety_enabled(self, enabled: bool):
        self.safety_enabled = bool(enabled)
        if not self.safety_enabled:
            self.safety_level = 0
            self.high_temp_hold_sec = 0.0
            self.crit_temp_hold_sec = 0.0

    def step(
        self,
        dt: float,
        gpu_temp: int | None,
        cpu_temp: int | None,
        gpu_pwr: float | None,
        actual_fan_rpm: int | None,
        gpu_sleeping: bool = False,
    ) -> RegulatorStepResult:
        dt = max(0.2, min(3.0, dt))

        # Seed continuous actuator state from actual live tachometer on first run / wake
        if self.ramped_rpm is None:
            if actual_fan_rpm is not None and actual_fan_rpm >= 1500:
                seed = max(self.RPM_FLOOR, min(float(self.RPM_BOOST), float(actual_fan_rpm)))
            else:
                seed = self.RPM_FLOOR
            self.ramped_rpm = seed
            self.latched_hw_rpm = int(round(seed))
            seed_init_align = True
        else:
            seed_init_align = False

        # 1. Multi-timescale sensor filtering
        prev_gpu = self.gpu_ema
        prev_cpu_soak = self.cpu_soak_ema

        if gpu_sleeping:
            # dGPU is powered off (D3cold) or idle-released: smoothly decay GPU thermal state without jumps
            self.gpu_ema = self._ema(self.gpu_ema, 38.0, dt, tau=8.0)
            self.pwr_ema = self._ema(self.pwr_ema, 0.0, dt, tau=6.0)
        else:
            if gpu_temp is not None:
                self.gpu_ema = self._ema(self.gpu_ema, float(gpu_temp), dt, tau=3.2)
            elif cpu_temp is not None and self.gpu_ema is None:
                self.gpu_ema = max(35.0, float(cpu_temp) - 6.0)

            if gpu_pwr is not None:
                self.pwr_ema = self._ema(self.pwr_ema, float(gpu_pwr), dt, tau=6.0)

        if cpu_temp is not None:
            self.cpu_fast_ema = self._ema(self.cpu_fast_ema, float(cpu_temp), dt, tau=2.8)
            self.cpu_soak_ema = self._ema(self.cpu_soak_ema, float(cpu_temp), dt, tau=8.5)

        raw_gpu_rate = ((self.gpu_ema - prev_gpu) / dt) if (prev_gpu is not None and self.gpu_ema is not None) else 0.0
        raw_cpu_rate = ((self.cpu_soak_ema - prev_cpu_soak) / dt) if (prev_cpu_soak is not None and self.cpu_soak_ema is not None) else 0.0
        self.gpu_rate_ema = self._ema(self.gpu_rate_ema, raw_gpu_rate, dt, tau=4.0)
        self.cpu_rate_ema = self._ema(self.cpu_rate_ema, raw_cpu_rate, dt, tau=4.0)

        if seed_init_align and self.mode == "target_65":
            self._align_bumpless()

        primary_temp, primary_rate, lead_sensor = self._primary_temp_and_rate()
        eff_rate = 0.0 if abs(primary_rate) < 0.025 else primary_rate

        # Compute curve reference temp (also sent to UI so live dot matches selected sensor)
        g_disp = self.gpu_ema if self.gpu_ema is not None else primary_temp
        if self.curve_sensor == "gpu":
            curve_raw_ref = g_disp
        elif self.curve_sensor == "cpu":
            curve_raw_ref = self.cpu_fast_ema if self.cpu_fast_ema is not None else g_disp
        else:
            c_curve = (self.cpu_soak_ema - 5.0) if self.cpu_soak_ema is not None else 0.0
            curve_raw_ref = max(g_disp, c_curve)

        # 2. Compute Desired RPM by Active Mode
        desired_rpm = 0.0
        up_slew_per_sec = 60.0
        down_slew_per_sec = 32.0
        mode_desc = ""

        if self.mode == "target_65":
            target = self.target_temp
            error = primary_temp - target
            eff_err = self._soft_deadband(error, deadband=0.45, blend=0.65)
            ff = self._compute_feedforward(primary_temp, target, self.pwr_ema)

            kp = 105.0 if eff_err >= 0 else 45.0
            kd = 75.0 if eff_rate > 0 else 45.0
            d_gate = smoothstep(target - 10.0, target - 3.0, primary_temp)
            pd_term = (eff_err * kp) + (eff_rate * kd * d_gate)

            # Conditional anti-windup integration
            if eff_err > 0:
                cooling_brake = 0.2 if eff_rate < -0.03 else 1.0
                if (ff + self.trim_rpm + pd_term) <= (self.ramped_rpm + 160.0):
                    self.trim_rpm += eff_err * 7.5 * cooling_brake * dt
            elif eff_err < 0:
                if error < -4.0:
                    if self.trim_rpm > 0:
                        self.trim_rpm = max(0.0, self.trim_rpm - 18.0 * dt)
                    elif (ff + self.trim_rpm + pd_term) >= (self.ramped_rpm - 180.0):
                        self.trim_rpm += max(eff_err, -2.5) * 6.0 * dt
                else:
                    warming_brake = 0.25 if eff_rate > 0.04 else 1.0
                    if (ff + self.trim_rpm + pd_term) >= (self.ramped_rpm - 180.0):
                        self.trim_rpm += eff_err * 7.5 * warming_brake * dt

            self.trim_rpm = max(self.RPM_FLOOR - ff, min(self.RPM_CEIL - ff, self.trim_rpm))
            desired_rpm = max(self.RPM_FLOOR, min(self.RPM_CEIL, ff + self.trim_rpm + pd_term))

            # Smooth acoustic slew rate: silky near target, responsive on large surges, prompt hush when cool
            over = max(0.0, error)
            up_slew_per_sec = 52.0 + min(68.0, over * 18.0)   # 52..120 RPM/s
            under_cool = max(0.0, -error - 5.0)
            down_slew_per_sec = 28.0 + min(42.0, under_cool * 5.0)  # 28..70 RPM/s

        elif self.mode == "curve":
            if self.curve_sticky_temp is None or curve_raw_ref >= self.curve_sticky_temp:
                self.curve_sticky_temp = curve_raw_ref
            elif self.curve_sticky_temp - curve_raw_ref > 1.2:
                self.curve_sticky_temp = curve_raw_ref + 1.2

            c_rpm = interpolate_curve(self.curve_points, self.curve_sticky_temp)
            desired_rpm = max(2000.0, min(7200.0, float(c_rpm)))
            up_slew_per_sec = 75.0
            down_slew_per_sec = 38.0

        elif self.mode == "manual":
            desired_rpm = float(self.manual_rpm)
            up_slew_per_sec = 350.0
            down_slew_per_sec = 250.0

        elif self.mode == "boost":
            desired_rpm = float(self.RPM_BOOST)
            up_slew_per_sec = 3500.0
            down_slew_per_sec = 250.0

        elif self.mode == "auto":
            desired_rpm = 0.0

        # 3. Debounced Progressive Thermal Safety Protection
        raw_g = float(gpu_temp) if gpu_temp is not None else (self.gpu_ema or 0.0)
        raw_c = float(cpu_temp) if cpu_temp is not None else (self.cpu_fast_ema or 0.0)
        g_check = self.gpu_ema if self.gpu_ema is not None else raw_g
        c_check = self.cpu_fast_ema if self.cpu_fast_ema is not None else raw_c

        if self.safety_enabled:
            # Immediate emergency if extreme raw temp; 2s hold debounce on raw critical threshold (ignores 1s spikes)
            if raw_g >= 88.0 or raw_c >= 97.0:
                self.crit_temp_hold_sec = 2.5
            elif raw_g >= 84.0 or raw_c >= 92.0:
                self.crit_temp_hold_sec += dt
            else:
                self.crit_temp_hold_sec = max(0.0, self.crit_temp_hold_sec - dt)

            if raw_g >= 80.0 or raw_c >= 87.0:
                self.high_temp_hold_sec += dt
            else:
                self.high_temp_hold_sec = max(0.0, self.high_temp_hold_sec - dt)

            if self.crit_temp_hold_sec >= 2.0 - 1e-6:
                self.safety_level = 2
            elif self.high_temp_hold_sec >= 3.0 - 1e-6 and self.safety_level < 2:
                self.safety_level = 1
            elif self.safety_level > 0:
                if g_check <= 74.0 and c_check <= 79.0 and raw_g <= 75.0 and raw_c <= 80.0:
                    self.safety_level = 0
                    self.high_temp_hold_sec = 0.0
                    self.crit_temp_hold_sec = 0.0

            if self.safety_level == 2:
                desired_rpm = float(self.RPM_BOOST)
            elif self.safety_level == 1:
                desired_rpm = max(desired_rpm, 5400.0)
                up_slew_per_sec = max(up_slew_per_sec, 220.0)

        # 4. Unified Smooth Slew-Rate Limiter & Output Quantization Hysteresis
        if self.mode == "auto" and self.safety_level == 0:
            if actual_fan_rpm is not None and actual_fan_rpm >= 1500:
                self.ramped_rpm = float(actual_fan_rpm)
            self.latched_hw_rpm = 0
            return RegulatorStepResult(
                target_rpm=0,
                desired_rpm=0,
                primary_temp=primary_temp,
                curve_ref_temp=curve_raw_ref,
                lead_sensor=lead_sensor,
                safety_level=0,
                is_boost=False,
                mode_desc="Штатный алгоритм Lenovo BIOS / EC (Auto)",
            )

        is_boost = (self.mode == "boost") or (self.safety_level == 2)
        if is_boost:
            # Explicit Turbo or Critical Thermal Emergency commands immediate full saturation;
            # exiting Boost later will glide smoothly down from 7500 via down_slew_per_sec!
            self.ramped_rpm = float(self.RPM_BOOST)
            self.latched_hw_rpm = self.RPM_BOOST
            cmd_rpm = self.RPM_BOOST
        else:
            diff = desired_rpm - self.ramped_rpm
            max_up = up_slew_per_sec * dt
            max_dn = down_slew_per_sec * dt
            if diff > max_up:
                self.ramped_rpm += max_up
            elif diff < -max_dn:
                self.ramped_rpm -= max_dn
            else:
                self.ramped_rpm = desired_rpm

            cand = int(round(self.ramped_rpm))
            if self.latched_hw_rpm is None or self.latched_hw_rpm == 0:
                self.latched_hw_rpm = cand
            elif abs(cand - self.latched_hw_rpm) >= 20 or (
                self.mode == "manual" and abs(self.ramped_rpm - desired_rpm) < 1.0
            ):
                self.latched_hw_rpm = cand
            cmd_rpm = self.latched_hw_rpm

        # 5. Format clear Russian status description
        if self.safety_level == 2:
            mode_desc = f"⚠️ ТЕРМОЗАЩИТА (100% ТУРБО): GPU {g_check:.0f}°C | CPU {c_check:.0f}°C"
        elif self.safety_level == 1:
            mode_desc = f"⚠️ Усиленное охлаждение: GPU {g_check:.0f}°C | CPU {c_check:.0f}°C ➔ {cmd_rpm} RPM"
        elif self.mode == "target_65":
            err = primary_temp - self.target_temp
            if err < -5.0 and cmd_rpm <= 2250:
                phase = "Тихий режим"
            elif abs(err) <= 0.6:
                phase = "Удержание цели"
            else:
                phase = "Плавная адаптация"
            mode_desc = (
                f"{phase} • Цель {self.target_temp:.0f}°C "
                f"({lead_sensor} эфф.: {primary_temp:.1f}°C, Δ {err:+.1f}°C) ➔ {cmd_rpm} RPM"
            )
        elif self.mode == "curve":
            mode_desc = f"Кривая ({curve_raw_ref:.1f}°C) ➔ {cmd_rpm} RPM"
        elif self.mode == "manual":
            mode_desc = f"Ручной режим ➔ {cmd_rpm} RPM"
        elif self.mode == "boost":
            mode_desc = "100% Турбо (максимум ~7300–7700 RPM)"

        return RegulatorStepResult(
            target_rpm=cmd_rpm,
            desired_rpm=int(round(desired_rpm)),
            primary_temp=primary_temp,
            curve_ref_temp=curve_raw_ref,
            lead_sensor=lead_sensor,
            safety_level=self.safety_level,
            is_boost=is_boost,
            mode_desc=mode_desc,
        )


class FanControllerThread(QThread):
    """Background thread for non-blocking telemetry acquisition and smooth EC fan control."""

    telemetry_updated = Signal(dict)
    status_message = Signal(str)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config = config_manager
        self.wmi = LenovoWmiHardware()
        self.nvml = NvmlHardware()
        self.cpu_cores = CpuCoreTelemetryProvider()
        self.running = True

        self._lock = threading.RLock()
        self._wake_event = threading.Event()
        self._force_apply = True

        self.regulator = NativeSmoothRegulator(
            mode=self.config.get("mode", "target_65"),
            target_temp=float(self.config.get("target_temp", 65)),
            manual_rpm=int(self.config.get("manual_rpm", 3600)),
            curve_points=self.config.get("curve_points"),
            curve_sensor=self.config.get("curve_sensor", "max"),
            safety_enabled=bool(self.config.get("safety_override", True)),
        )

        self._last_applied_rpm: int | None = None
        self._last_applied_mode: str | None = None
        self._boost_active_in_hw = False

    @property
    def _current_mode(self) -> str:
        with self._lock:
            return self.regulator.mode

    def set_mode(self, mode: str, persist: bool = True):
        """Switches active control mode: 'target_65', 'curve', 'manual', 'auto', 'boost'."""
        with self._lock:
            self.regulator.set_mode(mode)
            self._force_apply = True
        self.config.set("mode", mode, persist=persist)
        self._wake_event.set()

    def set_target_temp(self, temp: float, persist: bool = True):
        with self._lock:
            self.regulator.set_target_temp(float(temp))
            self._force_apply = True
        self.config.set("target_temp", float(temp), persist=persist)
        self._wake_event.set()

    def set_manual_rpm(self, rpm: int, persist: bool = True):
        with self._lock:
            self.regulator.set_manual_rpm(int(rpm))
            if self.regulator.mode == "manual":
                self._force_apply = True
        self.config.set("manual_rpm", int(rpm), persist=persist)
        if self._current_mode == "manual":
            self._wake_event.set()

    def set_curve_points(self, points: list[dict], persist: bool = True):
        with self._lock:
            self.regulator.set_curve_points(points)
            if self.regulator.mode == "curve":
                self._force_apply = True
        self.config.set("curve_points", points, persist=persist)
        if self._current_mode == "curve":
            self._wake_event.set()

    def set_curve_sensor(self, sensor: str, persist: bool = True):
        with self._lock:
            self.regulator.set_curve_sensor(sensor)
            if self.regulator.mode == "curve":
                self._force_apply = True
        self.config.set("curve_sensor", sensor, persist=persist)
        if self._current_mode == "curve":
            self._wake_event.set()

    def set_safety_override(self, enabled: bool, persist: bool = True):
        with self._lock:
            self.regulator.set_safety_enabled(bool(enabled))
            self._force_apply = True
        self.config.set("safety_override", bool(enabled), persist=persist)
        self._wake_event.set()

    def stop(self):
        """Stops thread promptly and restores BIOS auto fan control."""
        self.running = False
        self._wake_event.set()
        self.wait(3000)

    def run(self):
        self.wmi.init_com()

        logger.info("FanControllerThread started (NativeSmoothRegulator + Optimus RTD3 active).")

        last_tick_time = time.monotonic()
        last_hw_write_time = 0.0
        logged_first_telemetry = False

        try:
            while self.running:
                now = time.monotonic()
                raw_dt = now - last_tick_time
                last_tick_time = now

                # Detect Windows Sleep / Modern Standby resume (wall jump > 5s)
                resumed_from_sleep = raw_dt > 5.0
                if resumed_from_sleep:
                    logger.info(f"Detected system resume ({raw_dt:.1f}s gap). Re-seeding thermal filters...")
                    with self._lock:
                        self.regulator.reset_on_resume()
                        self._force_apply = True

                dt = max(0.25, min(2.5, raw_dt if raw_dt > 0.05 else 1.0))

                # 1. Read Hardware Telemetry (Optimus D3cold-safe: never wakes sleeping dGPU!)
                gpu_info = self.nvml.poll_telemetry_rtd3_safe()
                dgpu_state = gpu_info["dgpu_state"]
                gpu_sleeping = gpu_info["gpu_sleeping"]
                gpu_temp = gpu_info["gpu_temp"]
                gpu_pwr = gpu_info["gpu_pwr"]
                gpu_util = gpu_info["gpu_util"]

                cpu_temp = self.wmi.get_cpu_temp()
                cpu_details = self.cpu_cores.poll(ec_cpu_temp=cpu_temp)
                if cpu_temp is None and cpu_details.get("avg_temp") is not None:
                    cpu_temp = int(round(cpu_details["avg_temp"]))
                fan1_rpm = self.wmi.get_fan1_rpm()
                fan2_rpm = self.wmi.get_fan2_rpm()
                boost_state = self.wmi.get_boost_state()

                # Representative live fan speed for bumpless seeding
                valid_fans = [f for f in (fan1_rpm, fan2_rpm) if f is not None and f > 0]
                actual_rpm = int(round(sum(valid_fans) / len(valid_fans))) if valid_fans else None

                # 2. Compute Smooth Control Step under lock
                with self._lock:
                    cur_mode = self.regulator.mode
                    force_apply = self._force_apply
                    self._force_apply = False
                    step_res = self.regulator.step(
                        dt=dt,
                        gpu_temp=gpu_temp,
                        cpu_temp=cpu_temp,
                        gpu_pwr=gpu_pwr,
                        actual_fan_rpm=actual_rpm,
                        gpu_sleeping=gpu_sleeping,
                    )

                target_rpm = step_res.target_rpm
                is_auto_idle = (cur_mode == "auto" and step_res.safety_level == 0)

                # 3. Dispatch Command to Lenovo EC WMI
                # - In BIOS Auto mode: send reset_to_auto() ONCE on mode entry/resume, never spam!
                # - In active software modes: write immediately on RPM/mode change, plus anti-watchdog refresh every 3.5s
                mode_changed = (cur_mode != self._last_applied_mode)
                rpm_changed = (target_rpm != self._last_applied_rpm)
                watchdog_due = (not is_auto_idle) and ((now - last_hw_write_time) >= 3.5)

                if force_apply or mode_changed or rpm_changed or watchdog_due:
                    applied_ok = False
                    if step_res.is_boost:
                        applied_ok = self.wmi.set_boost(True, target_rpm)
                        if applied_ok:
                            self._boost_active_in_hw = True
                    else:
                        if self._boost_active_in_hw or self._last_applied_mode == "boost" or boost_state == 1:
                            self.wmi.clear_boost()
                            self._boost_active_in_hw = False
                        if is_auto_idle:
                            applied_ok = self.wmi.reset_to_auto()
                        else:
                            applied_ok = self.wmi.set_both_fans(target_rpm)

                    if applied_ok:
                        self._last_applied_mode = cur_mode
                        self._last_applied_rpm = target_rpm
                        last_hw_write_time = now
                        if not logged_first_telemetry:
                            logged_first_telemetry = True
                            logger.info(
                                f"Hardware handshake OK: GPU={gpu_temp}°C ({gpu_pwr}W), "
                                f"CPU={cpu_temp}°C, Fan1={fan1_rpm} RPM, Fan2={fan2_rpm} RPM -> "
                                f"Mode={cur_mode}, Cmd={target_rpm} RPM"
                            )
                    else:
                        # Retry next tick if WMI failed transiently
                        with self._lock:
                            self._force_apply = True

                # 4. Emit Live Telemetry to GUI
                wmi_ok = self.wmi.is_connected
                wmi_err = self.wmi.last_error
                mode_desc = step_res.mode_desc if wmi_ok else f"❌ Нет доступа к Lenovo EC WMI ({wmi_err or 'ошибка'})"

                telemetry_data = {
                    "dgpu_state": dgpu_state,
                    "gpu_sleeping": gpu_sleeping,
                    "gpu_temp": gpu_temp,
                    "gpu_pwr": gpu_pwr,
                    "gpu_util": gpu_util[0] if gpu_util else None,
                    "mem_util": gpu_util[1] if gpu_util else None,
                    "cpu_temp": cpu_temp,
                    "cpu_details": cpu_details,
                    "fan1_rpm": fan1_rpm,
                    "fan2_rpm": fan2_rpm,
                    "boost_state": boost_state,
                    "mode": cur_mode,
                    "target_rpm": target_rpm,
                    "desired_rpm": step_res.desired_rpm,
                    "primary_temp": round(step_res.primary_temp, 1),
                    "curve_ref_temp": round(step_res.curve_ref_temp, 1),
                    "lead_sensor": step_res.lead_sensor,
                    "safety_level": step_res.safety_level,
                    "wmi_ok": wmi_ok,
                    "wmi_error": wmi_err,
                    "mode_desc": mode_desc,
                }
                self.telemetry_updated.emit(telemetry_data)

                poll_sec = float(self.config.get("poll_interval_sec", 1.0))
                poll_sec = max(0.5, min(2.0, poll_sec))
                self._wake_event.wait(poll_sec)
                self._wake_event.clear()

        finally:
            logger.info("Controller exiting, restoring BIOS auto fan mode...")
            try:
                self.wmi.reset_to_auto()
            except Exception:
                pass
            self.cpu_cores.shutdown()
            self.nvml.shutdown()
            self.wmi.uninit_com()
            logger.info("Controller cleanup complete.")
