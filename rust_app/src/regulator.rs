use crate::config::{sanitize_curve, CurvePoint};

pub const RPM_FLOOR: f64 = 2100.0;
pub const RPM_CEIL: f64 = 6800.0;
pub const RPM_BOOST: i32 = 7500;

#[inline]
pub fn smoothstep(edge0: f64, edge1: f64, x: f64) -> f64 {
    if edge1 <= edge0 {
        return if x >= edge1 { 1.0 } else { 0.0 };
    }
    let t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

pub fn interpolate_curve(points: &[CurvePoint], temp: f64) -> i32 {
    if points.is_empty() {
        return 3600;
    }
    let mut sorted = points.to_vec();
    sorted.sort_by_key(|p| p.temp);

    if temp <= sorted[0].temp as f64 {
        return sorted[0].rpm;
    }
    let last = sorted.last().unwrap();
    if temp >= last.temp as f64 {
        return last.rpm;
    }

    for w in sorted.windows(2) {
        let p0 = &w[0];
        let p1 = &w[1];
        let t0 = p0.temp as f64;
        let t1 = p1.temp as f64;
        if temp >= t0 && temp <= t1 {
            let span = t1 - t0;
            if span <= 0.0 {
                return p0.rpm;
            }
            let ratio = (temp - t0) / span;
            let rpm = (p0.rpm as f64) + ratio * ((p1.rpm - p0.rpm) as f64);
            return rpm.round() as i32;
        }
    }
    last.rpm
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct RegulatorStepResult {
    pub target_rpm: i32,
    pub desired_rpm: i32,
    pub primary_temp: f64,
    pub curve_ref_temp: f64,
    pub lead_sensor: &'static str,
    pub safety_level: u8,
    pub is_boost: bool,
    pub mode_desc: String,
}

pub struct NativeSmoothRegulator {
    pub mode: String,
    pub target_temp: f64,
    pub manual_rpm: i32,
    pub curve_points: Vec<CurvePoint>,
    pub curve_sensor: String,
    pub safety_enabled: bool,

    pub gpu_ema: Option<f64>,
    pub cpu_fast_ema: Option<f64>,
    pub cpu_soak_ema: Option<f64>,
    pub pwr_ema: Option<f64>,
    pub gpu_rate_ema: f64,
    pub cpu_rate_ema: f64,

    pub trim_rpm: f64,
    pub ramped_rpm: Option<f64>,
    pub latched_hw_rpm: Option<i32>,
    pub curve_sticky_temp: Option<f64>,

    pub safety_level: u8,
    pub high_temp_hold_sec: f64,
    pub crit_temp_hold_sec: f64,
}

impl NativeSmoothRegulator {
    pub fn new(
        mode: &str,
        target_temp: f64,
        manual_rpm: i32,
        curve_points: &[CurvePoint],
        curve_sensor: &str,
        safety_enabled: bool,
    ) -> Self {
        Self {
            mode: mode.to_string(),
            target_temp,
            manual_rpm: manual_rpm.clamp(2000, 7500),
            curve_points: sanitize_curve(curve_points),
            curve_sensor: curve_sensor.to_string(),
            safety_enabled,
            gpu_ema: None,
            cpu_fast_ema: None,
            cpu_soak_ema: None,
            pwr_ema: None,
            gpu_rate_ema: 0.0,
            cpu_rate_ema: 0.0,
            trim_rpm: 0.0,
            ramped_rpm: None,
            latched_hw_rpm: None,
            curve_sticky_temp: None,
            safety_level: 0,
            high_temp_hold_sec: 0.0,
            crit_temp_hold_sec: 0.0,
        }
    }

    #[inline]
    fn ema(prev: Option<f64>, sample: f64, dt: f64, tau: f64) -> f64 {
        match prev {
            None => sample,
            Some(p) => {
                let alpha = 1.0 - (-dt / tau.max(0.1)).exp();
                p + alpha * (sample - p)
            }
        }
    }

    #[inline]
    fn soft_deadband(error: f64, deadband: f64, blend: f64) -> f64 {
        let ae = error.abs();
        if ae <= deadband {
            return 0.0;
        }
        let sign = if error > 0.0 { 1.0 } else { -1.0 };
        let excess = ae - deadband;
        if excess < blend {
            sign * (excess * excess) / (2.0 * blend)
        } else {
            sign * (excess - 0.5 * blend)
        }
    }

    fn compute_feedforward(&self, primary_temp: f64, target: f64, pwr_soak: Option<f64>) -> f64 {
        let nominal_at_target = (3550.0 - (target - 65.0) * 140.0).clamp(2700.0, 4600.0);
        let quiet_temp = 47.0_f64.min(target - 16.0);
        let s = smoothstep(quiet_temp, target - 1.0, primary_temp);
        let mut ff = RPM_FLOOR + s * (nominal_at_target - RPM_FLOOR);

        if let Some(pw) = pwr_soak {
            if pw > 18.0 {
                let pwr_gate = smoothstep(quiet_temp - 2.0, target - 3.0, primary_temp);
                ff += pwr_gate * ((pw - 18.0) * 21.0).min(1600.0);
            }
        }
        ff.clamp(RPM_FLOOR, RPM_CEIL)
    }

    fn primary_temp_and_rate(&self) -> (f64, f64, &'static str) {
        let g_temp = self.gpu_ema.unwrap_or(50.0);
        if let Some(c_soak) = self.cpu_soak_ema {
            let c_equiv = c_soak - 8.0;
            if self.gpu_ema.is_none() || c_equiv > g_temp {
                return (c_equiv, self.cpu_rate_ema, "CPU");
            }
        }
        (g_temp, self.gpu_rate_ema, "GPU")
    }

    fn align_bumpless(&mut self) {
        let Some(ramped) = self.ramped_rpm else { return };
        if ramped <= 0.0 {
            return;
        }
        let (p_temp, _, _) = self.primary_temp_and_rate();
        let ff = self.compute_feedforward(p_temp, self.target_temp, self.pwr_ema);
        let error = p_temp - self.target_temp;
        let eff_err = Self::soft_deadband(error, 0.45, 0.65);
        let kp = if eff_err >= 0.0 { 105.0 } else { 45.0 };
        let clamped_ramped = ramped.clamp(RPM_FLOOR, RPM_CEIL);
        self.trim_rpm = (clamped_ramped - ff - eff_err * kp).clamp(RPM_FLOOR - ff, RPM_CEIL - ff);
    }

    pub fn reset_on_resume(&mut self) {
        self.gpu_ema = None;
        self.cpu_fast_ema = None;
        self.cpu_soak_ema = None;
        self.pwr_ema = None;
        self.gpu_rate_ema = 0.0;
        self.cpu_rate_ema = 0.0;
        self.ramped_rpm = None;
        self.curve_sticky_temp = None;
    }

    pub fn set_mode(&mut self, mode: &str) {
        if mode == self.mode {
            return;
        }
        self.mode = mode.to_string();
        self.curve_sticky_temp = None;
        if self.mode == "target_65" {
            self.align_bumpless();
        }
    }

    pub fn set_target_temp(&mut self, temp: f64) {
        self.target_temp = temp.clamp(50.0, 80.0);
        if self.mode == "target_65" {
            self.align_bumpless();
        }
    }

    pub fn set_manual_rpm(&mut self, rpm: i32) {
        self.manual_rpm = rpm.clamp(2000, 7500);
    }

    pub fn set_curve_points(&mut self, pts: &[CurvePoint]) {
        self.curve_points = sanitize_curve(pts);
    }

    pub fn set_curve_sensor(&mut self, sensor: &str) {
        if sensor != self.curve_sensor {
            self.curve_sensor = sensor.to_string();
            self.curve_sticky_temp = None;
        }
    }

    pub fn set_safety_enabled(&mut self, enabled: bool) {
        self.safety_enabled = enabled;
        if !enabled {
            self.safety_level = 0;
            self.high_temp_hold_sec = 0.0;
            self.crit_temp_hold_sec = 0.0;
        }
    }

    pub fn step(
        &mut self,
        dt_raw: f64,
        gpu_temp: Option<i32>,
        cpu_temp: Option<i32>,
        gpu_pwr: Option<f64>,
        actual_fan_rpm: Option<i32>,
        gpu_sleeping: bool,
    ) -> RegulatorStepResult {
        let dt = dt_raw.clamp(0.2, 3.0);

        let seed_init_align = if self.ramped_rpm.is_none() {
            let seed = match actual_fan_rpm {
                Some(r) if r >= 1500 => (r as f64).clamp(RPM_FLOOR, RPM_BOOST as f64),
                _ => RPM_FLOOR,
            };
            self.ramped_rpm = Some(seed);
            self.latched_hw_rpm = Some(seed.round() as i32);
            true
        } else {
            false
        };

        let prev_gpu = self.gpu_ema;
        let prev_cpu_soak = self.cpu_soak_ema;

        if gpu_sleeping {
            self.gpu_ema = Some(Self::ema(self.gpu_ema, 38.0, dt, 8.0));
            self.pwr_ema = Some(Self::ema(self.pwr_ema, 0.0, dt, 6.0));
        } else {
            if let Some(gt) = gpu_temp {
                self.gpu_ema = Some(Self::ema(self.gpu_ema, gt as f64, dt, 3.2));
            } else if let (Some(ct), None) = (cpu_temp, self.gpu_ema) {
                self.gpu_ema = Some(((ct as f64) - 6.0).max(35.0));
            }
            if let Some(gp) = gpu_pwr {
                self.pwr_ema = Some(Self::ema(self.pwr_ema, gp, dt, 6.0));
            }
        }

        if let Some(ct) = cpu_temp {
            self.cpu_fast_ema = Some(Self::ema(self.cpu_fast_ema, ct as f64, dt, 2.8));
            self.cpu_soak_ema = Some(Self::ema(self.cpu_soak_ema, ct as f64, dt, 8.5));
        }

        let raw_g_rate = match (prev_gpu, self.gpu_ema) {
            (Some(p), Some(c)) => (c - p) / dt,
            _ => 0.0,
        };
        let raw_c_rate = match (prev_cpu_soak, self.cpu_soak_ema) {
            (Some(p), Some(c)) => (c - p) / dt,
            _ => 0.0,
        };
        self.gpu_rate_ema = Self::ema(Some(self.gpu_rate_ema), raw_g_rate, dt, 4.0);
        self.cpu_rate_ema = Self::ema(Some(self.cpu_rate_ema), raw_c_rate, dt, 4.0);

        if seed_init_align && self.mode == "target_65" {
            self.align_bumpless();
        }

        let (primary_temp, primary_rate, lead_sensor) = self.primary_temp_and_rate();
        let eff_rate = if primary_rate.abs() < 0.025 { 0.0 } else { primary_rate };

        let g_disp = self.gpu_ema.unwrap_or(primary_temp);
        let curve_raw_ref = match self.curve_sensor.as_str() {
            "gpu" => g_disp,
            "cpu" => self.cpu_fast_ema.unwrap_or(g_disp),
            _ => {
                let c_curve = self.cpu_soak_ema.map(|v| v - 5.0).unwrap_or(0.0);
                g_disp.max(c_curve)
            }
        };

        let mut desired_rpm: f64;
        let mut up_slew_per_sec = 60.0;
        let mut down_slew_per_sec = 32.0;

        match self.mode.as_str() {
            "target_65" => {
                let target = self.target_temp;
                let error = primary_temp - target;
                let eff_err = Self::soft_deadband(error, 0.45, 0.65);
                let ff = self.compute_feedforward(primary_temp, target, self.pwr_ema);

                let kp = if eff_err >= 0.0 { 105.0 } else { 45.0 };
                let kd = if eff_rate > 0.0 { 75.0 } else { 45.0 };
                let d_gate = smoothstep(target - 10.0, target - 3.0, primary_temp);
                let pd_term = (eff_err * kp) + (eff_rate * kd * d_gate);
                let cur_ramped = self.ramped_rpm.unwrap_or(RPM_FLOOR);

                if eff_err > 0.0 {
                    let cooling_brake = if eff_rate < -0.03 { 0.2 } else { 1.0 };
                    if (ff + self.trim_rpm + pd_term) <= (cur_ramped + 160.0) {
                        self.trim_rpm += eff_err * 7.5 * cooling_brake * dt;
                    }
                } else if eff_err < 0.0 {
                    if error < -4.0 {
                        if self.trim_rpm > 0.0 {
                            self.trim_rpm = (self.trim_rpm - 18.0 * dt).max(0.0);
                        } else if (ff + self.trim_rpm + pd_term) >= (cur_ramped - 180.0) {
                            self.trim_rpm += eff_err.max(-2.5) * 6.0 * dt;
                        }
                    } else {
                        let warming_brake = if eff_rate > 0.04 { 0.25 } else { 1.0 };
                        if (ff + self.trim_rpm + pd_term) >= (cur_ramped - 180.0) {
                            self.trim_rpm += eff_err * 7.5 * warming_brake * dt;
                        }
                    }
                }

                self.trim_rpm = self.trim_rpm.clamp(RPM_FLOOR - ff, RPM_CEIL - ff);
                desired_rpm = (ff + self.trim_rpm + pd_term).clamp(RPM_FLOOR, RPM_CEIL);

                let over = error.max(0.0);
                up_slew_per_sec = 52.0 + (over * 18.0).min(68.0);
                let under_cool = (-error - 5.0).max(0.0);
                down_slew_per_sec = 28.0 + (under_cool * 5.0).min(42.0);
            }
            "curve" => {
                let st = match self.curve_sticky_temp {
                    None => curve_raw_ref,
                    Some(prev_s) if curve_raw_ref >= prev_s => curve_raw_ref,
                    Some(prev_s) if (prev_s - curve_raw_ref) > 1.2 => curve_raw_ref + 1.2,
                    Some(prev_s) => prev_s,
                };
                self.curve_sticky_temp = Some(st);
                let c_rpm = interpolate_curve(&self.curve_points, st);
                desired_rpm = (c_rpm as f64).clamp(2000.0, 7200.0);
                up_slew_per_sec = 75.0;
                down_slew_per_sec = 38.0;
            }
            "manual" => {
                desired_rpm = self.manual_rpm as f64;
                up_slew_per_sec = 350.0;
                down_slew_per_sec = 250.0;
            }
            "boost" => {
                desired_rpm = RPM_BOOST as f64;
                up_slew_per_sec = 3500.0;
                down_slew_per_sec = 250.0;
            }
            _ => {
                desired_rpm = 0.0;
            }
        }

        // Debounced progressive thermal safety protection
        let raw_g = gpu_temp.map(|v| v as f64).or(self.gpu_ema).unwrap_or(0.0);
        let raw_c = cpu_temp.map(|v| v as f64).or(self.cpu_fast_ema).unwrap_or(0.0);
        let g_check = self.gpu_ema.unwrap_or(raw_g);
        let c_check = self.cpu_fast_ema.unwrap_or(raw_c);

        if self.safety_enabled {
            if raw_g >= 88.0 || raw_c >= 97.0 {
                self.crit_temp_hold_sec = 2.5;
            } else if raw_g >= 84.0 || raw_c >= 92.0 {
                self.crit_temp_hold_sec += dt;
            } else {
                self.crit_temp_hold_sec = (self.crit_temp_hold_sec - dt).max(0.0);
            }

            if raw_g >= 80.0 || raw_c >= 87.0 {
                self.high_temp_hold_sec += dt;
            } else {
                self.high_temp_hold_sec = (self.high_temp_hold_sec - dt).max(0.0);
            }

            if self.crit_temp_hold_sec >= 2.0 - 1e-6 {
                self.safety_level = 2;
            } else if self.high_temp_hold_sec >= 3.0 - 1e-6 && self.safety_level < 2 {
                self.safety_level = 1;
            } else if self.safety_level > 0
                && g_check <= 74.0
                && c_check <= 79.0
                && raw_g <= 75.0
                && raw_c <= 80.0
            {
                self.safety_level = 0;
                self.high_temp_hold_sec = 0.0;
                self.crit_temp_hold_sec = 0.0;
            }

            if self.safety_level == 2 {
                desired_rpm = RPM_BOOST as f64;
            } else if self.safety_level == 1 {
                desired_rpm = desired_rpm.max(5400.0);
                up_slew_per_sec = up_slew_per_sec.max(220.0);
            }
        }

        if self.mode == "auto" && self.safety_level == 0 {
            if let Some(act) = actual_fan_rpm {
                if act >= 1500 {
                    self.ramped_rpm = Some(act as f64);
                }
            }
            self.latched_hw_rpm = Some(0);
            return RegulatorStepResult {
                target_rpm: 0,
                desired_rpm: 0,
                primary_temp,
                curve_ref_temp: curve_raw_ref,
                lead_sensor,
                safety_level: 0,
                is_boost: false,
                mode_desc: "Штатный алгоритм Lenovo BIOS / EC (Auto)".to_string(),
            };
        }

        let is_boost = (self.mode == "boost") || (self.safety_level == 2);
        let cmd_rpm = if is_boost {
            self.ramped_rpm = Some(RPM_BOOST as f64);
            self.latched_hw_rpm = Some(RPM_BOOST);
            RPM_BOOST
        } else {
            let mut r = self.ramped_rpm.unwrap_or(RPM_FLOOR);
            let diff = desired_rpm - r;
            let max_up = up_slew_per_sec * dt;
            let max_dn = down_slew_per_sec * dt;
            if diff > max_up {
                r += max_up;
            } else if diff < -max_dn {
                r -= max_dn;
            } else {
                r = desired_rpm;
            }
            self.ramped_rpm = Some(r);

            let cand = r.round() as i32;
            let latched = match self.latched_hw_rpm {
                None | Some(0) => cand,
                Some(prev_l) => {
                    if (cand - prev_l).abs() >= 20
                        || (self.mode == "manual" && (r - desired_rpm).abs() < 1.0)
                    {
                        cand
                    } else {
                        prev_l
                    }
                }
            };
            self.latched_hw_rpm = Some(latched);
            latched
        };

        let mode_desc = if self.safety_level == 2 {
            format!("⚠️ ТЕРМОЗАЩИТА (100% ТУРБО): GPU {:.0}°C | CPU {:.0}°C", g_check, c_check)
        } else if self.safety_level == 1 {
            format!(
                "⚠️ Усиленное охлаждение: GPU {:.0}°C | CPU {:.0}°C ➔ {} RPM",
                g_check, c_check, cmd_rpm
            )
        } else {
            match self.mode.as_str() {
                "target_65" => {
                    let err = primary_temp - self.target_temp;
                    let phase = if gpu_sleeping && err < -5.0 {
                        "Тихий режим (iGPU)"
                    } else if err < -5.0 && cmd_rpm <= 2250 {
                        "Тихий режим"
                    } else if err.abs() <= 0.6 {
                        "Удержание цели"
                    } else {
                        "Плавная адаптация"
                    };
                    format!(
                        "{} • Цель {:.0}°C ({} эфф.: {:.1}°C, Δ {:+.1}°C) ➔ {} RPM",
                        phase, self.target_temp, lead_sensor, primary_temp, err, cmd_rpm
                    )
                }
                "curve" => format!("Кривая ({:.1}°C) ➔ {} RPM", curve_raw_ref, cmd_rpm),
                "manual" => format!("Ручной режим ➔ {} RPM", cmd_rpm),
                "boost" => "100% Турбо (максимум ~7300–7700 RPM)".to_string(),
                _ => "Штатный алгоритм Lenovo BIOS / EC (Auto)".to_string(),
            }
        };

        RegulatorStepResult {
            target_rpm: cmd_rpm,
            desired_rpm: desired_rpm.round() as i32,
            primary_temp,
            curve_ref_temp: curve_raw_ref,
            lead_sensor,
            safety_level: self.safety_level,
            is_boost,
            mode_desc,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::default_curve;

    #[test]
    fn test_cold_idle_startup_no_surge() {
        let mut reg = NativeSmoothRegulator::new("target_65", 65.0, 3600, &default_curve(), "max", true);
        let mut fan_rpm = 2100;
        for _ in 0..30 {
            let res = reg.step(1.0, Some(41), Some(44), Some(5.0), Some(fan_rpm), false);
            fan_rpm = res.target_rpm;
            assert!(fan_rpm <= 2150, "Unexpected surge: {}", fan_rpm);
        }
        assert_eq!(fan_rpm, 2100);
    }

    #[test]
    fn test_transient_cpu_spike_no_roar() {
        let mut reg = NativeSmoothRegulator::new("target_65", 65.0, 3600, &default_curve(), "max", true);
        let mut fan_rpm = 2100;
        for _ in 0..15 {
            fan_rpm = reg.step(1.0, Some(46), Some(48), Some(8.0), Some(fan_rpm), false).target_rpm;
        }
        let mut min_r = i32::MAX;
        let mut max_r = i32::MIN;
        for s in 0..15 {
            let c_in = if s == 1 || s == 2 { 85 } else { 48 };
            fan_rpm = reg.step(1.0, Some(46), Some(c_in), Some(8.0), Some(fan_rpm), false).target_rpm;
            min_r = min_r.min(fan_rpm);
            max_r = max_r.max(fan_rpm);
        }
        assert!(max_r - min_r <= 60);
    }

    #[test]
    fn test_heavy_load_zero_steady_hunting() {
        let mut reg = NativeSmoothRegulator::new("target_65", 65.0, 3600, &default_curve(), "max", true);
        let t_amb = 24.0;
        let mut t_pipe = 40.0;
        let mut t_gpu = 41.0;
        let mut t_cpu = 44.0;
        let mut fan_rpm = 2100;
        let mut prev = fan_rpm;
        let mut max_step = 0;
        let mut steady = Vec::new();

        for s in 0..280 {
            let g_cool = 0.65 + (fan_rpm as f64 / 1000.0) * 0.48;
            t_pipe += (100.0 - g_cool * (t_pipe - t_amb)) / 95.0;
            t_gpu += ((t_pipe + 75.0 * 0.11) - t_gpu) / 3.5;
            t_cpu += ((t_pipe + 25.0 * 0.32) - t_cpu) / 2.5;

            let res = reg.step(
                1.0,
                Some(t_gpu.round() as i32),
                Some(t_cpu.round() as i32),
                Some(75.0),
                Some(fan_rpm),
                false,
            );
            max_step = max_step.max((res.target_rpm - prev).abs());
            prev = res.target_rpm;
            fan_rpm = res.target_rpm;
            if s >= 215 {
                steady.push(fan_rpm);
            }
        }
        assert!(max_step <= 130);
        assert!((t_gpu - 65.0).abs() <= 0.5);
        assert_eq!(steady.iter().max().unwrap() - steady.iter().min().unwrap(), 0);
    }

    #[test]
    fn test_curve_hysteresis_no_flicker() {
        let mut reg = NativeSmoothRegulator::new("curve", 65.0, 3600, &default_curve(), "gpu", true);
        let mut fan_rpm = 3500;
        let mut hist = Vec::new();
        for s in 0..40 {
            let g = if s % 2 == 0 { 65 } else { 64 };
            fan_rpm = reg.step(1.0, Some(g), Some(60), Some(40.0), Some(fan_rpm), false).target_rpm;
            hist.push(fan_rpm);
        }
        let tail = &hist[25..];
        assert!(tail.iter().all(|&x| x == tail[0]));
    }
}
