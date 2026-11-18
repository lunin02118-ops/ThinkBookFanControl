# ThinkBook Fan Control v2.0.0 — Native Rust Edition (Lenovo 14 G6+ IMH)

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011%20x64-0078D6?style=flat&logo=windows)](https://github.com/lunin02118-ops/ThinkBookFanControl)
[![Rust](https://img.shields.io/badge/Rust-Native%20Win32%20%2F%20GDI%2B-DEA584.svg?logo=rust)](https://www.rust-lang.org/)
[![Footprint](https://img.shields.io/badge/Binary-0.70%20MB%20%7C%20RAM%20~8%20MB-10B981.svg)](https://github.com/lunin02118-ops/ThinkBookFanControl/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/lunin02118-ops/ThinkBookFanControl?color=orange&logo=github)](https://github.com/lunin02118-ops/ThinkBookFanControl/releases)

---

> [!IMPORTANT]
> **Languages / Языки**: [Русский](#русский) | [English](#english)

---

<a name="русский"></a>
## 🇷🇺 Русский

**ThinkBook Fan Control v2.0.0** — ультра-лёгкий нативный системный комплекс аппаратного управления вентиляторами и глубокой телеметрии ядер CPU/GPU для ноутбука **Lenovo ThinkBook 14 G6+ IMH** (Type `21LD`, процессор **Intel Core Ultra 7 155H** `6P + 8E + 2LP-E` + дискретная видеокарта **NVIDIA GeForce RTX 4060 Laptop GPU** + встроенная **Intel Arc iGPU**).

В версии **2.0.0** приложение полностью переписано с Python/PySide6 на чистый **Rust + нативный Win32 API (GDI/GDI+ Per-Monitor-V2 HiDPI)** без внешних фреймворков, без WebView2/Tauri и без аппаратного 3D-ускорения окна:
- **Размер исполняемого файла**: **`0.70 МБ` (`736 КБ`)** вместо `185 МБ`.
- **Потребление ОЗУ**: **`~7–12 МБ`** вместо `157 МБ` (при сворачивании в трей буфер окна освобождается через `K32EmptyWorkingSet`).
- **Нагрузка на CPU в фоне**: **`< 0.01%`** (в свёрнутом режиме не будит спящие P-ядра межъядерными прерываниями).
- **100% сохранение глубокого сна видеокарты `D3cold (0.0 Вт)`**: динамическая выгрузка `nvml.dll` (`FreeLibrary`) в простое + контроль состояния шины PCIe через `CfgMgr32`.

---

### ✨ Ключевые возможности v2.0.0

1. **Прямое аппаратное управление кулерами через нативный COM/WMI (`root\wmi:LENOVO_OTHER_METHOD`)**:
   - Вентилятор 1 (CPU): `0x04030001` (`2000–7700 RPM`, `0` = BIOS Auto).
   - Вентилятор 2 (GPU): `0x04030002` (`2000–7700 RPM`, `0` = BIOS Auto).
   - Режим Full Boost: `0x04020000` + автоматическое удержание от сброса сторожевым таймером EC (anti-watchdog refresh каждые `3.5 с`).
   - В режиме **«Штатный BIOS (Auto)»** команда сброса отправляется **однократно** без паразитного фонового WMI-спама.
2. **Адаптивный плавный регулятор `NativeSmoothRegulator` (Hermite Feedforward + Anti-Windup I-PD)**:
   - Учитывает общую теплотрубку CPU и GPU, подавляя кратковременные 1–3 секундные турбо-всплески процессора без «взвывания» кулеров.
   - Многомасштабная EMA-фильтрация (`tau_gpu=3.2с`, `tau_cpu_fast=2.8с`, `tau_cpu_soak=8.5с`, `tau_pwr=6.0с`), гладкая зона нечувствительности $C^1$ (`±0.45 °C`), гистерезис вывода `20 RPM` и безударное переключение режимов (`0 RPM` скачка при старте и смене уставки).
3. **Две раздельные подписанные HiDPI-иконки в системном трее (`[CPU]` и `[GPU]`)**:
   - Значок **`[CPU]`** всегда отображает среднюю температуру процессора.
   - Значок **`[GPU]`** отображает температуру RTX 4060 под нагрузкой, **`IDLE`** при ожидании сна или тёмно-зелёный **`OFF`**, когда видеокарта отключена в **`D3cold (0.0 Вт)`**.
4. **Матрица всех 16 физических ядер CPU (`6P + 8E + 2LP`) + Средняя по процессору**:
   - Прямое чтение цифровых термодатчиков каждого ядра (DTS `MSR 0x19C` / `0x1A2`) и энергопотребления пакета RAPL (`MSR 0x606` / `0x611`, Ватты) на уровне Ring-0 через подписанный драйвер `WinRing0x64.sys`.
   - Загрузка каждого ядра (`%`) за один системный вызов `NtQuerySystemInformation` (`0.005 мс`) и реальная эффективная частота Turbo (`ГГц`).
   - Наглядная плашка **«Средняя по CPU: XX.X °C»** и карточка состояния **GPU RTX 4060 / Optimus**.
5. **Нулевое пробуждение RTX 4060 (Zero-Wake NVIDIA Optimus RTD3 `D3cold`)**:
   - Мониторинг состояния питания PCIe (`DEVPKEY_Device_PowerData`, `4 = D3cold`) через `CfgMgr32` и WDDM-активности по `LUID` видеоадаптера без обращения к драйверу NVIDIA.
   - Библиотека `nvml.dll` загружается динамически (`LoadLibraryW`) только при реальной 3D/Compute нагрузке и полностью выгружается из памяти (`nvmlShutdown` + `FreeLibrary`) через 4 секунды простоя, позволяя RTX 4060 уходить в глубокий сон `0.0 Вт`.
6. **Интерактивная кривая вентиляторов и ручной режим RPM**:
   - Перетаскивание точек кривой мышью (Drag-and-Drop), двойной клик для добавления точки, правый клик для удаления, 4 готовых пресета и гистерезис охлаждения `1.2 °C`.
7. **Двухступенчатая прогрессивная термозащита и гарантированный возврат в BIOS Auto**:
   - Ступень 1 (`>= 80 °C GPU` или `>= 87 °C CPU` дольше 3 с) ➔ плавный подъём минимум до `5400 RPM`.
   - Ступень 2 (`>= 84 °C GPU` или `>= 92 °C CPU` дольше 2 с) ➔ `100% Турбо (7500 RPM)`.
   - Перехват закрытия, завершения сеанса Windows (`WM_ENDSESSION`), сигналов консоли и `panic_hook` гарантированно возвращает кулеры под управление BIOS.
8. **Мгновенное открытие без повторного запроса UAC и CLI-управление**:
   - Повторный запуск ярлыка или `.exe` мгновенно разворачивает окно уже работающего фонового процесса через защищённый Named Pipe IPC (`\\.\pipe\ThinkBookFanControl_Rust_v2`).
   - Автозапуск через Планировщик заданий Windows (`RunLevel = Highest`) работает и от сети, и от батареи без ограничения в 72 часа.

---

### 🚀 Установка и запуск

#### Вариант 1: Готовый нативный релиз (Рекомендуется)
1. Скачайте архив `ThinkBookFanControl-v2.0.0-win64.zip` из раздела [**Releases**](https://github.com/lunin02118-ops/ThinkBookFanControl/releases).
2. Распакуйте в любую папку (например, `C:\tools\ThinkBookFanControl`).
3. Запустите `ThinkBookFanControl.exe` (при первом старте подтвердите запрос UAC).

#### Команды CLI / IPC (работают без запроса UAC при запущенном демоне)
```powershell
ThinkBookFanControl.exe                  # Развернуть главное окно
ThinkBookFanControl.exe --minimized      # Запустить свёрнутым в системный трей
ThinkBookFanControl.exe --status         # Вывести JSON-телеметрию (CPU, GPU D3cold, RPM, RAM)
ThinkBookFanControl.exe --mode target_65 # Переключить режим (target_65 | curve | manual | auto | boost)
ThinkBookFanControl.exe --quit           # Безопасно остановить и вернуть кулеры в BIOS Auto
ThinkBookFanControl.exe --self-test      # Автотест всех подсистем без повышения прав
```

#### Вариант 2: Сборка из исходного кода (Rust)
```powershell
cd rust_app
cargo test
cargo build --release
python stamp_resources.py target\release\ThinkBookFanControl.exe target\app_resources.res
```

---

### 🛠️ Структура репозитория

```text
ThinkBookFanControl/
├── rust_app/                      # Нативное приложение v2.0.0 на Rust + Win32
│   ├── Cargo.toml                 # Конфигурация крейта и LTO-профиль релиза
│   ├── build.rs                   # Компиляция ресурсов Windows SDK (rc.exe)
│   ├── app.rc / app.manifest      # Иконка, версия и PerMonitorV2 HiDPI манифест
│   ├── stamp_resources.py         # Вшивание ресурсов в PE-образ
│   ├── WinRing0x64.sys            # Подписанный драйвер Ring-0 для чтения MSR ядер CPU
│   └── src/
│       ├── main.rs                # Точка входа, Named Pipe IPC, поток управления, логгер
│       ├── wmi_ec.rs              # Нативный COM/WMI клиент LENOVO_OTHER_METHOD
│       ├── regulator.rs           # Адаптивный регулятор NativeSmoothRegulator + тесты
│       ├── gpu_rtd3.rs            # Детектор Optimus D3cold (CfgMgr32/PDH) + динамический NVML
│       ├── cpu_msr.rs             # Топология 16 ядер (6P+8E+2LP), MSR DTS °C, RAPL Ватты
│       ├── config.rs              # Атомарное хранение config.json и автозапуск schtasks
│       └── ui_win32.rs            # Тёмный HiDPI интерфейс Win32/GDI+ и двойной трей [CPU]/[GPU]
├── app_icon.ico / app_icon.png    # Иконки приложения
├── config.json                    # Конфигурация по умолчанию
├── release_notes_v2.0.0.md        # Подробное описание релиза v2.0.0
└── LICENSE                        # Лицензия MIT
```

---

<a name="english"></a>
## 🇬🇧 English

**ThinkBook Fan Control v2.0.0** is an ultra-lightweight native hardware fan controller and deep CPU/GPU telemetry daemon engineered specifically for the **Lenovo ThinkBook 14 G6+ IMH** (Model `21LD`, **Intel Core Ultra 7 155H** `6P + 8E + 2LP-E` + **NVIDIA GeForce RTX 4060 Laptop GPU** + **Intel Arc iGPU**).

In **v2.0.0**, the entire suite has been rewritten from Python/PySide6 into **pure native Rust + Win32 GDI/GDI+ (`PerMonitorV2` HiDPI)** with zero external runtimes, zero WebView2/Tauri, and zero GPU 3D context creation:
- **Standalone Binary Size**: **`0.70 MB` (`736 KB`)** (down from `185 MB`).
- **Memory Footprint**: **`~7–12 MB` RAM** (down from `157 MB`; frees offscreen DIB backbuffer and trims working set via `K32EmptyWorkingSet` when minimized to tray).
- **Idle CPU Overhead**: **`< 0.01%`** (skips per-core MSR IPI wakeups when hidden in tray).
- **100% Zero-Wake NVIDIA Optimus `D3cold (0.0W)` Support**: checks PCIe power state via `CfgMgr32` (`DEVPKEY_Device_PowerData`) and dynamically unloads `nvml.dll` via `FreeLibrary` after 4 seconds of idle so the RTX 4060 sleeps in true `D3cold (0.0W)`.

### ✨ Key Highlights in v2.0.0
- **Dual Labeled HiDPI Tray Icons (`[CPU]` & `[GPU]`)**: Distinct `64×64` badges showing real-time CPU average temperature and GPU temperature (or dark-green `OFF` when RTX 4060 is asleep in `D3cold`).
- **16-Core Hybrid CPU Matrix (`P1..P6`, `E1..E8`, `LP1..LP2`)**: Real-time per-core DTS temperature (`MSR 0x19C`/`0x1A2` via signed `WinRing0x64.sys`), per-core utilization (`NtQuerySystemInformation`), effective Turbo frequency (`GHz`), **CPU Average Temperature** badge, and RAPL package power (`Watts`).
- **Smooth Thermal Regulator (`NativeSmoothRegulator`)**: Hermite `smoothstep` feedforward + wide-range anti-windup `I-PD` controller (`2100..6800 RPM`), multi-timescale EMA filters, `±0.45°C` soft deadband, `20 RPM` output hysteresis, bumpless mode transfers, interactive draggable fan curve, and 2-stage progressive thermal safety override.
- **Instant UAC-Free Activation & CLI IPC**: Re-launching the desktop shortcut or calling `--status` / `--mode` communicates with the elevated daemon over a local Named Pipe (`\\.\pipe\ThinkBookFanControl_Rust_v2`) in `<2 ms` without triggering a UAC prompt.

### 📄 License
Licensed under the [MIT License](LICENSE).
