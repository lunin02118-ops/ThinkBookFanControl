use std::mem::ManuallyDrop;
use std::time::Instant;
use windows::core::{BSTR, PCWSTR, VARIANT};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoSetProxyBlanket, CLSCTX_INPROC_SERVER,
    COINIT_MULTITHREADED, EOAC_NONE, RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE,
};
use windows::Win32::System::Rpc::{RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE};
use windows::Win32::System::Variant::{VariantClear, VT_BSTR, VT_I4, VT_UI4};
use windows::Win32::System::Wmi::{
    IWbemClassObject, IWbemLocator, IWbemServices, WbemLocator, WBEM_FLAG_FORWARD_ONLY,
    WBEM_INFINITE,
};

pub const ID_FAN1_RPM: i32 = 0x0403_0001;
pub const ID_FAN2_RPM: i32 = 0x0403_0002;
pub const ID_FAN_BOOST: i32 = 0x0402_0000;
pub const ID_CPU_TEMP: i32 = 0x0504_0000;

pub struct LenovoWmiHardware {
    services: Option<IWbemServices>,
    class_obj: Option<IWbemClassObject>,
    instance_path: Option<BSTR>,
    pub last_error: Option<String>,
    last_connect_attempt: Option<Instant>,
}

impl LenovoWmiHardware {
    pub fn new() -> Self {
        Self {
            services: None,
            class_obj: None,
            instance_path: None,
            last_error: None,
            last_connect_attempt: None,
        }
    }

    pub fn is_connected(&self) -> bool {
        self.services.is_some() && self.instance_path.is_some()
    }

    fn disconnect(&mut self) {
        self.services = None;
        self.class_obj = None;
        self.instance_path = None;
    }

    fn make_i4_variant(val: i32) -> VARIANT {
        unsafe {
            let mut v = VARIANT::default();
            let ptr = &mut v as *mut VARIANT as *mut u8;
            // VARTYPE vt at offset 0 (u16), lVal at offset 8 (i32)
            *(ptr as *mut u16) = VT_I4.0;
            *(ptr.add(8) as *mut i32) = val;
            v
        }
    }

    fn read_int_variant(v: &VARIANT) -> Option<i32> {
        unsafe {
            let ptr = v as *const VARIANT as *const u8;
            let vt = *(ptr as *const u16);
            if vt == VT_I4.0 || vt == VT_UI4.0 {
                Some(*(ptr.add(8) as *const i32))
            } else {
                None
            }
        }
    }

    fn read_bstr_variant(v: &VARIANT) -> Option<BSTR> {
        unsafe {
            let ptr = v as *const VARIANT as *const u8;
            let vt = *(ptr as *const u16);
            if vt == VT_BSTR.0 {
                let bstr_raw = *(ptr.add(8) as *const *const u16);
                if !bstr_raw.is_null() {
                    let s = PCWSTR(bstr_raw).to_string().ok()?;
                    return Some(BSTR::from(s));
                }
            }
            None
        }
    }

    fn ensure_connected(&mut self) -> bool {
        if self.is_connected() {
            return true;
        }
        if let Some(t) = self.last_connect_attempt {
            if t.elapsed().as_millis() < 900 {
                return false;
            }
        }
        self.last_connect_attempt = Some(Instant::now());

        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);

            let locator: IWbemLocator = match CoCreateInstance(&WbemLocator, None, CLSCTX_INPROC_SERVER) {
                Ok(l) => l,
                Err(e) => {
                    self.last_error = Some(format!("WbemLocator error: {}", e));
                    return false;
                }
            };

            let ns = BSTR::from("ROOT\\WMI");
            let services = match locator.ConnectServer(&ns, &BSTR::new(), &BSTR::new(), &BSTR::new(), 0, &BSTR::new(), None) {
                Ok(s) => s,
                Err(e) => {
                    let code = e.code().0 as u32;
                    if code == 0x80041003 || code == 0x80070005 {
                        self.last_error = Some("Требуются права Администратора (WMI Access Denied)".to_string());
                    } else {
                        self.last_error = Some(format!("ConnectServer WMI error: 0x{:08X}", code));
                    }
                    return false;
                }
            };

            let _ = CoSetProxyBlanket(
                &services,
                RPC_C_AUTHN_WINNT,
                RPC_C_AUTHZ_NONE,
                PCWSTR::null(),
                RPC_C_AUTHN_LEVEL_CALL,
                RPC_C_IMP_LEVEL_IMPERSONATE,
                None,
                EOAC_NONE,
            );

            let cls_name = BSTR::from("LENOVO_OTHER_METHOD");
            let mut class_obj: Option<IWbemClassObject> = None;
            if let Err(e) = services.GetObject(&cls_name, Default::default(), None, Some(&mut class_obj), None) {
                self.last_error = Some(format!("LENOVO_OTHER_METHOD class error: {}", e));
                return false;
            }

            let enum_inst = match services.CreateInstanceEnum(&cls_name, WBEM_FLAG_FORWARD_ONLY, None) {
                Ok(en) => en,
                Err(e) => {
                    self.last_error = Some(format!("CreateInstanceEnum error: {}", e));
                    return false;
                }
            };

            let mut objs: [Option<IWbemClassObject>; 1] = [None];
            let mut returned = 0u32;
            let _ = enum_inst.Next(WBEM_INFINITE, &mut objs, &mut returned);
            let Some(inst) = objs[0].take() else {
                self.last_error = Some("Экземпляр LENOVO_OTHER_METHOD не найден".to_string());
                return false;
            };

            let mut path_var = VARIANT::default();
            if inst
                .Get(PCWSTR::from_raw(BSTR::from("__PATH").as_wide().as_ptr()), 0, &mut path_var, None, None)
                .is_err()
            {
                let _ = VariantClear(&mut path_var);
                self.last_error = Some("Не удалось прочитать __PATH у LENOVO_OTHER_METHOD".to_string());
                return false;
            }
            let inst_path = Self::read_bstr_variant(&path_var);
            let _ = VariantClear(&mut path_var);

            let Some(p) = inst_path else {
                self.last_error = Some("Пустой __PATH у LENOVO_OTHER_METHOD".to_string());
                return false;
            };

            self.services = Some(services);
            self.class_obj = class_obj;
            self.instance_path = Some(p);
            self.last_error = None;
            true
        }
    }

    pub fn get_feature(&mut self, feature_id: i32) -> Option<i32> {
        if !self.ensure_connected() {
            return None;
        }
        unsafe {
            let services = self.services.as_ref()?;
            let class_obj = self.class_obj.as_ref()?;
            let inst_path = self.instance_path.as_ref()?;

            let method_name = BSTR::from("GetFeatureValue");
            let mut in_sig: Option<IWbemClassObject> = None;
            if class_obj
                .GetMethod(PCWSTR(method_name.as_wide().as_ptr()), 0, &mut in_sig, std::ptr::null_mut())
                .is_err()
            {
                self.disconnect();
                return None;
            }
            let in_params = in_sig?.SpawnInstance(0).ok()?;

            let id_prop = BSTR::from("IDs");
            let id_var = ManuallyDrop::new(Self::make_i4_variant(feature_id));
            if in_params
                .Put(PCWSTR(id_prop.as_wide().as_ptr()), 0, &*id_var, 0)
                .is_err()
            {
                return None;
            }

            let mut out_params: Option<IWbemClassObject> = None;
            if services
                .ExecMethod(
                    inst_path,
                    &method_name,
                    Default::default(),
                    None,
                    &in_params,
                    Some(&mut out_params),
                    None,
                )
                .is_err()
            {
                self.disconnect();
                return None;
            }

            let out_obj = out_params?;
            let val_prop = BSTR::from("value");
            let mut out_var = VARIANT::default();
            if out_obj
                .Get(PCWSTR(val_prop.as_wide().as_ptr()), 0, &mut out_var, None, None)
                .is_ok()
            {
                let res = Self::read_int_variant(&out_var);
                let _ = VariantClear(&mut out_var);
                return res;
            }
            let _ = VariantClear(&mut out_var);
            None
        }
    }

    pub fn set_feature(&mut self, feature_id: i32, value: i32) -> bool {
        if !self.ensure_connected() {
            return false;
        }
        unsafe {
            let (Some(services), Some(class_obj), Some(inst_path)) = (
                self.services.as_ref(),
                self.class_obj.as_ref(),
                self.instance_path.as_ref(),
            ) else {
                return false;
            };

            let method_name = BSTR::from("SetFeatureValue");
            let mut in_sig: Option<IWbemClassObject> = None;
            if class_obj
                .GetMethod(PCWSTR(method_name.as_wide().as_ptr()), 0, &mut in_sig, std::ptr::null_mut())
                .is_err()
            {
                self.disconnect();
                return false;
            }
            let Some(sig) = in_sig else { return false };
            let Ok(in_params) = sig.SpawnInstance(0) else { return false };

            let id_prop = BSTR::from("IDs");
            let val_prop = BSTR::from("value");
            let id_var = ManuallyDrop::new(Self::make_i4_variant(feature_id));
            let val_var = ManuallyDrop::new(Self::make_i4_variant(value));

            if in_params.Put(PCWSTR(id_prop.as_wide().as_ptr()), 0, &*id_var, 0).is_err() {
                return false;
            }
            if in_params.Put(PCWSTR(val_prop.as_wide().as_ptr()), 0, &*val_var, 0).is_err() {
                return false;
            }

            if services
                .ExecMethod(inst_path, &method_name, Default::default(), None, &in_params, None, None)
                .is_err()
            {
                self.disconnect();
                return false;
            }
            true
        }
    }

    #[inline]
    pub fn clamp_rpm(rpm: i32) -> i32 {
        if rpm <= 0 {
            0
        } else {
            rpm.clamp(2000, 7700)
        }
    }

    pub fn get_cpu_temp(&mut self) -> Option<i32> {
        self.get_feature(ID_CPU_TEMP).filter(|&v| (10..=115).contains(&v))
    }

    pub fn get_fan1_rpm(&mut self) -> Option<i32> {
        self.get_feature(ID_FAN1_RPM).filter(|&v| (0..=9500).contains(&v))
    }

    pub fn get_fan2_rpm(&mut self) -> Option<i32> {
        self.get_feature(ID_FAN2_RPM).filter(|&v| (0..=9500).contains(&v))
    }

    #[allow(dead_code)]
    pub fn get_boost_state(&mut self) -> Option<i32> {
        self.get_feature(ID_FAN_BOOST)
    }

    pub fn set_both_fans(&mut self, rpm: i32) -> bool {
        let clamped = Self::clamp_rpm(rpm);
        let ok1 = self.set_feature(ID_FAN1_RPM, clamped);
        let ok2 = self.set_feature(ID_FAN2_RPM, clamped);
        ok1 && ok2
    }

    pub fn set_boost(&mut self, enable: bool, target_rpm: i32) -> bool {
        if enable {
            let b_ok = self.set_feature(ID_FAN_BOOST, 1);
            let f_ok = self.set_both_fans(target_rpm);
            b_ok && f_ok
        } else {
            self.reset_to_auto()
        }
    }

    pub fn clear_boost(&mut self) -> bool {
        self.set_feature(ID_FAN_BOOST, 0)
    }

    pub fn reset_to_auto(&mut self) -> bool {
        let b = self.set_feature(ID_FAN_BOOST, 0);
        let f1 = self.set_feature(ID_FAN1_RPM, 0);
        let f2 = self.set_feature(ID_FAN2_RPM, 0);
        b && f1 && f2
    }
}
