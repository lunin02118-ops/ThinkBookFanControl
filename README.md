# ThinkBook Fan Control (Lenovo 14 G6+ IMH)

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011%20x64-0078D6?style=flat&logo=windows)](https://github.com/lunin02118-ops/ThinkBookFanControl)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt6-41CD52.svg?logo=qt)](https://www.qt.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/lunin02118-ops/ThinkBookFanControl?color=orange&logo=github)](https://github.com/lunin02118-ops/ThinkBookFanControl/releases)

---

> [!IMPORTANT]
> **Languages / Языки**: [Русский](#русский) | [English](#english)

---

<a name="русский"></a>
## 🇷🇺 Русский

**ThinkBook Fan Control** — специализированный комплекс точного аппаратного управления вентиляторами и мониторинга телеметрии для ноутбука **Lenovo ThinkBook 14 G6+ IMH** (модель `21LD`, процессор Intel Core Ultra Meteor Lake + дискретная видеокарта NVIDIA GeForce RTX 4060 Laptop GPU).

Большинство универсальных утилит (таких как *FanControl*, *SpeedFan*, *Notebook FanControl*) не имеют доступа к встроенному контроллеру (EC) современных ноутбуков Lenovo на платформе Meteor Lake. Данная программа напрямую взаимодействует с драйвером ACPI/EC через закрытый WMI-интерфейс Lenovo, обеспечивая абсолютный контроль над охлаждением.

---

### ✨ Ключевые возможности

1. **Прямое аппаратное управление через ACPI/EC WMI**:
   - Адресация в пространстве `root\wmi:LENOVO_OTHER_METHOD`.
   - Вентилятор 1 (CPU): метод `0x04030001` (2000–7700 RPM).
   - Вентилятор 2 (GPU): метод `0x04030002` (2000–7700 RPM).
   - Обход ограничений стандартных BIOS-таблиц и сторонних программ.
2. **Режим «Целевая 65°C» (Closed-loop PI-регулятор)**:
   - Разработан специально для непрерывных тяжелых нагрузок (рендеринг, майнинг криптовалют, вычисления).
   - ПИ-регулятор с зоной нечувствительности (deadband) удерживает температуру кристалла GPU строго в диапазоне 60–65°C без постоянных резких скачков оборотов (акустический комфорт и сохранение ресурса гидродинамических подшипников).
3. **Режим «100% Турбо» (Full Boost)**:
   - Принудительная максимальная ШИМ-сатурация вентиляторов (~7300–7700 RPM).
   - Встроенный механизм анти-таймаута (anti-watchdog refresh) каждые 1.2–1.5 сек предотвращает сброс контроллера EC в заводской авто-режим.
4. **Интерактивная 6-точечная кривая вентиляторов**:
   - Визуальный графический редактор с поддержкой Drag-and-Drop точек.
   - Готовые профили: *Тихий (Quiet)*, *Сбалансированный*, *Майнинг (Цель 65°C)*, *Максимальное охлаждение*.
   - Плавная линейная интерполяция оборотов в реальном времени.
   - Выбор датчика привязки (GPU, CPU или максимальная температура).
5. **Ручной режим (Manual RPM)**:
   - Плавный слайдер и цифровой спинбокс с диапазоном от 2000 до 7500 RPM.
6. **Полная телеметрия в реальном времени**:
   - Температура GPU, потребляемая мощность (Ватты), нагрузка ядер и памяти через direct C-types NVML API.
   - Температура CPU через прямой опрос EC WMI (`0x05040000`).
   - Тахометры реальных оборотов обоих вентиляторов (Fan 1 и Fan 2).
7. **Интеграция с System Tray**:
   - Сворачивание в трей при закрытии крестиком.
   - Динамический бейдж в трее с выводом текущей температуры GPU прямо на иконке.
   - Контекстное меню быстрого переключения режимов и безопасного выхода.
8. **Аппаратная термозащита (Safety Override)**:
   - При превышении безопасного порога (GPU > 80°C или CPU > 85°C) система автоматически активирует 100% охлаждение независимо от выбранного режима.
9. **Безопасное восстановление BIOS при выходе**:
   - Перехват сигналов завершения процесса (`atexit`, `SIGINT`, `SIGTERM`).
   - При выходе из программы управление вентиляторами мгновенно и гарантированно возвращается штатному BIOS/EC ноутбука.

---

### 💻 Системные требования

- **Ноутбук**: Lenovo ThinkBook 14 G6+ IMH (Type 21LD)
- **Процессор / Платформа**: Intel Core Ultra 5 / 7 / 9 (Meteor Lake)
- **Видеокарта**: NVIDIA GeForce RTX 4060 Laptop GPU (с установленными драйверами NVIDIA Game Ready / Studio)
- **Операционная система**: Windows 10 / 11 (64-bit)
- **Права доступа**: **Запуск от имени Администратора** (требуется Windows для доступа к WMI-пространству `root\wmi`).

---

### 🚀 Установка и запуск

#### Вариант 1: Готовая портативная сборка (Рекомендуется)
1. Перейдите в раздел [**Releases**](https://github.com/lunin02118-ops/ThinkBookFanControl/releases).
2. Скачайте архив `ThinkBookFanControl-v1.0.0-win64.zip`.
3. Распакуйте архив в удобную папку (например, `C:\Tools\ThinkBookFanControl`).
4. Запустите `ThinkBookFanControl.exe` или `Run_As_Admin.bat` от имени Администратора.
5. *Установка сторонних компонентов или Python не требуется.*

#### Вариант 2: Запуск из исходного кода
1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/lunin02118-ops/ThinkBookFanControl.git
   cd ThinkBookFanControl
   ```
2. Запустите автоматический установщик и лаунчер:
   ```cmd
   Install_or_Run.bat
   ```
   *Скрипт запросит повышение прав UAC, установит необходимые зависимости (`PySide6`, `pywin32`) и запустит программу.*

---

### 🛠️ Архитектура проекта

```
ThinkBookFanControl/
├── app_icon.ico              # Иконка приложения (мультиразмерная ICO)
├── app_icon.png              # Графический логотип приложения
├── config.py                 # Менеджер настроек (JSON) и автозапуска в реестре
├── config.json               # Текущие пользовательские конфигурации
├── controller.py             # Фоновый поток телеметрии, ПИ-регулятор и интерполяция
├── curve_widget.py           # Кастомный виджет интерактивного графика кривой
├── hardware.py               # Аппаратный слой: Lenovo ACPI/EC WMI + NVIDIA NVML
├── main_window.py            # Главное окно PySide6, UI карточки, трей и бейджи
├── main.py                   # Точка входа, Single Instance Mutex, IPC, Safe Exit
├── test_suite.py             # Комплексный автоматизированный набор тестов
├── test_controller_simulation.py # Тестирование матмодели ПИ-регулятора
├── ThinkBookFanControl.spec   # Конфигурация сборщика PyInstaller
├── requirements.txt          # Список зависимостей Python
├── run.bat                   # Быстрый запуск с автоматическим запросом UAC
└── Install_or_Run.bat        # Интерактивное развертывание и сборка
```

---

<a name="english"></a>
## 🇬🇧 English

**ThinkBook Fan Control** is an advanced hardware fan controller and telemetry suite engineered specifically for the **Lenovo ThinkBook 14 G6+ IMH** (Model `21LD`, Intel Core Ultra Meteor Lake CPU + NVIDIA GeForce RTX 4060 Laptop GPU) running Windows.

Standard universal utilities (*FanControl*, *SpeedFan*, *Notebook FanControl*) are unable to communicate with the embedded controller (EC) of modern Meteor Lake Lenovo laptops. This utility communicates directly with the Lenovo EC ACPI subsystem via proprietary WMI calls, providing unconstrained thermal control.

---

### ✨ Features

1. **Direct ACPI / EC Hardware Control via WMI**:
   - Target namespace: `root\wmi:LENOVO_OTHER_METHOD`.
   - Fan 1 (CPU): Method ID `0x04030001` (2000–7700 RPM).
   - Fan 2 (GPU): Method ID `0x04030002` (2000–7700 RPM).
   - Bypasses BIOS power limits and fan lockouts.
2. **Dedicated "Target 65°C" Mode (Closed-Loop PI Regulator)**:
   - Designed for sustained heavy compute loads (3D rendering, crypto mining, AI training).
   - Closed-loop PI algorithm with configurable deadband keeps GPU core precisely between 60–65°C while preventing high-frequency RPM cycling to protect hydrodynamic bearings.
3. **"100% Turbo" (Full Boost) Mode**:
   - Forces full PWM saturation (~7300–7700 RPM).
   - Anti-watchdog background refresh prevents embedded controller timeouts from reverting fans to default mode.
4. **Interactive 6-Point Fan Curve Editor**:
   - Visual spline curve editor with drag-and-drop handles.
   - Built-in presets: *Quiet*, *Balanced*, *Mining (Target 65°C)*, *Maximum Cooling*.
   - Live operating point indicator displaying current temperature and commanded RPM.
5. **Manual RPM Control**:
   - Precise slider & spinbox to lock fan speeds up to 7500 RPM.
6. **Real-Time Telemetry**:
   - GPU Temperature, Power Draw (W), GPU Core/Memory Utilization via direct ctypes NVML.
   - CPU Temperature via embedded controller WMI sensor (`0x05040000`).
   - Live tachometer feedback for Fan 1 and Fan 2.
7. **System Tray Integration**:
   - Minimize-to-tray with background execution.
   - Dynamic tray icon displaying real-time GPU temperature badge.
   - Tray context menu for fast profile switching.
8. **Thermal Emergency Safety Override**:
   - Automatic fail-safe: immediately commands 100% boost if GPU exceeds 80°C or CPU exceeds 85°C.
9. **Safe BIOS Restoration on Exit**:
   - Clean shutdown handlers (`atexit`, `SIGINT`, `SIGTERM`) ensure fans immediately revert to factory BIOS auto mode upon application exit or crash.

---

### 📦 System Requirements

- **Target Laptop**: Lenovo ThinkBook 14 G6+ IMH (Model `21LD`)
- **CPU Platform**: Intel Core Ultra (Meteor Lake)
- **Discrete GPU**: NVIDIA GeForce RTX 4060 Laptop GPU (NVIDIA Display Driver installed)
- **OS**: Windows 10 or Windows 11 (64-bit)
- **Permissions**: **Run as Administrator** (required for `root\wmi` hardware access).

---

### 🚀 Quick Start

#### Option 1: Standalone Portable Release (Recommended)
1. Download `ThinkBookFanControl-v1.0.0-win64.zip` from [**Releases**](https://github.com/lunin02118-ops/ThinkBookFanControl/releases).
2. Extract the archive anywhere on your machine.
3. Right-click `ThinkBookFanControl.exe` (or `Run_As_Admin.bat`) and select **Run as Administrator**.

#### Option 2: Run from Source
1. Clone the repository:
   ```bash
   git clone https://github.com/lunin02118-ops/ThinkBookFanControl.git
   cd ThinkBookFanControl
   ```
2. Execute the launcher script:
   ```cmd
   Install_or_Run.bat
   ```

---

### 🧪 Testing & Verification

Automated unit tests covering curve interpolation, config persistence, WMI clamping, PI controller stability, and IPC can be executed via:

```bash
python test_suite.py
python test_controller_simulation.py
```

---

### ⚠️ Disclaimer

This tool manipulates hardware fan controllers at the embedded controller level. The software includes built-in thermal safety overrides, but users remain responsible for monitoring hardware temperatures. This project is not affiliated with or endorsed by Lenovo or NVIDIA.

---

### 📄 License

This project is licensed under the [MIT License](LICENSE).
