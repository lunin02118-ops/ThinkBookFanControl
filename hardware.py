"""
Hardware abstraction layer for Lenovo ThinkBook 14 G6+ IMH (Type 21LD)
Accesses:
- WMI root\\wmi -> LENOVO_OTHER_METHOD for Fan 1, Fan 2, CPU Temp, Boost
- Zero-wake CfgMgr32 + PDH Optimus RTD3 (D3cold) state monitor so RTX 4060 can power off to 0W!
- nvml.dll for NVIDIA GeForce RTX 4060 Laptop GPU metrics only when dGPU is actively used
"""

import ctypes
from ctypes import wintypes
import json
import os
import re
import subprocess
import sys
import time
import uuid
import logging
import pythoncom
import win32com.client

logger = logging.getLogger("ThinkBookHardware")

# Lenovo ACPI/EC WMI Feature IDs (LENOVO_OTHER_METHOD)
ID_FAN1_RPM = 0x04030001      # 67305473: Set/Get Fan 1 RPM (0 = Auto, 2000..7700)
ID_FAN2_RPM = 0x04030002      # 67305474: Set/Get Fan 2 RPM (0 = Auto, 2000..7700)
ID_FAN_BOOST = 0x04020000     # 67239936: Full Fan Boost 100% (1 = On, 0 = Off)
ID_CPU_TEMP = 0x05040000      # 84148224: CPU Temperature in °C

WBEM_E_ACCESS_DENIED = -2147217405

# Windows DEVICE_POWER_STATE enum values in CM_POWER_DATA.PD_MostRecentPowerState
POWER_DEVICE_D0 = 1
POWER_DEVICE_D3 = 4


class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", wintypes.ULONG)]


class _PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [
        ("szName", wintypes.LPWSTR),
        ("FmtValue_CStatus", wintypes.DWORD),
        ("_pad", wintypes.DWORD),
        ("doubleValue", ctypes.c_double),
    ]


def _make_devpropkey(guid_str: str, pid: int) -> _DEVPROPKEY:
    raw = uuid.UUID(guid_str).bytes_le
    return _DEVPROPKEY((ctypes.c_ubyte * 16).from_buffer_copy(raw), pid)


# DEVPKEY_Device_PowerData: {A45C254E-DF1C-4EFD-8020-67D146A850E0}, 32
DEVPKEY_DEVICE_POWER_DATA = _make_devpropkey("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 32)
# DEVPKEY_DisplayAdapter_Luid: {60B193CB-5276-4D0F-96FC-F173ABAD3EC6}, 2
DEVPKEY_DISPLAY_ADAPTER_LUID = _make_devpropkey("{60B193CB-5276-4D0F-96FC-F173ABAD3EC6}", 2)
# DEVPKEY_PciDevice_BaseClass: {3AB22E31-8264-4B4E-9AF5-A8D2D8E33E62}, 3
DEVPKEY_PCI_BASE_CLASS = _make_devpropkey("{3AB22E31-8264-4B4E-9AF5-A8D2D8E33E62}", 3)


class OptimusRtd3Detector:
    """
    Zero-wake Windows kernel PnP (CfgMgr32) + WDDM Performance Counter (PDH) monitor.
    Reads dGPU D-state (D0 vs D3cold) and DirectX engine/VRAM activity in <1.5 ms
    WITHOUT calling nvml.dll or waking the NVIDIA GPU over PCIe!
    """

    def __init__(self):
        self._cfgmgr = None
        self._pdh = None
        self._devinst = None
        self._luid_tag: str | None = None
        self._pdh_query = None
        self._cnt_util = None
        self._cnt_vram = None
        self._initialized = False

    def _init_handles(self) -> bool:
        if self._initialized:
            return self._devinst is not None
        self._initialized = True
        try:
            self._cfgmgr = ctypes.WinDLL("CfgMgr32.dll")
            # Enumerate present Display Class devices: {4d36e968-e325-11ce-bfc1-08002be10318}
            disp_class = "{4d36e968-e325-11ce-bfc1-08002be10318}"
            # CM_GETIDLIST_FILTER_CLASS | CM_GETIDLIST_FILTER_PRESENT = 0x300
            flags = 0x00000300
            ul_len = wintypes.ULONG(0)
            if self._cfgmgr.CM_Get_Device_ID_List_SizeW(ctypes.byref(ul_len), disp_class, flags) == 0 and ul_len.value > 1:
                buf = (ctypes.c_wchar * ul_len.value)()
                if self._cfgmgr.CM_Get_Device_ID_ListW(disp_class, buf, ul_len.value, flags) == 0:
                    raw_str = ctypes.wstring_at(buf, ul_len.value)
                    for inst_id in raw_str.split("\x00"):
                        if not inst_id.upper().startswith("PCI\\VEN_10DE&"):
                            continue
                        dn = wintypes.DWORD(0)
                        if self._cfgmgr.CM_Locate_DevNodeW(ctypes.byref(dn), inst_id, 0) != 0:
                            continue
                        self._devinst = dn
                        # Read 64-bit Adapter LUID to match PDH GPU counters accurately
                        pt = wintypes.ULONG(0)
                        luid_buf = (ctypes.c_ubyte * 16)()
                        bl_luid = wintypes.ULONG(16)
                        if self._cfgmgr.CM_Get_DevNode_PropertyW(
                            dn, ctypes.byref(DEVPKEY_DISPLAY_ADAPTER_LUID), ctypes.byref(pt), luid_buf, ctypes.byref(bl_luid), 0
                        ) == 0:
                            low_part = int.from_bytes(bytes(luid_buf[0:4]), "little")
                            high_part = int.from_bytes(bytes(luid_buf[4:8]), "little")
                            self._luid_tag = f"luid_0x{high_part:08x}_0x{low_part:08x}".lower()
                        break

            # Setup language-independent PDH counters for WDDM GPU activity
            self._pdh = ctypes.WinDLL("pdh.dll")
            hq = wintypes.HANDLE()
            if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(hq)) == 0:
                self._pdh_query = hq
                cu = wintypes.HANDLE()
                cv = wintypes.HANDLE()
                if self._pdh.PdhAddEnglishCounterW(hq, r"\GPU Engine(*)\Utilization Percentage", None, ctypes.byref(cu)) == 0:
                    self._cnt_util = cu
                if self._pdh.PdhAddEnglishCounterW(hq, r"\GPU Adapter Memory(*)\Dedicated Usage", None, ctypes.byref(cv)) == 0:
                    self._cnt_vram = cv
                self._pdh.PdhCollectQueryData(hq)
        except Exception as e:
            logger.debug(f"OptimusRtd3Detector init fallback: {e}")

        return self._devinst is not None

    def get_power_state(self) -> int | None:
        """Returns 1 for D0 (awake) or 4 for D3/D3cold (asleep, 0W). Never wakes GPU."""
        if not self._init_handles() or self._devinst is None:
            return None
        try:
            buf = (ctypes.c_ubyte * 128)()
            pt = wintypes.ULONG(0)
            bl = wintypes.ULONG(128)
            cr = self._cfgmgr.CM_Get_DevNode_PropertyW(
                self._devinst, ctypes.byref(DEVPKEY_DEVICE_POWER_DATA), ctypes.byref(pt), buf, ctypes.byref(bl), 0
            )
            if cr == 0 and bl.value >= 8:
                return int.from_bytes(bytes(buf[4:8]), "little")
        except Exception:
            pass
        return None

    def _read_pdh_array(self, counter_handle) -> list[tuple[str, float]]:
        if not self._pdh or not counter_handle:
            return []
        bs = wintypes.DWORD(0)
        ic = wintypes.DWORD(0)
        # PDH_FMT_DOUBLE = 0x00000200
        self._pdh.PdhGetFormattedCounterArrayW(counter_handle, 0x00000200, ctypes.byref(bs), ctypes.byref(ic), None)
        if bs.value == 0 or ic.value == 0:
            return []
        buf = (ctypes.c_byte * bs.value)()
        if self._pdh.PdhGetFormattedCounterArrayW(counter_handle, 0x00000200, ctypes.byref(bs), ctypes.byref(ic), buf) != 0:
            return []
        arr = ctypes.cast(buf, ctypes.POINTER(_PDH_FMT_COUNTERVALUE_ITEM_W))
        out = []
        for i in range(ic.value):
            name = arr[i].szName or ""
            out.append((name.lower(), float(arr[i].doubleValue)))
        return out

    def get_wddm_dgpu_activity(self) -> tuple[float, float]:
        """Returns (total_engine_util_percent, dedicated_vram_mb) for NVIDIA dGPU via WDDM without waking it."""
        self._init_handles()
        if not self._pdh_query:
            return 0.0, 0.0
        try:
            self._pdh.PdhCollectQueryData(self._pdh_query)
            vram_items = self._read_pdh_array(self._cnt_vram)

            # Discrete NVIDIA GPU is the adapter with Dedicated VRAM > 0 (Intel Arc iGPU has 0 Dedicated VRAM)
            if self._luid_tag is None:
                for name, val in vram_items:
                    if val > 0.0 and "luid_0x" in name:
                        # Extract 'luid_0x00000000_0x00011887' prefix
                        parts = name.split("_phys_")[0]
                        if parts.startswith("luid_0x"):
                            self._luid_tag = parts
                            break

            tag = self._luid_tag
            ded_mb = 0.0
            for name, val in vram_items:
                if tag is not None:
                    if tag in name:
                        ded_mb = max(ded_mb, val / (1024.0 * 1024.0))
                elif val > 0.0:
                    ded_mb = max(ded_mb, val / (1024.0 * 1024.0))

            total_util = 0.0
            if tag is not None:
                for name, val in self._read_pdh_array(self._cnt_util):
                    if tag in name and val > 0.0:
                        total_util += val

            return round(min(100.0, total_util), 1), round(ded_mb, 1)
        except Exception:
            return 0.0, 0.0

    def close(self):
        if self._pdh and self._pdh_query:
            try:
                self._pdh.PdhCloseQuery(self._pdh_query)
            except Exception:
                pass
            self._pdh_query = None


class LenovoWmiHardware:
    """Manages WMI interaction with root\\wmi:LENOVO_OTHER_METHOD."""

    def __init__(self):
        self._lom = None
        self._com_initialized = False
        self.access_denied = False
        self.last_error: str | None = None
        self._last_err_log_ts = 0.0
        self._last_connect_attempt_ts = 0.0

    @property
    def is_connected(self) -> bool:
        return self._lom is not None

    def init_com(self):
        """Must be called in every thread that uses this object."""
        if not self._com_initialized:
            try:
                pythoncom.CoInitialize()
                self._com_initialized = True
            except Exception:
                pass

    def uninit_com(self):
        try:
            self._lom = None
            if self._com_initialized:
                pythoncom.CoUninitialize()
                self._com_initialized = False
        except Exception:
            pass

    def _log_throttled(self, level: int, msg: str, min_interval: float = 15.0):
        now = time.monotonic()
        if now - self._last_err_log_ts >= min_interval:
            self._last_err_log_ts = now
            logger.log(level, msg)

    def _ensure_connected(self) -> bool:
        if self._lom is not None:
            return True

        now = time.monotonic()
        if now - self._last_connect_attempt_ts < 0.8:
            return False
        self._last_connect_attempt_ts = now

        try:
            self.init_com()
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
            loms = list(wmi.InstancesOf("LENOVO_OTHER_METHOD"))
            if not loms:
                self.last_error = "LENOVO_OTHER_METHOD instance not found"
                self._log_throttled(logging.ERROR, self.last_error)
                return False
            self._lom = loms[0]
            self.access_denied = False
            self.last_error = None
            return True
        except Exception as e:
            err_str = str(e)
            self.access_denied = (
                (str(WBEM_E_ACCESS_DENIED) in err_str)
                or ("Отказано в доступе" in err_str)
                or ("Access is denied" in err_str)
            )
            self.last_error = "Требуются права Администратора (WMI Access Denied)" if self.access_denied else f"Ошибка WMI: {e}"
            self._log_throttled(logging.ERROR, f"Failed to connect to LENOVO_OTHER_METHOD: {e}")
            self._lom = None
            return False

    def get_feature(self, feature_id: int) -> int | None:
        if not self._ensure_connected():
            return None
        try:
            in_param = self._lom.Methods_("GetFeatureValue").InParameters.SpawnInstance_()
            in_param.IDs = int(feature_id)
            res = self._lom.ExecMethod_("GetFeatureValue", in_param)
            if res is not None and hasattr(res, "value"):
                return int(res.value)
            return None
        except Exception as e:
            self._log_throttled(logging.WARNING, f"get_feature(0x{feature_id:08X}) failed: {e}")
            self._lom = None
            return None

    def set_feature(self, feature_id: int, value: int) -> bool:
        if not self._ensure_connected():
            return False
        try:
            in_param = self._lom.Methods_("SetFeatureValue").InParameters.SpawnInstance_()
            in_param.IDs = int(feature_id)
            in_param.value = int(value)
            self._lom.ExecMethod_("SetFeatureValue", in_param)
            return True
        except Exception as e:
            self._log_throttled(logging.WARNING, f"set_feature(0x{feature_id:08X}, {value}) failed: {e}")
            self._lom = None
            return False

    def get_cpu_temp(self) -> int | None:
        val = self.get_feature(ID_CPU_TEMP)
        if val is not None and 10 <= val <= 115:
            return val
        return None

    def get_fan1_rpm(self) -> int | None:
        val = self.get_feature(ID_FAN1_RPM)
        if val is not None and 0 <= val <= 9500:
            return val
        return None

    def get_fan2_rpm(self) -> int | None:
        val = self.get_feature(ID_FAN2_RPM)
        if val is not None and 0 <= val <= 9500:
            return val
        return None

    def get_boost_state(self) -> int | None:
        return self.get_feature(ID_FAN_BOOST)

    @staticmethod
    def _clamp_rpm(rpm: int) -> int:
        val = int(rpm)
        if val <= 0:
            return 0
        if val < 2000:
            return 2000
        if val > 7700:
            return 7700
        return val

    def set_fan1_rpm(self, rpm: int) -> bool:
        return self.set_feature(ID_FAN1_RPM, self._clamp_rpm(rpm))

    def set_fan2_rpm(self, rpm: int) -> bool:
        return self.set_feature(ID_FAN2_RPM, self._clamp_rpm(rpm))

    def set_both_fans(self, rpm: int) -> bool:
        ok1 = self.set_fan1_rpm(rpm)
        ok2 = self.set_fan2_rpm(rpm)
        return ok1 and ok2

    def set_boost(self, enable: bool, target_rpm: int = 7500) -> bool:
        if enable:
            ok_b = self.set_feature(ID_FAN_BOOST, 1)
            ok_f = self.set_both_fans(target_rpm)
            return ok_b and ok_f
        return self.reset_to_auto()

    def clear_boost(self) -> bool:
        return self.set_feature(ID_FAN_BOOST, 0)

    def reset_to_auto(self) -> bool:
        try:
            b_ok = self.set_feature(ID_FAN_BOOST, 0)
            f1_ok = self.set_fan1_rpm(0)
            f2_ok = self.set_fan2_rpm(0)
            return b_ok and f1_ok and f2_ok
        except Exception as e:
            logger.error(f"Error resetting fans to auto: {e}")
            return False


class NvmlHardware:
    """
    Manages NVIDIA GPU metrics via nvml.dll with full Optimus RTD3 (D3cold) sleep protection:
    - Never calls NVML while RTX 4060 is asleep in D3cold (0W)
    - Automatically releases NVML session after 4s of idle so NVIDIA driver can power off dGPU!
    """

    def __init__(self):
        self._nvml = None
        self._device = None
        self._initialized = False
        self._last_init_attempt_ts = 0.0
        self._rtd3 = OptimusRtd3Detector()
        self._idle_ticks = 0
        self._idle_released = False
        self._prev_power_state = None

    def _bind_prototypes(self, lib):
        lib.nvmlInit_v2.argtypes = []
        lib.nvmlInit_v2.restype = ctypes.c_int

        lib.nvmlShutdown.argtypes = []
        lib.nvmlShutdown.restype = ctypes.c_int

        lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        lib.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int

        lib.nvmlDeviceGetTemperature.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetTemperature.restype = ctypes.c_int

        lib.nvmlDeviceGetPowerUsage.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerUsage.restype = ctypes.c_int

        lib.nvmlDeviceGetUtilizationRates.argtypes = [ctypes.c_void_p, ctypes.POINTER(_NvmlUtilization)]
        lib.nvmlDeviceGetUtilizationRates.restype = ctypes.c_int

    def init(self) -> bool:
        if self._initialized:
            return True

        now = time.monotonic()
        if now - self._last_init_attempt_ts < 1.5:
            return False
        self._last_init_attempt_ts = now

        candidates = [
            "nvml.dll",
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NVIDIA Corporation", "NVSMI", "nvml.dll"),
        ]
        last_exc = None
        for dll_path in candidates:
            try:
                lib = ctypes.CDLL(dll_path)
                self._bind_prototypes(lib)
                if lib.nvmlInit_v2() != 0:
                    continue
                dev = ctypes.c_void_p()
                if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev)) != 0:
                    lib.nvmlShutdown()
                    continue
                self._nvml = lib
                self._device = dev
                self._initialized = True
                return True
            except Exception as e:
                last_exc = e

        if last_exc is not None:
            logger.warning(f"NVML init failed: {last_exc}")
        self._initialized = False
        return False

    def _handle_nvml_error(self, e=None):
        logger.warning(f"NVML communication error: {e}")
        self._close_nvml_only()

    def _close_nvml_only(self):
        if self._initialized and self._nvml:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
        self._initialized = False
        self._device = None

    def get_gpu_temp(self) -> int | None:
        if not self._initialized and not self.init():
            return None
        try:
            temp = ctypes.c_uint()
            ret = self._nvml.nvmlDeviceGetTemperature(self._device, 0, ctypes.byref(temp))
            if ret == 0:
                val = int(temp.value)
                return val if 5 <= val <= 115 else None
            self._handle_nvml_error(f"ret={ret}")
        except Exception as e:
            self._handle_nvml_error(e)
        return None

    def get_gpu_power(self) -> float | None:
        if not self._initialized and not self.init():
            return None
        try:
            power = ctypes.c_uint()
            ret = self._nvml.nvmlDeviceGetPowerUsage(self._device, ctypes.byref(power))
            if ret == 0:
                return round(power.value / 1000.0, 1)
            if ret not in (2, 3):
                self._handle_nvml_error(f"ret={ret}")
        except Exception as e:
            self._handle_nvml_error(e)
        return None

    def get_gpu_utilization(self) -> tuple[int, int] | None:
        if not self._initialized and not self.init():
            return None
        try:
            u = _NvmlUtilization()
            ret = self._nvml.nvmlDeviceGetUtilizationRates(self._device, ctypes.byref(u))
            if ret == 0:
                return int(u.gpu), int(u.memory)
            if ret not in (2, 3):
                self._handle_nvml_error(f"ret={ret}")
        except Exception as e:
            self._handle_nvml_error(e)
        return None

    def poll_telemetry_rtd3_safe(self) -> dict:
        """
        Optimus D3cold-aware telemetry poll:
        - Returns dgpu_state='D3cold' (0.0 W, temp=None) without touching NVML when card is powered off.
        - Releases NVML ('D0_idle') after 4 idle ticks so NVIDIA RTD3 timer can power off the GPU.
        - Immediately resumes NVML ('D0_active') when WDDM engine load or VRAM usage appears.
        """
        p_state = self._rtd3.get_power_state()
        wddm_util, wddm_vram_mb = self._rtd3.get_wddm_dgpu_activity()

        # 1. Hardware is powered OFF in D3cold (Optimus iGPU-only mode!)
        if p_state == POWER_DEVICE_D3:
            if self._initialized:
                self._close_nvml_only()
            if self._prev_power_state != POWER_DEVICE_D3:
                logger.info("NVIDIA dGPU entered RTD3 sleep (D3cold, 0W — running on Intel Arc iGPU).")
            self._prev_power_state = POWER_DEVICE_D3
            self._idle_released = True
            self._idle_ticks = 0
            return {
                "dgpu_state": "D3cold",
                "gpu_sleeping": True,
                "gpu_temp": None,
                "gpu_pwr": 0.0,
                "gpu_util": (0, 0),
            }

        # Did GPU have real WDDM workload?
        has_wddm_workload = (wddm_util >= 1.5) or (wddm_vram_mb >= 48.0)

        # If GPU just woke from D3 -> D0, briefly inspect or wake on workload
        woke_from_d3 = (self._prev_power_state == POWER_DEVICE_D3 and p_state == POWER_DEVICE_D0)
        self._prev_power_state = p_state

        if self._idle_released:
            if has_wddm_workload or woke_from_d3:
                self._idle_released = False
                self._idle_ticks = 0
                logger.info(
                    f"NVIDIA dGPU active (WDDM util={wddm_util}%, VRAM={wddm_vram_mb}MB) — resuming NVML telemetry."
                )
            else:
                # Stay silent: do NOT call NVML so nvlddmkm RTD3 timer can transition D0 -> D3cold!
                return {
                    "dgpu_state": "D0_idle",
                    "gpu_sleeping": True,
                    "gpu_temp": None,
                    "gpu_pwr": None,
                    "gpu_util": (0, 0),
                }

        # 2. Active NVML read
        gpu_temp = self.get_gpu_temp()
        gpu_pwr = self.get_gpu_power()
        gpu_util = self.get_gpu_utilization()

        nvml_util_val = gpu_util[0] if gpu_util else 0
        is_idle_now = (
            not has_wddm_workload
            and nvml_util_val <= 1
            and (gpu_pwr is None or gpu_pwr <= 8.0)
            and (gpu_temp is None or gpu_temp <= 68)
        )

        if is_idle_now:
            self._idle_ticks += 1
            if self._idle_ticks >= 4:
                logger.info("NVIDIA dGPU idle for 4s — releasing NVML handle to allow Optimus D3cold sleep.")
                self._close_nvml_only()
                self._idle_released = True
        else:
            self._idle_ticks = 0

        return {
            "dgpu_state": "D0_active",
            "gpu_sleeping": False,
            "gpu_temp": gpu_temp,
            "gpu_pwr": gpu_pwr,
            "gpu_util": gpu_util,
        }

    def shutdown(self):
        self._close_nvml_only()
        self._rtd3.close()


class CpuCoreTelemetryProvider:
    """
    Persistent zero-GPU-wake CPU per-core sensor bridge using CpuCoreSensorHelper.exe
    (LibreHardwareMonitorLib 0.9.4 with IsCpuEnabled=true, IsGpuEnabled=false)
    plus PDH frequency fallback and EC fallback when running non-elevated.
    """

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._failed_permanently = False
        self._pdh = None
        self._pdh_q = None
        self._pdh_freq_cnt = None
        self._init_pdh_freq()

    def _init_pdh_freq(self):
        try:
            self._pdh = ctypes.WinDLL("pdh.dll")
            hq = wintypes.HANDLE()
            if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(hq)) == 0:
                self._pdh_q = hq
                cf = wintypes.HANDLE()
                if self._pdh.PdhAddEnglishCounterW(
                    hq, r"\Processor Information(0,*)\Processor Frequency", None, ctypes.byref(cf)
                ) == 0:
                    self._pdh_freq_cnt = cf
                self._pdh.PdhCollectQueryData(hq)
        except Exception:
            pass

    def _read_pdh_base_freqs(self) -> dict[int, int]:
        out: dict[int, int] = {}
        if not self._pdh or not self._pdh_q or not self._pdh_freq_cnt:
            return out
        try:
            self._pdh.PdhCollectQueryData(self._pdh_q)
            bs = wintypes.DWORD(0)
            ic = wintypes.DWORD(0)
            self._pdh.PdhGetFormattedCounterArrayW(self._pdh_freq_cnt, 0x00000200, ctypes.byref(bs), ctypes.byref(ic), None)
            if bs.value == 0 or ic.value == 0:
                return out
            buf = (ctypes.c_byte * bs.value)()
            if self._pdh.PdhGetFormattedCounterArrayW(self._pdh_freq_cnt, 0x00000200, ctypes.byref(bs), ctypes.byref(ic), buf) != 0:
                return out
            arr = ctypes.cast(buf, ctypes.POINTER(_PDH_FMT_COUNTERVALUE_ITEM_W))
            for i in range(ic.value):
                nm = (arr[i].szName or "").strip()
                if nm.startswith("0,") and "_total" not in nm.lower():
                    try:
                        lp_idx = int(nm.split(",")[1])
                        out[lp_idx] = int(round(arr[i].doubleValue))
                    except Exception:
                        pass
        except Exception:
            pass
        return out

    @staticmethod
    def _find_helper_exe() -> str | None:
        base_dirs = []
        if getattr(sys, "frozen", False):
            base_dirs.append(os.path.dirname(sys.executable))
            if hasattr(sys, "_MEIPASS"):
                base_dirs.append(sys._MEIPASS)
        base_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        for b in base_dirs:
            cand = os.path.join(b, "sensor_helper", "CpuCoreSensorHelper.exe")
            if os.path.exists(cand):
                return cand
        return None

    def _ensure_started(self) -> bool:
        if self._failed_permanently:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        exe = self._find_helper_exe()
        if not exe:
            self._failed_permanently = True
            logger.warning("CpuCoreSensorHelper.exe not found; using EC summary fallback.")
            return False
        try:
            no_win = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._proc = subprocess.Popen(
                [exe],
                cwd=os.path.dirname(exe),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=no_win,
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to start CpuCoreSensorHelper: {e}")
            self._failed_permanently = True
            return False

    def poll(self, ec_cpu_temp: int | None = None) -> dict:
        """Polls per-core temperatures, loads, clocks, package power, and computes exact Core Average."""
        raw_json = None
        if self._ensure_started() and self._proc and self._proc.stdin and self._proc.stdout:
            try:
                self._proc.stdin.write("POLL\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
                if line:
                    raw_json = json.loads(line)
            except Exception as e:
                logger.debug(f"CpuCoreSensorHelper poll failed: {e}")
                self.shutdown_helper_only()

        if not raw_json or not raw_json.get("ok"):
            base_t = float(ec_cpu_temp) if ec_cpu_temp is not None else None
            return {
                "cpu_name": "Intel Core Ultra",
                "msr_active": False,
                "avg_temp": base_t,
                "max_temp": base_t,
                "pkg_temp": base_t,
                "power_w": None,
                "total_load": None,
                "cores": [],
            }

        cpu_name = raw_json.get("cpu") or "Intel Core Ultra"
        sensors = raw_json.get("sensors", [])

        core_temps: dict[int, float] = {}
        core_clocks: dict[int, float] = {}
        core_thread_loads: dict[int, list[float]] = {}
        total_load: float | None = None
        pkg_temp: float | None = None
        lhm_avg_temp: float | None = None
        lhm_max_temp: float | None = None
        pkg_power_w: float | None = None

        core_re = re.compile(r"CPU Core #(\d+)(?:\s+Thread #(\d+))?", re.IGNORECASE)

        for s in sensors:
            stype = s.get("t")
            sname = s.get("n") or ""
            sval = s.get("v")

            if stype == "Temperature":
                if sname == "CPU Package" and sval is not None:
                    pkg_temp = float(sval)
                elif sname == "Core Average" and sval is not None:
                    lhm_avg_temp = float(sval)
                elif sname == "Core Max" and sval is not None:
                    lhm_max_temp = float(sval)
                else:
                    m = core_re.match(sname)
                    if m and sval is not None:
                        c_num = int(m.group(1))
                        core_temps[c_num] = float(sval)

            elif stype == "Clock":
                m = core_re.match(sname)
                if m and sval is not None:
                    c_num = int(m.group(1))
                    core_clocks[c_num] = float(sval)

            elif stype == "Load":
                if sname == "CPU Total" and sval is not None:
                    total_load = max(0.0, min(100.0, float(sval)))
                else:
                    m = core_re.match(sname)
                    if m:
                        c_num = int(m.group(1))
                        core_thread_loads.setdefault(c_num, [])
                        if sval is not None:
                            core_thread_loads[c_num].append(max(0.0, min(100.0, float(sval))))

            elif stype == "Power":
                if ("Package" in sname or sname == "CPU Cores") and sval is not None:
                    if pkg_power_w is None or "Package" in sname:
                        pkg_power_w = round(float(sval), 1)

        all_core_nums = sorted(set(core_thread_loads.keys()) | set(core_temps.keys()) | set(core_clocks.keys()))
        pdh_freqs = self._read_pdh_base_freqs() if not core_clocks else {}

        # Classify cores: SMT (2 threads) -> P-Core; single-thread -> E-Core (first 8) / LP-E (beyond 8 on 16c Ultra)
        p_nums = [c for c in all_core_nums if len(core_thread_loads.get(c, [])) >= 2]
        single_nums = [c for c in all_core_nums if c not in p_nums]
        if len(p_nums) == 6 and len(single_nums) == 10:
            e_nums = single_nums[:8]
            lp_nums = single_nums[8:]
        else:
            e_nums = single_nums
            lp_nums = []

        ordered_specs: list[tuple[int, str, str]] = []
        for idx, c in enumerate(p_nums, 1):
            ordered_specs.append((c, f"P{idx}", "P"))
        for idx, c in enumerate(e_nums, 1):
            ordered_specs.append((c, f"E{idx}", "E"))
        for idx, c in enumerate(lp_nums, 1):
            ordered_specs.append((c, f"LP{idx}", "LP"))

        msr_active = len(core_temps) > 0
        valid_temps = list(core_temps.values())

        if valid_temps:
            avg_temp = round(sum(valid_temps) / len(valid_temps), 1)
            max_temp = round(max(valid_temps), 1)
        else:
            avg_temp = lhm_avg_temp if lhm_avg_temp is not None else (float(ec_cpu_temp) if ec_cpu_temp is not None else None)
            max_temp = lhm_max_temp if lhm_max_temp is not None else avg_temp

        if pkg_temp is None:
            pkg_temp = float(ec_cpu_temp) if ec_cpu_temp is not None else max_temp

        cores_list = []
        for c_num, label, kind in ordered_specs:
            t_loads = core_thread_loads.get(c_num, [])
            c_load = round(sum(t_loads) / len(t_loads), 1) if t_loads else 0.0
            c_temp = core_temps.get(c_num)
            if c_temp is None and ec_cpu_temp is not None:
                # Non-admin fallback approximation around EC sensor
                c_temp = round(float(ec_cpu_temp) + (c_load * 0.05), 1)
            c_mhz = int(round(core_clocks[c_num])) if c_num in core_clocks else (
                1400 if kind == "P" else (700 if kind == "LP" else 900)
            )
            if not core_clocks and pdh_freqs:
                c_mhz = pdh_freqs.get(0 if kind == "P" else (20 if kind == "LP" else 2), c_mhz)

            cores_list.append({
                "core_id": c_num,
                "label": label,
                "kind": kind,
                "temp": round(c_temp, 1) if c_temp is not None else None,
                "load": c_load,
                "mhz": c_mhz,
            })

        if total_load is None and cores_list:
            total_load = round(sum(x["load"] for x in cores_list) / len(cores_list), 1)

        return {
            "cpu_name": cpu_name,
            "msr_active": msr_active,
            "avg_temp": avg_temp,
            "max_temp": max_temp,
            "pkg_temp": round(pkg_temp, 1) if pkg_temp is not None else None,
            "power_w": pkg_power_w,
            "total_load": round(total_load, 1) if total_load is not None else 0.0,
            "cores": cores_list,
        }

    def shutdown_helper_only(self):
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.write("EXIT\n")
                    self._proc.stdin.flush()
                    self._proc.stdin.close()
                self._proc.wait(timeout=1.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def shutdown(self):
        self.shutdown_helper_only()
        if self._pdh and self._pdh_q:
            try:
                self._pdh.PdhCloseQuery(self._pdh_q)
            except Exception:
                pass
            self._pdh_q = None

