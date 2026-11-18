# Release Notes — ThinkBook Fan Control v2.0.0 (Native Rust Edition)

## 🇷🇺 Что нового в версии 2.0.0

### 1. Полный переход на нативный Rust + Win32 API (`0.70 МБ`, `~8 МБ ОЗУ`)
- Программа полностью переписана с Python / PySide6 / C# на чистый **Rust** с прямым использованием Win32 API, COM/WMI (`IWbemLocator` / `IWbemServices`), GDI/GDI+ (`PerMonitorV2` HiDPI), `CfgMgr32`, `PDH` и `NtQuerySystemInformation`.
- Весь дистрибутив сжат с **`185 МБ`** до одного нативного файла **`ThinkBookFanControl.exe` (`736 256` байт / `0.70 МБ`)**.
- Потребление оперативной памяти снижено со **`157 МБ`** до **`~7–12 МБ`** (при сворачивании в системный трей внеэкранный буфер DIB освобождается и вызывается `K32EmptyWorkingSet`).
- В свёрнутом состоянии программа прекращает посылать межъядерные прерывания (`SetThreadAffinityMask`) на спящие ядра процессора, снижая фоновую нагрузку на CPU до **`< 0.01%`**.

### 2. Две раздельные иконки в системном трее: `[CPU]` и `[GPU]`
- Вместо одной неподписанной иконки программа создаёт в трее два чётких HiDPI-значка `64×64`:
  - **`[CPU]`** — средняя температура процессора Intel Core Ultra 7 155H.
  - **`[GPU]`** — температура дискретной видеокарты RTX 4060, статус **`IDLE`** (ожидание сна) либо тёмно-зелёный значок **`OFF`**, когда RTX 4060 полностью обесточена в режиме энергосбережения **`D3cold (0.0 Вт)`**.

### 3. Матрица всех 16 физических ядер процессора (`6P + 8E + 2LP`) и «Средняя по CPU»
- В развёрнутом окне добавлена детальная панель процессора **Intel Core Ultra 7 155H** (`P1..P6`, `E1..E8`, `LP1..LP2`) и карточка **GPU RTX 4060 / Optimus**:
  - Прямое чтение температуры каждого ядра (DTS `MSR 0x19C` / `0x1A2`) и мощности процессора RAPL (`MSR 0x606` / `0x611`, Вт) через подписанный драйвер уровня ядра `WinRing0x64.sys`.
  - Отображение загрузки каждого ядра (`%`), реальной частоты Turbo (`ГГц`) и сводной плашки **«Средняя по CPU: XX.X °C»**.

### 4. Безупречная поддержка глубокого сна NVIDIA Optimus `D3cold (0.0 Вт)`
- Детектор `OptimusRtd3Detector` проверяет состояние питания PCIe (`DEVPKEY_Device_PowerData`, `4 = D3cold`) через `CfgMgr32` и реальную загрузку видеоядра/видеопамяти через WDDM-счётчики по `LUID` адаптера.
- Библиотека `nvml.dll` загружается динамически только при реальной нагрузке (`WDDM >= 1.0%` или `VRAM >= 32 МБ`) и полностью выгружается из адресного пространства процесса (`nvmlShutdown` + `FreeLibrary`) через 4 секунды простоя.
- Отрисовка окна выполняется исключительно силами CPU (GDI/GDI+) без создания Direct3D/OpenGL контекстов, поэтому открытие окна никогда не будит спящую RTX 4060.

### 5. Адаптивный регулятор `NativeSmoothRegulator` и мгновенный IPC без UAC
- Плавный закон управления на базе полинома Эрмита (`smoothstep`) + широкодиапазонного `I-PD` (`2100..6800 RPM`) с многомасштабными EMA-фильтрами, мягкой зоной нечувствительности `±0.45 °C`, гистерезисом `20 RPM` и двухступенчатой термозащитой.
- Повторный клик по ярлыку на Рабочем столе или вызов CLI (`--status`, `--mode`, `--quit`) мгновенно обращается к уже запущенному процессу по локальному Named Pipe (`\\.\pipe\ThinkBookFanControl_Rust_v2`) без повторного окна UAC.

---

## 🇬🇧 What's New in v2.0.0

- **100% Native Rust + Win32 Rewrite**: Standalone `.exe` is now **`0.70 MB` (`736,256` bytes)** instead of `185 MB`, using **`~7–12 MB` RAM** instead of `157 MB` and **`< 0.01%` idle CPU**.
- **Dual Labeled System Tray Badges (`[CPU]` & `[GPU]`)**: Clearly distinguishes CPU average temperature from GPU temperature / `OFF` (`D3cold` `0.0W` sleep).
- **16-Core Intel Core Ultra 7 155H Matrix (`6P + 8E + 2LP`) + CPU Average**: Ring-0 MSR per-core DTS temperatures (`0x19C`/`0x1A2`), RAPL package power (`W`), per-core load (`%`), and real-time Turbo clocks (`GHz`).
- **True Zero-Wake NVIDIA Optimus `D3cold (0.0W)`**: Dynamically unloads `nvml.dll` via `FreeLibrary` when idle and ignores transient `0% / 0 MB` OS DXGI probes.
- **Instant UAC-Free IPC & CLI**: Named Pipe IPC supports `--status`, `--mode <m>`, `--minimized`, `--quit`, and `--self-test`.

---

## 🔐 Checksums (SHA-256)

- `ThinkBookFanControl-v2.0.0-win64.zip` (`450,523` bytes / `440 KB`):
  `81D02F6A9E57B309EAD506FBA62FA774F68BD8F45587457596F3F885FA7DD04A`
- `ThinkBookFanControl.exe` (`736,256` bytes / `0.70 MB`):
  `2F6CE0E7B7EA3F5F1C2B052AC4A60879D15AE39EEFE8D27073C71B2940B66C8C`
- `WinRing0x64.sys` (`14,544` bytes, Authenticode Valid):
  `11BD2C9F9E2397C9A16E0990E4ED2CF0679498FE0FD418A3DFDAC60B5C160EE5`
