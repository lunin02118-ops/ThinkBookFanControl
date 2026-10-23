# ThinkBook Fan Control v1.0.0 — Direct EC WMI Thermal Suite

**Lenovo ThinkBook 14 G6+ IMH (Model 21LD)**
Intel Core Ultra Meteor Lake • NVIDIA GeForce RTX 4060 Laptop GPU • Windows 10/11 x64

---

### 🇷🇺 Описание релиза

Первый официальный релиз специализированной утилиты для аппаратного управления охлаждением и мониторинга **Lenovo ThinkBook 14 G6+ IMH**. Программа обходит стандартные ограничения сторонних утилит (FanControl, SpeedFan) за счет прямого взаимодействия с контроллером EC через закрытый WMI-интерфейс Lenovo (`root\wmi:LENOVO_OTHER_METHOD`).

#### Основные возможности:
- ⚡ **Режим «Целевая 65°C» (Майнинг)**: Интеллектуальный ПИ-регулятор с зоной нечувствительности, поддерживающий температуру GPU строго в коридоре 60–65°C под непрерывной нагрузкой.
- 🚀 **Режим «100% Турбо» (Full Boost)**: Максимальная ШИМ-сатурация (~7300–7700 RPM) с автоматическим обновлением для защиты от таймаута EC.
- 📈 **Интерактивная кривая вентиляторов**: 6-точечный график с Drag-and-Drop, пресетами (*Тихий*, *Сбалансированный*, *Майнинг*, *Максимальный*) и индикатором рабочей точки.
- 🎛️ **Ручной режим (Manual RPM)**: Точная фиксация оборотов слайдером до 7500 RPM.
- 🍃 **Штатный BIOS (Auto)**: Моментальный возврат управления заводским алгоритмам EC.
- 📊 **Полная телеметрия**: Температура GPU/CPU, мощность GPU в Ваттах, утилизация GPU/памяти через NVML, реальные обороты кулеров Fan 1 и Fan 2.
- 🔔 **Системный трей**: Сворачивание в трей, динамическая иконка с выводом температуры GPU в реальном времени.
- 🛡️ **Безопасность**: Защита от перегрева при >80°C и гарантированный сброс в штатный BIOS при закрытии программы.

#### Инструкция по запуску:
1. Скачайте `ThinkBookFanControl-v1.0.0-win64.zip` (или `ThinkBookFanControl-v1.0.0-Portable.exe`).
2. Распакуйте архив в любую папку.
3. Запустите `Run_As_Admin.bat` или нажмите правой кнопкой на `ThinkBookFanControl.exe` -> **«Запуск от имени администратора»**.

---

### 🇬🇧 English Release Notes

First official release of **ThinkBook Fan Control** — the dedicated hardware thermal management and telemetry software for **Lenovo ThinkBook 14 G6+ IMH**.

#### Key Features:
- ⚡ **Target 65°C Mode**: Closed-loop PI temperature regulator keeping GPU at stable 60–65°C with minimal fan bearing wear.
- 🚀 **100% Turbo (Full Boost)**: Commands maximum PWM saturation (~7300–7700 RPM) with anti-watchdog refresh.
- 📈 **Interactive 6-Point Fan Curve Editor**: Visual editor with drag-and-drop handles and pre-tuned profiles.
- 🎛️ **Manual RPM Control**: Precise slider and spinbox up to 7500 RPM.
- 🍃 **Factory BIOS Auto**: Instant handover to standard Lenovo EC firmware.
- 📊 **Real-Time Telemetry**: GPU temp, power draw (W), GPU core & VRAM load via NVML, CPU temp via EC WMI, and live tachometers.
- 🔔 **Tray Integration**: Minimize to tray with live temperature badge icon.
- 🛡️ **Hardware Safety**: Automatic thermal emergency boost at >80°C and safe BIOS reset on exit.

#### Getting Started:
1. Download `ThinkBookFanControl-v1.0.0-win64.zip` below.
2. Extract to any directory.
3. Right-click `ThinkBookFanControl.exe` (or run `Run_As_Admin.bat`) and select **"Run as administrator"**.
