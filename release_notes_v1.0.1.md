# Release Notes — ThinkBook Fan Control v1.0.1

## 🇷🇺 Что нового в версии 1.0.1

### 1. Спаренный термоиндекс для общей системы охлаждения (Dual-Sensor Heatpipe Coupling)
- В ноутбуке Lenovo ThinkBook 14 G6+ IMH процессор (Intel Core Ultra 7 155H) и видеокарта (RTX 4060) делят общий медный радиатор с тепловыми трубками.
- В версии 1.0.1 контур ПИД-регулирования режима «Целевая 65°C» непрерывно отслеживает оба чипа (`eff_cpu = filtered_cpu - 6.0`, `primary_temp = max(gpu, eff_cpu)`).
- Если CPU начинает нагреваться из-за фоновых вычислений, вентиляторы плавно и упреждающе поднимают обороты **до** того, как чип нагреется до опасных значений.

### 2. Защита от перегрева с гистерезисом (Anti-Hunting Hysteresis)
- Полностью устранены резкие ступенчатые скачки кулеров между тихим режимом и аварийным максимумом.
- Добавлен алгоритмический гистерезис: аварийное охлаждение удерживает повышенные обороты до гарантированного снижения температуры ниже 75°C GPU / 78°C CPU.

### 3. Исправление инициализации профилей и сохранение настроек
- Устранено возможное залипание режима Boost при перезапуске.

---

## 🇬🇧 What's New in v1.0.1

### 1. Coupled Dual-Sensor Regulation for Shared Heatpipe
- The ThinkBook 14 G6+ IMH shares heatpipes between the Intel Core Ultra 7 155H and NVIDIA RTX 4060 Laptop GPU.
- In v1.0.1, the PID closed loop dynamically monitors both sensors, proactively scaling fan RPM when CPU activity rises before either component overheats.

### 2. Progressive Safety Override with Hysteresis
- Eliminates stepped fan oscillation (hunting) around 80–85°C boundaries.
- Latching hysteresis maintains cooling until temperatures safely subside.

### 3. Clean Profile State Persistence
- Fixed configuration drift and ensures stable default operation in Target 65°C closed-loop mode.
