"""
Hardware abstraction layer for Lenovo ThinkBook 14 G6+ IMH
Accesses:
- WMI root\\wmi -> LENOVO_OTHER_METHOD for Fan 1, Fan 2, CPU Temp, Boost
- nvml.dll for NVIDIA RTX 4060 Laptop GPU metrics
"""

import ctypes
import os
import sys
import logging
import pythoncom
import win32com.client

logger = logging.getLogger("ThinkBookHardware")

# WMI IDs
ID_FAN1_RPM = 0x04030001      # 67305473: Set/Get Fan 1 RPM (0 = Auto, 2400..5000)
ID_FAN2_RPM = 0x04030002      # 67305474: Set/Get Fan 2 RPM (0 = Auto, 2400..4500)
ID_FAN_BOOST = 0x04020000     # 67239936: Full Fan Boost 100% (1 = On, 0 = Off)
ID_CPU_TEMP = 0x05040000      # 84148224: CPU Temperature in °C


class LenovoWmiHardware:
    """Manages WMI interaction with LENOVO_OTHER_METHOD."""

    def __init__(self):
        self._lom = None
        self._initialized = False

    def init_com(self):
        """Must be called in every thread that uses this object."""
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

    def uninit_com(self):
        try:
            self._lom = None
            pythoncom.CoUninitialize()
        except Exception:
            pass

    def _ensure_connected(self):
        if self._lom is not None:
            return True
        try:
            self.init_com()
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
            loms = list(wmi.InstancesOf("LENOVO_OTHER_METHOD"))
            if not loms:
                logger.error("LENOVO_OTHER_METHOD instance not found!")
                return False
            self._lom = loms[0]
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"Failed to connect to LENOVO_OTHER_METHOD: {e}")
            self._lom = None
            return False

    def get_feature(self, feature_id: int) -> int | None:
        """Reads a 32-bit feature value from LENOVO_OTHER_METHOD."""
        if not self._ensure_connected():
            return None
        try:
            in_param = self._lom.Methods_("GetFeatureValue").InParameters.SpawnInstance_()
            in_param.IDs = int(feature_id)
            res = self._lom.ExecMethod_("GetFeatureValue", in_param)
            if res and hasattr(res, "value"):
                return int(res.value)
            return None
        except Exception as e:
            logger.warning(f"get_feature(0x{feature_id:08X}) failed: {e}")
            self._lom = None  # force reconnect on error
            return None

    def set_feature(self, feature_id: int, value: int) -> bool:
        """Sets a 32-bit feature value via LENOVO_OTHER_METHOD."""
        if not self._ensure_connected():
            return False
        try:
            in_param = self._lom.Methods_("SetFeatureValue").InParameters.SpawnInstance_()
            in_param.IDs = int(feature_id)
            in_param.value = int(value)
            self._lom.ExecMethod_("SetFeatureValue", in_param)
            return True
        except Exception as e:
            logger.warning(f"set_feature(0x{feature_id:08X}, {value}) failed: {e}")
            self._lom = None
            return False

    def get_cpu_temp(self) -> int | None:
        return self.get_feature(ID_CPU_TEMP)

    def get_fan1_rpm(self) -> int | None:
        return self.get_feature(ID_FAN1_RPM)

    def get_fan2_rpm(self) -> int | None:
        return self.get_feature(ID_FAN2_RPM)

    def get_boost_state(self) -> int | None:
        return self.get_feature(ID_FAN_BOOST)

    def set_fan1_rpm(self, rpm: int) -> bool:
        """Set Fan 1 RPM (0 = Auto, or 2000..7700)."""
        val = int(rpm)
        if val < 0:
            val = 0
        elif 0 < val < 2000:
            val = 2000
        elif val > 7700:
            val = 7700
        return self.set_feature(ID_FAN1_RPM, val)

    def set_fan2_rpm(self, rpm: int) -> bool:
        """Set Fan 2 RPM (0 = Auto, or 2000..7700)."""
        val = int(rpm)
        if val < 0:
            val = 0
        elif 0 < val < 2000:
            val = 2000
        elif val > 7700:
            val = 7700
        return self.set_feature(ID_FAN2_RPM, val)

    def set_both_fans(self, rpm: int) -> bool:
        """Sets both fans to specified RPM (or 0 for auto)."""
        ok1 = self.set_fan1_rpm(rpm)
        ok2 = self.set_fan2_rpm(rpm)
        return ok1 and ok2

    def set_boost(self, enable: bool, target_rpm: int = 7500) -> bool:
        """Toggles full fan boost.
        Dispatches maximum target RPM (7500 RPM) to both fans and sets boost flag,
        ensuring full physical speed (~7300–7700 RPM) across all EC firmware variants.
        """
        if enable:
            self.set_feature(ID_FAN_BOOST, 1)
            return self.set_both_fans(target_rpm)
        else:
            return self.reset_to_auto()

    def clear_boost(self) -> bool:
        """Clears boost feature flag (0x04020000) in EC."""
        return self.set_feature(ID_FAN_BOOST, 0)

    def reset_to_auto(self) -> bool:
        """Restores default EC / BIOS automatic fan control."""
        try:
            self.set_feature(ID_FAN_BOOST, 0)
            self.set_fan1_rpm(0)
            self.set_fan2_rpm(0)
            return True
        except Exception as e:
            logger.error(f"Error resetting fans to auto: {e}")
            return False


class NvmlHardware:
    """Manages NVIDIA GPU metrics via nvml.dll."""

    def __init__(self):
        self._nvml = None
        self._device = None
        self._initialized = False

    def init(self) -> bool:
        if self._initialized:
            return True
        try:
            # nvml.dll is in System32 or driver store
            self._nvml = ctypes.CDLL("nvml.dll")
            if self._nvml.nvmlInit_v2() != 0:
                logger.error("nvmlInit_v2 failed")
                return False
            dev = ctypes.c_void_p()
            if self._nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev)) != 0:
                logger.error("nvmlDeviceGetHandleByIndex_v2 failed")
                return False
            self._device = dev
            self._initialized = True
            return True
        except Exception as e:
            logger.warning(f"NVML init failed: {e}")
            self._initialized = False
            return False

    def _handle_nvml_error(self, e=None):
        logger.warning(f"NVML communication error: {e}")
        self._initialized = False
        self._device = None
        if self._nvml:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass

    def get_gpu_temp(self) -> int | None:
        if not self._initialized and not self.init():
            return None
        try:
            temp = ctypes.c_uint()
            # 0 = NVML_TEMPERATURE_GPU
            ret = self._nvml.nvmlDeviceGetTemperature(self._device, 0, ctypes.byref(temp))
            if ret == 0:
                return int(temp.value)
            self._handle_nvml_error(f"ret={ret}")
        except Exception as e:
            self._handle_nvml_error(e)
        return None

    def get_gpu_power(self) -> float | None:
        """Returns GPU power draw in Watts."""
        if not self._initialized and not self.init():
            return None
        try:
            power = ctypes.c_uint()
            ret = self._nvml.nvmlDeviceGetPowerUsage(self._device, ctypes.byref(power))
            if ret == 0:
                return round(power.value / 1000.0, 1)
            self._handle_nvml_error(f"ret={ret}")
        except Exception as e:
            self._handle_nvml_error(e)
        return None

    def get_gpu_utilization(self) -> tuple[int, int] | None:
        """Returns (gpu_util_percent, mem_util_percent)."""
        if not self._initialized and not self.init():
            return None
        try:
            class _NvmlUtilization(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            u = _NvmlUtilization()
            ret = self._nvml.nvmlDeviceGetUtilizationRates(self._device, ctypes.byref(u))
            if ret == 0:
                return int(u.gpu), int(u.memory)
            self._handle_nvml_error(f"ret={ret}")
        except Exception as e:
            self._handle_nvml_error(e)
        return None

    def shutdown(self):
        if self._initialized and self._nvml:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
            self._initialized = False
            self._device = None
