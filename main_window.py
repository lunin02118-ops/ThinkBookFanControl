"""
Main GUI Window for Lenovo ThinkBook Fan Control (14 G6+ IMH)
PySide6 Implementation featuring:
- Dual labeled HiDPI System Tray badges (dedicated [CPU] and [GPU] icons)
- Live 16-core Intel Core Ultra (6P + 8E + 2LP-E) per-core temperature/load/clock matrix + CPU Average
- Live NVIDIA RTX 4060 + Intel Arc Optimus RTD3 (D3cold) telemetry panel
- Interactive hysteresis-stabilized curve editor, manual RPM, BIOS Auto, Boost, and smooth I-PD target mode
"""

import os
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QSlider,
    QSpinBox,
    QComboBox,
    QStackedWidget,
    QFrame,
    QCheckBox,
    QProgressBar,
    QSystemTrayIcon,
    QMenu,
    QApplication,
)
from PySide6.QtGui import QIcon, QFont, QColor, QAction, QPixmap, QPainter
from PySide6.QtCore import Qt, Slot, QTimer, QSignalBlocker, QRect

from config import ConfigManager, DEFAULT_CURVE, PRESETS, get_app_dir
from controller import FanControllerThread
from curve_widget import InteractiveCurveWidget

APP_DIR = get_app_dir()
ICON_PATH = os.path.join(APP_DIR, "app_icon.ico")
PNG_ICON_PATH = os.path.join(APP_DIR, "app_icon.png")

BOOST_ACTIVE_STYLE = (
    "background-color: #2a1215; color: #f43f5e; font-size: 13px; "
    "font-weight: bold; padding: 10px 14px; border-radius: 6px; border: 1px solid #7f1d1d;"
)
BOOST_IDLE_STYLE = (
    "background-color: #1e2230; color: #94a3b8; font-size: 12px; font-weight: 600; "
    "padding: 10px 14px; border-radius: 6px; border: 1px solid #2d3348;"
)


DARK_STYLESHEET = """
QMainWindow, QWidget#centralWidget {
    background-color: #12141a;
    color: #e2e8f0;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
}

QFrame.card {
    background-color: #1a1d26;
    border: 1px solid #2a2e3d;
    border-radius: 10px;
    padding: 8px;
}

QFrame.card:hover {
    border-color: #3b4256;
}

QFrame.coreTile {
    background-color: #141720;
    border: 1px solid #252a3a;
    border-radius: 6px;
}

QProgressBar.coreBar {
    background-color: #1f2433;
    border: none;
    border-radius: 2px;
    max-height: 4px;
    min-height: 4px;
}

QProgressBar.coreBar::chunk {
    background-color: #38bdf8;
    border-radius: 2px;
}

QLabel {
    color: #cbd5e1;
}

QLabel#titleLabel {
    color: #f8fafc;
    font-size: 17px;
    font-weight: bold;
}

QLabel#subtitleLabel {
    color: #94a3b8;
    font-size: 12px;
}

QLabel#cardTitle {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}

QLabel#cardValue {
    font-size: 23px;
    font-weight: bold;
}

QRadioButton {
    background-color: #1e2230;
    color: #94a3b8;
    border: 1px solid #2d3348;
    border-radius: 8px;
    padding: 7px 13px;
    font-size: 12px;
    font-weight: 600;
}

QRadioButton:hover {
    background-color: #262c3e;
    color: #f1f5f9;
}

QRadioButton:checked {
    background-color: #0284c7;
    color: #ffffff;
    border: 1px solid #38bdf8;
}

QRadioButton::indicator {
    width: 0px;
    height: 0px;
}

QPushButton {
    background-color: #242938;
    color: #e2e8f0;
    border: 1px solid #333a4f;
    border-radius: 6px;
    padding: 6px 13px;
    font-size: 12px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #31384d;
    border-color: #475373;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #1a1e2b;
}

QPushButton#primaryBtn {
    background-color: #0284c7;
    border: 1px solid #38bdf8;
    color: #ffffff;
}

QPushButton#primaryBtn:hover {
    background-color: #0369a1;
}

QPushButton#turboBtn {
    background-color: #be123c;
    border: 1px solid #f43f5e;
    color: #ffffff;
    font-weight: bold;
}

QPushButton#turboBtn:hover {
    background-color: #9f1239;
}

QPushButton#safeExitBtn {
    background-color: #334155;
    border: 1px solid #475569;
    color: #f1f5f9;
}

QPushButton#safeExitBtn:hover {
    background-color: #dc2626;
    border-color: #ef4444;
}

QSlider::groove:horizontal {
    height: 8px;
    background: #242938;
    border-radius: 4px;
}

QSlider::sub-page:horizontal {
    background: #0284c7;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background: #38bdf8;
    border: 2px solid #ffffff;
    width: 18px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 9px;
}

QSlider::handle:horizontal:hover {
    background: #7dd3fc;
}

QSpinBox, QComboBox {
    background-color: #1e2230;
    border: 1px solid #333a4f;
    border-radius: 6px;
    color: #ffffff;
    padding: 4px 8px;
    font-size: 13px;
    font-weight: bold;
}

QComboBox QAbstractItemView {
    background-color: #1a1d26;
    color: #f1f5f9;
    selection-background-color: #0284c7;
    border: 1px solid #333a4f;
}

QCheckBox {
    color: #94a3b8;
    font-size: 12px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #3b4256;
    background: #1e2230;
}

QCheckBox::indicator:checked {
    background: #0284c7;
    border-color: #38bdf8;
}

QMenu {
    background-color: #1a1d26;
    border: 1px solid #333a4f;
    color: #f1f5f9;
    padding: 5px;
}

QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #0284c7;
}

QMenu::separator {
    height: 1px;
    background: #2d3348;
    margin: 4px 8px;
}
"""


class MainWindow(QMainWindow):
    """Main Application Window for ThinkBook Fan Controller."""

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config = config_manager
        self.setWindowTitle("ThinkBook Fan Control — Lenovo 14 G6+ IMH")
        self.resize(980, 800)
        self.setMinimumSize(880, 680)

        app_icon = QIcon(ICON_PATH if os.path.exists(ICON_PATH) else PNG_ICON_PATH)
        self.setWindowIcon(app_icon)
        self.setStyleSheet(DARK_STYLESHEET)

        self._save_debounce_timer = QTimer(self)
        self._save_debounce_timer.setSingleShot(True)
        self._save_debounce_timer.setInterval(280)
        self._save_debounce_timer.timeout.connect(self.config.save)

        self._core_tile_widgets: list[dict] = []

        self.controller = FanControllerThread(self.config, self)
        self.controller.telemetry_updated.connect(self.on_telemetry_updated)

        self._init_ui()
        self._init_tray(app_icon)

        self.controller.start()

    def _init_ui(self):
        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # 1. Header Bar
        header_layout = QHBoxLayout()
        header_info = QVBoxLayout()
        header_info.setSpacing(2)

        title = QLabel("ThinkBook Fan Control (Lenovo 14 G6+ IMH)")
        title.setObjectName("titleLabel")
        subtitle = QLabel("Intel Core Ultra 7 155H (16 ядер) • NVIDIA RTX 4060 + Intel Arc • Плавное управление EC WMI")
        subtitle.setObjectName("subtitleLabel")
        header_info.addWidget(title)
        header_info.addWidget(subtitle)
        header_layout.addLayout(header_info)

        header_layout.addStretch()

        self.btn_toggle_cores = QPushButton("Скрыть панель ядер")
        self.btn_toggle_cores.setFixedHeight(28)
        self.btn_toggle_cores.clicked.connect(self._toggle_hardware_matrix)
        header_layout.addWidget(self.btn_toggle_cores)

        init_t = int(self.config.get("target_temp", 65))
        self.lbl_mode_badge = QLabel(f"⚡ РЕЖИМ: ЦЕЛЕВАЯ {init_t}°C")
        self.lbl_mode_badge.setStyleSheet(
            "background-color: #0284c7; color: #ffffff; font-weight: bold; "
            "padding: 6px 14px; border-radius: 12px; font-size: 12px;"
        )
        header_layout.addWidget(self.lbl_mode_badge)
        main_layout.addLayout(header_layout)

        # 2. Top 5 Summary Cards (CPU Average + GPU + Power + Fan 1 + Fan 2)
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(8)

        card_cpu, self.lbl_cpu_temp, self.lbl_cpu_sub = self._create_card(
            "CPU СРЕДНЯЯ", "-- °C", "#38bdf8", "Пик: --°C • EC: --°C"
        )
        cards_layout.addWidget(card_cpu)

        card_gpu, self.lbl_gpu_temp, self.lbl_gpu_sub = self._create_card(
            "GPU RTX 4060", "-- °C", "#10b981", "Load: --%"
        )
        cards_layout.addWidget(card_gpu)

        card_pwr, self.lbl_gpu_pwr, self.lbl_gpu_pwr_sub = self._create_card(
            "ПИТАНИЕ GPU / CPU", "-- W", "#f43f5e", "CPU: -- W"
        )
        cards_layout.addWidget(card_pwr)

        card_f1, self.lbl_fan1_rpm, self.lbl_fan1_sub = self._create_card(
            "FAN 1 (CPU)", "-- RPM", "#818cf8", "Target: --"
        )
        cards_layout.addWidget(card_f1)

        card_f2, self.lbl_fan2_rpm, self.lbl_fan2_sub = self._create_card(
            "FAN 2 (GPU)", "-- RPM", "#a78bfa", "Target: --"
        )
        cards_layout.addWidget(card_f2)

        main_layout.addLayout(cards_layout)

        # 3. Detailed CPU Per-Core Matrix + GPU Telemetry Panel (Expanded by default!)
        self.hw_matrix_panel = self._build_hardware_matrix_panel()
        main_layout.addWidget(self.hw_matrix_panel)

        # 4. Mode Selection Button Group
        mode_box = QHBoxLayout()
        mode_box.setSpacing(8)

        self.btn_group_modes = QButtonGroup(self)

        self.rb_target65 = QRadioButton("🎯 Авто-цель °C (Тихий ПИД)")
        self.rb_curve = QRadioButton("📈 Кривая вентиляторов")
        self.rb_manual = QRadioButton("🎛️ Ручной режим (RPM)")
        self.rb_auto = QRadioButton("🍃 Штатный BIOS (Auto)")
        self.rb_boost = QRadioButton("🚀 100% Турбо (Full Boost)")

        self.btn_group_modes.addButton(self.rb_target65, 0)
        self.btn_group_modes.addButton(self.rb_curve, 1)
        self.btn_group_modes.addButton(self.rb_manual, 2)
        self.btn_group_modes.addButton(self.rb_auto, 3)
        self.btn_group_modes.addButton(self.rb_boost, 4)

        mode_box.addWidget(self.rb_target65)
        mode_box.addWidget(self.rb_curve)
        mode_box.addWidget(self.rb_manual)
        mode_box.addWidget(self.rb_auto)
        mode_box.addWidget(self.rb_boost)

        main_layout.addLayout(mode_box)

        # 5. Stacked Pages for Modes
        self.stack = QStackedWidget(self)

        self.page_target65 = self._build_page_target65()
        self.page_curve = self._build_page_curve()
        self.page_manual = self._build_page_manual()
        self.page_auto = self._build_page_auto()
        self.page_boost = self._build_page_boost()

        self.stack.addWidget(self.page_target65)
        self.stack.addWidget(self.page_curve)
        self.stack.addWidget(self.page_manual)
        self.stack.addWidget(self.page_auto)
        self.stack.addWidget(self.page_boost)

        main_layout.addWidget(self.stack, 1)

        self.btn_group_modes.idClicked.connect(self.on_mode_changed)

        cur_mode = self.config.get("mode", "target_65")
        mode_map = {"target_65": 0, "curve": 1, "manual": 2, "auto": 3, "boost": 4}
        idx = mode_map.get(cur_mode, 0)
        self._set_mode_from_ui(idx, persist=False)

        # 6. Bottom Toolbar
        bottom_layout = QHBoxLayout()
        self.chk_min_to_tray = QCheckBox("В трей при закрытии")
        self.chk_min_to_tray.setChecked(bool(self.config.get("minimize_to_tray_on_close", True)))
        self.chk_min_to_tray.toggled.connect(
            lambda v: self.config.set("minimize_to_tray_on_close", bool(v))
        )
        bottom_layout.addWidget(self.chk_min_to_tray)

        self.chk_autostart = QCheckBox("Автозапуск с Windows")
        self.chk_autostart.setChecked(self.config.is_autostart_enabled())
        self.chk_autostart.toggled.connect(self._on_autostart_toggled)
        bottom_layout.addWidget(self.chk_autostart)

        self.chk_safety = QCheckBox("Защита от перегрева (>80°C)")
        self.chk_safety.setChecked(bool(self.config.get("safety_override", True)))
        self.chk_safety.toggled.connect(
            lambda v: self.controller.set_safety_override(bool(v))
        )
        bottom_layout.addWidget(self.chk_safety)

        bottom_layout.addStretch()

        btn_min = QPushButton("Свернуть в трей")
        btn_min.clicked.connect(self.hide)
        bottom_layout.addWidget(btn_min)

        btn_exit = QPushButton("Выход (Сброс на BIOS)")
        btn_exit.setObjectName("safeExitBtn")
        btn_exit.clicked.connect(self.safe_exit)
        bottom_layout.addWidget(btn_exit)

        main_layout.addLayout(bottom_layout)

    def _create_card(self, title_text, init_val, color_hex, sub_text):
        frame = QFrame()
        frame.setObjectName("cardFrame")
        frame.setProperty("class", "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(1)

        lbl_t = QLabel(title_text)
        lbl_t.setObjectName("cardTitle")

        lbl_v = QLabel(init_val)
        lbl_v.setObjectName("cardValue")
        lbl_v.setStyleSheet(f"color: {color_hex};")

        lbl_s = QLabel(sub_text)
        lbl_s.setStyleSheet("color: #64748b; font-size: 11px;")

        layout.addWidget(lbl_t)
        layout.addWidget(lbl_v)
        layout.addWidget(lbl_s)
        return frame, lbl_v, lbl_s

    def _build_hardware_matrix_panel(self) -> QWidget:
        """Builds the live 16-core CPU + Average + GPU details panel."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        # ---- Left Card: CPU Cores + Average ----
        cpu_card = QFrame()
        cpu_card.setProperty("class", "card")
        cpu_vbox = QVBoxLayout(cpu_card)
        cpu_vbox.setContentsMargins(10, 8, 10, 8)
        cpu_vbox.setSpacing(6)

        cpu_hdr = QHBoxLayout()
        cpu_hdr.setSpacing(8)

        self.lbl_cpu_matrix_title = QLabel("🧠 ЯДРА ПРОЦЕССОРА (6P + 8E + 2LP)")
        self.lbl_cpu_matrix_title.setStyleSheet("color: #f1f5f9; font-size: 12px; font-weight: bold;")
        cpu_hdr.addWidget(self.lbl_cpu_matrix_title)

        cpu_hdr.addStretch()

        self.lbl_cpu_avg_pill = QLabel("Средняя: -- °C")
        self.lbl_cpu_avg_pill.setStyleSheet(
            "background-color: #0c4a6e; color: #38bdf8; font-weight: bold; "
            "font-size: 11px; padding: 3px 9px; border-radius: 8px; border: 1px solid #0284c7;"
        )
        cpu_hdr.addWidget(self.lbl_cpu_avg_pill)

        self.lbl_cpu_max_pill = QLabel("Пик: -- °C")
        self.lbl_cpu_max_pill.setStyleSheet(
            "background-color: #1e293b; color: #cbd5e1; font-weight: 600; "
            "font-size: 11px; padding: 3px 8px; border-radius: 8px;"
        )
        cpu_hdr.addWidget(self.lbl_cpu_max_pill)

        self.lbl_cpu_load_pill = QLabel("CPU: --% • -- W")
        self.lbl_cpu_load_pill.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; font-weight: 600; "
            "font-size: 11px; padding: 3px 8px; border-radius: 8px;"
        )
        cpu_hdr.addWidget(self.lbl_cpu_load_pill)

        cpu_vbox.addLayout(cpu_hdr)

        # 8x2 Grid of 16 Core Tiles
        grid = QGridLayout()
        grid.setSpacing(5)
        self._core_tile_widgets.clear()

        default_labels = (
            [f"P{i}" for i in range(1, 7)]
            + [f"E{i}" for i in range(1, 9)]
            + ["LP1", "LP2"]
        )

        for idx in range(16):
            tile = QFrame()
            tile.setProperty("class", "coreTile")
            t_lay = QVBoxLayout(tile)
            t_lay.setContentsMargins(6, 4, 6, 4)
            t_lay.setSpacing(2)

            top_r = QHBoxLayout()
            top_r.setSpacing(4)
            lbl_name = QLabel(default_labels[idx])
            lbl_name.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: bold;")
            lbl_temp = QLabel("--°C")
            lbl_temp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lbl_temp.setStyleSheet("color: #10b981; font-size: 12px; font-weight: bold;")
            top_r.addWidget(lbl_name)
            top_r.addStretch()
            top_r.addWidget(lbl_temp)
            t_lay.addLayout(top_r)

            lbl_meta = QLabel("0% • -- ГГц")
            lbl_meta.setStyleSheet("color: #64748b; font-size: 9px;")
            t_lay.addWidget(lbl_meta)

            bar = QProgressBar()
            bar.setProperty("class", "coreBar")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            t_lay.addWidget(bar)

            r_idx = idx // 8
            c_idx = idx % 8
            grid.addWidget(tile, r_idx, c_idx)

            self._core_tile_widgets.append({
                "frame": tile,
                "name": lbl_name,
                "temp": lbl_temp,
                "meta": lbl_meta,
                "bar": bar,
            })

        cpu_vbox.addLayout(grid)
        row.addWidget(cpu_card, 3)

        # ---- Right Card: Dedicated GPU + iGPU Details ----
        gpu_card = QFrame()
        gpu_card.setProperty("class", "card")
        gpu_vbox = QVBoxLayout(gpu_card)
        gpu_vbox.setContentsMargins(10, 8, 10, 8)
        gpu_vbox.setSpacing(6)

        gpu_hdr = QHBoxLayout()
        lbl_g_title = QLabel("🎮 ВИДЕОКАРТА")
        lbl_g_title.setStyleSheet("color: #f1f5f9; font-size: 12px; font-weight: bold;")
        gpu_hdr.addWidget(lbl_g_title)
        gpu_hdr.addStretch()

        self.lbl_gpu_state_pill = QLabel("Ожидание...")
        self.lbl_gpu_state_pill.setStyleSheet(
            "background-color: #064e3b; color: #34d399; font-size: 10px; "
            "font-weight: bold; padding: 2px 7px; border-radius: 7px;"
        )
        gpu_hdr.addWidget(self.lbl_gpu_state_pill)
        gpu_vbox.addLayout(gpu_hdr)

        g_grid = QGridLayout()
        g_grid.setVerticalSpacing(5)
        g_grid.setHorizontalSpacing(8)

        def _add_gpu_row(r_i: int, key_txt: str, init_v: str):
            k = QLabel(key_txt)
            k.setStyleSheet("color: #94a3b8; font-size: 11px;")
            v = QLabel(init_v)
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            v.setStyleSheet("color: #f1f5f9; font-size: 11px; font-weight: bold;")
            g_grid.addWidget(k, r_i, 0)
            g_grid.addWidget(v, r_i, 1)
            return v

        self.lbl_gd_chip = _add_gpu_row(0, "dGPU чип:", "RTX 4060 Laptop")
        self.lbl_gd_temp = _add_gpu_row(1, "Температура:", "-- °C")
        self.lbl_gd_pwr = _add_gpu_row(2, "Потребление:", "-- W")
        self.lbl_gd_util = _add_gpu_row(3, "Ядро / Память:", "--% / --%")
        self.lbl_gd_igpu = _add_gpu_row(4, "Дисплей (iGPU):", "Intel Arc • Активна")

        gpu_vbox.addLayout(g_grid)
        gpu_vbox.addStretch()
        row.addWidget(gpu_card, 1)

        return container

    def _toggle_hardware_matrix(self):
        vis = not self.hw_matrix_panel.isVisible()
        self.hw_matrix_panel.setVisible(vis)
        self.btn_toggle_cores.setText("Скрыть панель ядер" if vis else "Показать ядра CPU / GPU")

    # ------------------ Mode Pages ------------------

    def _build_page_target65(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        banner = QFrame()
        banner.setProperty("class", "card")
        b_layout = QVBoxLayout(banner)
        b_layout.setSpacing(4)

        b_title = QLabel("🎯 Нативное плавное удержание целевой температуры (Адаптивный I-PD)")
        b_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #38bdf8;")
        b_desc = QLabel(
            "Двухконтурный регулятор контролирует тепловую инерцию общей теплотрубки GPU RTX 4060 и CPU Core Ultra. "
            "В простое удерживает бесшумный минимум (~2100 RPM) и отпускает RTX 4060 в сон D3cold (0 Вт), "
            "а под длительной нагрузкой плавно выводит и точно стабилизирует температуру на заданной цели без рывков."
        )
        b_desc.setWordWrap(True)
        b_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")

        b_layout.addWidget(b_title)
        b_layout.addWidget(b_desc)
        layout.addWidget(banner)

        ctrl_card = QFrame()
        ctrl_card.setProperty("class", "card")
        c_layout = QGridLayout(ctrl_card)
        c_layout.setSpacing(10)

        lbl_target = QLabel("Целевая температура:")
        lbl_target.setStyleSheet("font-size: 13px; font-weight: bold; color: #f1f5f9;")

        self.spin_target_temp = QSpinBox()
        self.spin_target_temp.setRange(50, 80)
        self.spin_target_temp.setValue(int(self.config.get("target_temp", 65)))
        self.spin_target_temp.setSuffix(" °C")
        self.spin_target_temp.setFixedWidth(110)
        self.spin_target_temp.valueChanged.connect(self.on_target_temp_changed)

        c_layout.addWidget(lbl_target, 0, 0)
        c_layout.addWidget(self.spin_target_temp, 0, 1)

        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(8)
        for t in [60, 65, 68, 72]:
            btn = QPushButton(f"{t}°C {'(Оптимум)' if t == 65 else ''}".strip())
            btn.clicked.connect(lambda _, temp=t: self.spin_target_temp.setValue(temp))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()

        c_layout.addWidget(QLabel("Быстрые пресеты:"), 1, 0)
        c_layout.addLayout(quick_layout, 1, 1)

        self.lbl_target65_status = QLabel("Статус регулятора: инициализация датчиков...")
        self.lbl_target65_status.setStyleSheet(
            "background-color: #1e293b; color: #10b981; font-weight: bold; "
            "padding: 8px 12px; border-radius: 6px; font-size: 12px;"
        )
        c_layout.addWidget(self.lbl_target65_status, 2, 0, 1, 2)

        layout.addWidget(ctrl_card)
        layout.addStretch()
        return w

    def _build_page_curve(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        sensor_box = QHBoxLayout()
        sensor_box.setSpacing(8)
        lbl_s = QLabel("Датчик для кривой:")
        lbl_s.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 600;")
        sensor_box.addWidget(lbl_s)

        self.combo_curve_sensor = QComboBox()
        self.combo_curve_sensor.addItem("Максимум: GPU / CPU (Безопасный)", "max")
        self.combo_curve_sensor.addItem("Только GPU (NVIDIA RTX 4060)", "gpu")
        self.combo_curve_sensor.addItem("Только CPU (Intel Core Ultra)", "cpu")
        cur_sensor = self.config.get("curve_sensor", "max")
        idx = self.combo_curve_sensor.findData(cur_sensor)
        if idx >= 0:
            self.combo_curve_sensor.setCurrentIndex(idx)
        self.combo_curve_sensor.currentIndexChanged.connect(
            lambda: self.controller.set_curve_sensor(self.combo_curve_sensor.currentData())
        )
        sensor_box.addWidget(self.combo_curve_sensor)
        sensor_box.addStretch()
        layout.addLayout(sensor_box)

        self.curve_widget = InteractiveCurveWidget(self)
        self.curve_widget.setMinimumSize(460, 210)
        self.curve_widget.set_points(self.config.get("curve_points"))
        self.curve_widget.set_target_indicator(float(self.config.get("target_temp", 65)))
        self.curve_widget.curve_changed.connect(self.on_curve_modified)
        self.curve_widget.curve_edit_finished.connect(lambda _: self.config.save())
        layout.addWidget(self.curve_widget, 1)

        tb_layout = QHBoxLayout()
        tb_layout.setSpacing(8)

        tb_layout.addWidget(QLabel("Пресеты кривой:"))
        for name, pts in PRESETS.items():
            btn = QPushButton(name)
            btn.clicked.connect(lambda _, p=pts: self.apply_curve_preset(p))
            tb_layout.addWidget(btn)

        tb_layout.addStretch()

        btn_add_pt = QPushButton("+ Точка")
        btn_add_pt.clicked.connect(self.add_curve_point)
        tb_layout.addWidget(btn_add_pt)

        btn_reset_def = QPushButton("Сбросить")
        btn_reset_def.clicked.connect(lambda: self.apply_curve_preset(DEFAULT_CURVE))
        tb_layout.addWidget(btn_reset_def)

        layout.addLayout(tb_layout)

        hint = QLabel(
            "💡 Совет: перетаскивайте точки мышью (сглаживание и гистерезис 1.2°C защищают от рывков). "
            "Двойной клик — добавить точку, правый клик — удалить."
        )
        hint.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(hint)

        return w

    def _build_page_manual(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        t_lbl = QLabel("Ручная установка фиксированных оборотов (RPM)")
        t_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #f8fafc;")
        c_layout.addWidget(t_lbl)

        slider_box = QHBoxLayout()
        slider_box.setSpacing(14)

        init_manual = max(2000, min(7500, int(self.config.get("manual_rpm", 3600))))

        self.slider_manual_rpm = QSlider(Qt.Horizontal)
        self.slider_manual_rpm.setRange(2000, 7500)
        self.slider_manual_rpm.setSingleStep(50)
        self.slider_manual_rpm.setPageStep(200)
        self.slider_manual_rpm.setValue(init_manual)

        self.spin_manual_rpm = QSpinBox()
        self.spin_manual_rpm.setRange(2000, 7500)
        self.spin_manual_rpm.setSingleStep(50)
        self.spin_manual_rpm.setValue(init_manual)
        self.spin_manual_rpm.setSuffix(" RPM")
        self.spin_manual_rpm.setFixedWidth(130)

        self.slider_manual_rpm.valueChanged.connect(self.on_manual_slider_changed)
        self.spin_manual_rpm.valueChanged.connect(self.on_manual_spin_changed)

        slider_box.addWidget(self.slider_manual_rpm, 1)
        slider_box.addWidget(self.spin_manual_rpm)
        c_layout.addLayout(slider_box)

        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(8)
        quick_layout.addWidget(QLabel("Быстрый выбор:"))
        for r in [2100, 2600, 3200, 3800, 4500, 5500, 6500, 7500]:
            b = QPushButton(f"{r} RPM")
            b.clicked.connect(lambda _, rpm=r: self.slider_manual_rpm.setValue(rpm))
            quick_layout.addWidget(b)
        quick_layout.addStretch()
        c_layout.addLayout(quick_layout)

        layout.addWidget(card)
        layout.addStretch()
        return w

    def _build_page_auto(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(10)

        t_lbl = QLabel("🍃 Штатный автоматический режим BIOS")
        t_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #10b981;")
        c_layout.addWidget(t_lbl)

        d_lbl = QLabel(
            "В этом режиме управление вентиляторами полностью передается встроенному контроллеру "
            "Lenovo Embedded Controller (EC / BIOS) без лишних цикличных перезаписей WMI."
        )
        d_lbl.setWordWrap(True)
        d_lbl.setStyleSheet("color: #94a3b8; font-size: 13px;")
        c_layout.addWidget(d_lbl)

        btn_activate = QPushButton("Подтвердить сброс на штатный BIOS")
        btn_activate.setObjectName("primaryBtn")
        btn_activate.setFixedWidth(260)
        btn_activate.clicked.connect(lambda: self._set_mode_from_ui(3))
        c_layout.addWidget(btn_activate)

        layout.addWidget(card)
        layout.addStretch()
        return w

    def _build_page_boost(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(10)

        t_lbl = QLabel("🚀 Режим максимального охлаждения (100% Турбо)")
        t_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #f43f5e;")
        c_layout.addWidget(t_lbl)

        d_lbl = QLabel(
            "Включает непрерывную продувку на физическом максимуме вентиляторов (~7300–7700 RPM). "
            "При возврате в другой режим обороты снижаются плавно без резкого удара."
        )
        d_lbl.setWordWrap(True)
        d_lbl.setStyleSheet("color: #cbd5e1; font-size: 13px;")
        c_layout.addWidget(d_lbl)

        self.lbl_boost_status = QLabel("Статус: 🚀 100% ТУРБО АКТИВЕН (Обороты максимальные ~7300–7700 RPM)")
        self.lbl_boost_status.setStyleSheet(BOOST_ACTIVE_STYLE)
        c_layout.addWidget(self.lbl_boost_status)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_boost = QPushButton("🔥 АКТИВИРОВАТЬ 100% ТУРБО")
        self.btn_boost.setObjectName("turboBtn")
        self.btn_boost.setFixedHeight(36)
        self.btn_boost.clicked.connect(lambda: self._set_mode_from_ui(4))
        btn_layout.addWidget(self.btn_boost)

        self.btn_boost_return_m65 = QPushButton("🎯 Вернуться к Авто-цели")
        self.btn_boost_return_m65.setFixedHeight(36)
        self.btn_boost_return_m65.setStyleSheet(
            "background-color: #0284c7; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 6px 14px;"
        )
        self.btn_boost_return_m65.clicked.connect(lambda: self._set_mode_from_ui(0))
        btn_layout.addWidget(self.btn_boost_return_m65)

        self.btn_boost_return_auto = QPushButton("🍃 Сброс на BIOS (Auto)")
        self.btn_boost_return_auto.setFixedHeight(36)
        self.btn_boost_return_auto.clicked.connect(lambda: self._set_mode_from_ui(3))
        btn_layout.addWidget(self.btn_boost_return_auto)

        btn_layout.addStretch()
        c_layout.addLayout(btn_layout)

        layout.addWidget(card)
        layout.addStretch()
        return w

    # ------------------ Dual Labeled HiDPI System Tray Icons (CPU + GPU) ------------------

    @staticmethod
    def _create_labeled_tray_icon(header_text: str, value_text: str, header_hex: str, body_hex: str) -> QIcon:
        """
        Renders a crisp 64x64 HiDPI tray badge with a distinct top label ('CPU' or 'GPU')
        and bold bottom value ('54°' or 'OFF') so CPU and GPU are unmistakable at a glance!
        """
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        # Full badge body
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(body_hex))
        painter.drawRoundedRect(1, 1, 62, 62, 10, 10)

        # Top header bar for 'CPU' / 'GPU'
        painter.setBrush(QColor(header_hex))
        painter.drawRoundedRect(1, 1, 62, 22, 10, 10)
        painter.fillRect(1, 13, 62, 10, QColor(header_hex))

        # Crisp top label ('CPU' or 'GPU')
        painter.setPen(QColor("#ffffff"))
        f_hdr = QFont("Segoe UI", 12, QFont.Black)
        f_hdr.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
        painter.setFont(f_hdr)
        painter.drawText(QRect(1, 0, 62, 22), Qt.AlignCenter, header_text)

        # Bottom temperature or 'OFF' value
        is_word = not any(ch.isdigit() for ch in value_text)
        f_val = QFont("Segoe UI", 15 if is_word else 20, QFont.Bold)
        painter.setFont(f_val)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRect(1, 22, 62, 40), Qt.AlignCenter, value_text)

        painter.end()
        return QIcon(pixmap)

    def _init_tray(self, app_icon):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self._base_icon = app_icon

        tray_menu = QMenu(self)
        action_show = QAction("Показать окно (Ядра CPU и GPU)", self)
        action_show.triggered.connect(self.show_and_raise)
        tray_menu.addAction(action_show)

        tray_menu.addSeparator()

        action_m65 = QAction("🎯 Авто-цель °C (Тихий ПИД)", self)
        action_m65.triggered.connect(lambda: self._set_mode_from_ui(0))
        tray_menu.addAction(action_m65)

        action_m_curve = QAction("📈 Кривая вентиляторов", self)
        action_m_curve.triggered.connect(lambda: self._set_mode_from_ui(1))
        tray_menu.addAction(action_m_curve)

        action_m_manual = QAction("🎛️ Ручной режим (RPM)", self)
        action_m_manual.triggered.connect(lambda: self._set_mode_from_ui(2))
        tray_menu.addAction(action_m_manual)

        action_m_auto = QAction("🍃 Штатный BIOS (Auto)", self)
        action_m_auto.triggered.connect(lambda: self._set_mode_from_ui(3))
        tray_menu.addAction(action_m_auto)

        action_m_boost = QAction("🚀 100% Турбо (Boost)", self)
        action_m_boost.triggered.connect(lambda: self._set_mode_from_ui(4))
        tray_menu.addAction(action_m_boost)

        tray_menu.addSeparator()

        action_exit = QAction("Выход (Сброс на BIOS)", self)
        action_exit.triggered.connect(self.safe_exit)
        tray_menu.addAction(action_exit)

        self._tray_menu = tray_menu

        # 1. Dedicated CPU Tray Badge
        init_cpu_icon = self._create_labeled_tray_icon("CPU", "--", "#0284c7", "#1e293b")
        self.tray_cpu = QSystemTrayIcon(init_cpu_icon, self)
        self.tray_cpu.setToolTip("CPU — ThinkBook Fan Control")
        self.tray_cpu.setContextMenu(tray_menu)
        self.tray_cpu.activated.connect(self.on_tray_activated)
        self.tray_cpu.show()

        # Alias self.tray -> self.tray_cpu for backward compatibility
        self.tray = self.tray_cpu

        # 2. Dedicated GPU Tray Badge
        init_gpu_icon = self._create_labeled_tray_icon("GPU", "OFF", "#059669", "#064e3b")
        self.tray_gpu = QSystemTrayIcon(init_gpu_icon, self)
        self.tray_gpu.setToolTip("GPU — ThinkBook Fan Control")
        self.tray_gpu.setContextMenu(tray_menu)
        self.tray_gpu.activated.connect(self.on_tray_activated)
        self.tray_gpu.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show_and_raise()

    def show_and_raise(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    # ------------------ Slots & Event Handlers ------------------

    def _update_mode_badge(self, mode: str):
        tgt = int(self.spin_target_temp.value()) if hasattr(self, "spin_target_temp") else int(self.config.get("target_temp", 65))
        badge_texts = {
            "target_65": f"⚡ РЕЖИМ: ЦЕЛЕВАЯ {tgt}°C",
            "curve": "📈 РЕЖИМ: КРИВАЯ",
            "manual": "🎛️ РЕЖИМ: РУЧНОЙ",
            "auto": "🍃 РЕЖИМ: BIOS (AUTO)",
            "boost": "🚀 РЕЖИМ: 100% ТУРБО",
        }
        badge_colors = {
            "target_65": "#0284c7",
            "curve": "#6366f1",
            "manual": "#8b5cf6",
            "auto": "#10b981",
            "boost": "#ef4444",
        }
        self.lbl_mode_badge.setText(badge_texts.get(mode, "РЕЖИМ"))
        self.lbl_mode_badge.setStyleSheet(
            f"background-color: {badge_colors.get(mode, '#0284c7')}; color: #ffffff; "
            f"font-weight: bold; padding: 6px 14px; border-radius: 12px; font-size: 12px;"
        )

    @Slot(int)
    def on_mode_changed(self, mode_id: int, persist: bool = True):
        modes = {0: "target_65", 1: "curve", 2: "manual", 3: "auto", 4: "boost"}
        mode = modes.get(mode_id, "target_65")
        self.stack.setCurrentIndex(mode_id)
        self.controller.set_mode(mode, persist=persist)
        self._update_mode_badge(mode)

        if hasattr(self, "lbl_boost_status"):
            if mode == "boost":
                self.lbl_boost_status.setText("Статус: 🚀 100% ТУРБО АКТИВЕН (Обороты максимальные ~7300–7700 RPM)")
                self.lbl_boost_status.setStyleSheet(BOOST_ACTIVE_STYLE)
            else:
                self.lbl_boost_status.setText("Статус: ожидает активации (нажмите кнопку ниже или переключите вкладку)")
                self.lbl_boost_status.setStyleSheet(BOOST_IDLE_STYLE)

    def toggle_turbo(self):
        cur_mode = self.controller._current_mode
        self._set_mode_from_ui(0 if cur_mode == "boost" else 4)

    def _set_mode_from_ui(self, mode_id: int, persist: bool = True):
        btn = self.btn_group_modes.button(mode_id)
        if btn:
            with QSignalBlocker(self.btn_group_modes):
                btn.setChecked(True)
        self.on_mode_changed(mode_id, persist=persist)

    def _on_autostart_toggled(self, checked: bool):
        ok = self.config.set_autostart(bool(checked))
        if not ok and checked:
            with QSignalBlocker(self.chk_autostart):
                self.chk_autostart.setChecked(False)

    @Slot(int)
    def on_target_temp_changed(self, temp: int):
        self.controller.set_target_temp(temp, persist=False)
        if hasattr(self, "curve_widget"):
            self.curve_widget.set_target_indicator(float(temp))
        if self.controller._current_mode == "target_65":
            self._update_mode_badge("target_65")
        self._save_debounce_timer.start()

    @Slot(int)
    def on_manual_slider_changed(self, rpm: int):
        with QSignalBlocker(self.spin_manual_rpm):
            self.spin_manual_rpm.setValue(rpm)
        self.controller.set_manual_rpm(rpm, persist=False)
        self._save_debounce_timer.start()

    @Slot(int)
    def on_manual_spin_changed(self, rpm: int):
        with QSignalBlocker(self.slider_manual_rpm):
            self.slider_manual_rpm.setValue(rpm)
        self.controller.set_manual_rpm(rpm, persist=False)
        self._save_debounce_timer.start()

    @Slot(list)
    def on_curve_modified(self, points: list):
        self.controller.set_curve_points(points, persist=False)
        self._save_debounce_timer.start()

    def apply_curve_preset(self, points: list):
        self.curve_widget.set_points(points)
        self.controller.set_curve_points(self.curve_widget.get_points(), persist=True)

    def add_curve_point(self):
        self.curve_widget.add_point_smart()

    @staticmethod
    def _temp_color(t: float | None) -> str:
        if t is None:
            return "#94a3b8"
        if t < 60:
            return "#10b981"
        if t < 75:
            return "#38bdf8"
        if t < 85:
            return "#f59e0b"
        return "#ef4444"

    @Slot(dict)
    def on_telemetry_updated(self, data: dict):
        dgpu_state = data.get("dgpu_state", "D0_active")
        gpu_t = data.get("gpu_temp")
        cpu_t = data.get("cpu_temp")
        cpu_det = data.get("cpu_details") or {}
        f1 = data.get("fan1_rpm")
        f2 = data.get("fan2_rpm")
        pwr = data.get("gpu_pwr")
        util = data.get("gpu_util")
        mem_u = data.get("mem_util")
        tgt_rpm = data.get("target_rpm", 0)
        wmi_ok = data.get("wmi_ok", True)
        safety_lvl = data.get("safety_level", 0)
        desc = data.get("mode_desc", "")

        tgt_temp = int(self.spin_target_temp.value()) if hasattr(self, "spin_target_temp") else 65

        cpu_avg = cpu_det.get("avg_temp")
        cpu_max = cpu_det.get("max_temp")
        cpu_pwr = cpu_det.get("power_w")
        cpu_load = cpu_det.get("total_load")
        cores = cpu_det.get("cores") or []

        # Representative CPU display temp (Core Average preferred, fallback to EC)
        disp_cpu_val = cpu_avg if cpu_avg is not None else (float(cpu_t) if cpu_t is not None else None)

        # ---- 1. Top Summary Cards ----
        if disp_cpu_val is not None:
            c_col = self._temp_color(disp_cpu_val)
            self.lbl_cpu_temp.setText(f"{disp_cpu_val:.1f} °C")
            self.lbl_cpu_temp.setStyleSheet(f"color: {c_col};")
            pk_s = f"{cpu_max:.0f}°C" if cpu_max is not None else "--"
            ec_s = f"{cpu_t}°C" if cpu_t is not None else "--"
            self.lbl_cpu_sub.setText(f"Пик: {pk_s} • EC: {ec_s}")
        else:
            self.lbl_cpu_temp.setText("-- °C")
            if not wmi_ok:
                self.lbl_cpu_sub.setText("Нет доступа WMI (Admin)")

        cpu_pwr_str = f"CPU: {cpu_pwr:.1f} W" if cpu_pwr is not None else (
            f"CPU Load: {cpu_load:.0f}%" if cpu_load is not None else "CPU: --"
        )

        if dgpu_state == "D3cold":
            self.lbl_gpu_temp.setText("ОТКЛ.")
            self.lbl_gpu_temp.setStyleSheet("color: #10b981;")
            self.lbl_gpu_sub.setText("Сон D3cold • iGPU")
            self.lbl_gpu_pwr.setText("0.0 W")
            self.lbl_gpu_pwr.setStyleSheet("color: #10b981;")
            self.lbl_gpu_pwr_sub.setText(f"GPU Сон • {cpu_pwr_str}")
        elif dgpu_state == "D0_idle":
            self.lbl_gpu_temp.setText("ПРОСТОЙ")
            self.lbl_gpu_temp.setStyleSheet("color: #38bdf8;")
            self.lbl_gpu_sub.setText("NVML закрыт (ждёт сна)")
            self.lbl_gpu_pwr.setText("~1 W")
            self.lbl_gpu_pwr.setStyleSheet("color: #94a3b8;")
            self.lbl_gpu_pwr_sub.setText(cpu_pwr_str)
        else:
            if gpu_t is not None:
                if gpu_t < tgt_temp - 5:
                    t_color = "#10b981"
                elif gpu_t <= tgt_temp + 1:
                    t_color = "#38bdf8"
                elif gpu_t <= 75:
                    t_color = "#f59e0b"
                else:
                    t_color = "#ef4444"
                self.lbl_gpu_temp.setText(f"{gpu_t} °C")
                self.lbl_gpu_temp.setStyleSheet(f"color: {t_color};")
                self.lbl_gpu_sub.setText(f"Load: {util if util is not None else '--'}%")
            else:
                self.lbl_gpu_temp.setText("-- °C")

            self.lbl_gpu_pwr.setStyleSheet("color: #f43f5e;")
            self.lbl_gpu_pwr_sub.setText(cpu_pwr_str)
            self.lbl_gpu_pwr.setText(f"{pwr:.1f} W" if pwr is not None else "-- W")

        self.lbl_fan1_rpm.setText(f"{f1 if f1 is not None else '--'} RPM")
        self.lbl_fan1_sub.setText(f"Target: {tgt_rpm} RPM" if tgt_rpm else "Target: BIOS Auto")

        self.lbl_fan2_rpm.setText(f"{f2 if f2 is not None else '--'} RPM")
        self.lbl_fan2_sub.setText(f"Target: {tgt_rpm} RPM" if tgt_rpm else "Target: BIOS Auto")

        # ---- 2. Update 16-Core CPU Matrix & GPU Details Panel ----
        if disp_cpu_val is not None:
            self.lbl_cpu_avg_pill.setText(f"Средняя по процессору: {disp_cpu_val:.1f} °C")
        if cpu_max is not None:
            self.lbl_cpu_max_pill.setText(f"Пик ядра: {cpu_max:.1f} °C")
        if cpu_load is not None:
            pw_part = f" • {cpu_pwr:.1f} W" if cpu_pwr is not None else ""
            self.lbl_cpu_load_pill.setText(f"Загрузка: {cpu_load:.1f}%{pw_part}")

        kind_colors = {"P": "#38bdf8", "E": "#818cf8", "LP": "#34d399"}
        for idx, tile_w in enumerate(self._core_tile_widgets):
            if idx < len(cores):
                c_info = cores[idx]
                tile_w["frame"].setVisible(True)
                lbl_k = c_info.get("label", f"C{idx+1}")
                k_type = c_info.get("kind", "E")
                c_t = c_info.get("temp")
                c_l = float(c_info.get("load") or 0.0)
                c_mhz = int(c_info.get("mhz") or 0)

                tile_w["name"].setText(lbl_k)
                tile_w["name"].setStyleSheet(
                    f"color: {kind_colors.get(k_type, '#94a3b8')}; font-size: 10px; font-weight: bold;"
                )
                tile_w["temp"].setText(f"{c_t:.0f}°C" if c_t is not None else "--°C")
                tile_w["temp"].setStyleSheet(
                    f"color: {self._temp_color(c_t)}; font-size: 12px; font-weight: bold;"
                )
                ghz = c_mhz / 1000.0 if c_mhz else 0.0
                tile_w["meta"].setText(f"{c_l:.0f}% • {ghz:.1f} ГГц" if ghz > 0 else f"{c_l:.0f}%")
                tile_w["bar"].setValue(int(round(max(0.0, min(100.0, c_l)))))
            else:
                tile_w["frame"].setVisible(True)

        # GPU detail panel right side
        if dgpu_state == "D3cold":
            self.lbl_gpu_state_pill.setText("🟢 Сон D3cold (0 Вт)")
            self.lbl_gpu_state_pill.setStyleSheet(
                "background-color: #064e3b; color: #34d399; font-size: 10px; font-weight: bold; padding: 2px 7px; border-radius: 7px;"
            )
            self.lbl_gd_temp.setText("ОТКЛ. (Сон)")
            self.lbl_gd_temp.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold;")
            self.lbl_gd_pwr.setText("0.0 W (Обесточена)")
            self.lbl_gd_util.setText("0% / 0%")
        elif dgpu_state == "D0_idle":
            self.lbl_gpu_state_pill.setText("💤 Простой (ждёт D3)")
            self.lbl_gpu_state_pill.setStyleSheet(
                "background-color: #1e293b; color: #38bdf8; font-size: 10px; font-weight: bold; padding: 2px 7px; border-radius: 7px;"
            )
            self.lbl_gd_temp.setText("Простой (NVML откр. отпущен)")
            self.lbl_gd_temp.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold;")
            self.lbl_gd_pwr.setText("~1.0 W")
            self.lbl_gd_util.setText("0% / 0%")
        else:
            self.lbl_gpu_state_pill.setText("⚡ RTX 4060 Активна")
            self.lbl_gpu_state_pill.setStyleSheet(
                "background-color: #3b0764; color: #e879f9; font-size: 10px; font-weight: bold; padding: 2px 7px; border-radius: 7px;"
            )
            self.lbl_gd_temp.setText(f"{gpu_t} °C" if gpu_t is not None else "-- °C")
            self.lbl_gd_temp.setStyleSheet(
                f"color: {self._temp_color(gpu_t)}; font-size: 11px; font-weight: bold;"
            )
            self.lbl_gd_pwr.setText(f"{pwr:.1f} W" if pwr is not None else "-- W")
            u_s = f"{util}%" if util is not None else "--%"
            m_s = f"{mem_u}%" if mem_u is not None else "--%"
            self.lbl_gd_util.setText(f"{u_s} / {m_s}")

        # ---- 3. Mode Status Banner ----
        if data.get("mode") == "target_65":
            if not wmi_ok or safety_lvl > 0:
                fg, bg = "#f43f5e", "#2a1215"
            else:
                fg, bg = "#10b981", "#1e293b"
            self.lbl_target65_status.setText(f"Регулятор активен ➔ {desc}" if wmi_ok else desc)
            self.lbl_target65_status.setStyleSheet(
                f"background-color: {bg}; color: {fg}; font-weight: bold; "
                f"padding: 8px 12px; border-radius: 6px; font-size: 12px;"
            )
        elif data.get("mode") == "boost" and hasattr(self, "lbl_boost_status"):
            f1_str = f"{f1} RPM" if f1 is not None else "--"
            f2_str = f"{f2} RPM" if f2 is not None else "--"
            self.lbl_boost_status.setText(
                f"Статус: 🚀 100% ТУРБО АКТИВЕН ➔ Fan 1: {f1_str} | Fan 2: {f2_str} (ФИЗИЧЕСКИЙ МАКСИМУМ)"
            )
            self.lbl_boost_status.setStyleSheet(BOOST_ACTIVE_STYLE)

        curve_ref = data.get("curve_ref_temp")
        live_rpm = f2 if (f2 is not None and f2 > 0) else (f1 if (f1 is not None and f1 > 0) else tgt_rpm)
        self.curve_widget.set_live_point(curve_ref if curve_ref is not None else gpu_t, live_rpm)

        # ---- 4. Update Dual Labeled System Tray Badges ([CPU] + [GPU]) ----
        f_str = f"Кулеры: {f1 if f1 is not None else '--'} / {f2 if f2 is not None else '--'} RPM"

        if hasattr(self, "tray_cpu") and self.tray_cpu.isVisible():
            if disp_cpu_val is not None:
                c_int = int(round(disp_cpu_val))
                if c_int < 60:
                    c_body = "#0f766e"
                elif c_int < 75:
                    c_body = "#1e3a8a"
                elif c_int < 85:
                    c_body = "#b45309"
                else:
                    c_body = "#b91c1c"
                self.tray_cpu.setIcon(self._create_labeled_tray_icon("CPU", str(c_int), "#0284c7", c_body))
            pk_tip = f"{cpu_max:.1f}°C" if cpu_max is not None else "--"
            avg_tip = f"{disp_cpu_val:.1f}°C" if disp_cpu_val is not None else "--"
            ec_tip = f"{cpu_t}°C" if cpu_t is not None else "--"
            self.tray_cpu.setToolTip(
                f"ПРОЦЕССОР (CPU Core Ultra 7 155H)\n"
                f"Средняя: {avg_tip} | Пик: {pk_tip} | EC: {ec_tip}\n"
                f"{f_str}\n{desc}"
            )

        if hasattr(self, "tray_gpu") and self.tray_gpu.isVisible():
            if dgpu_state == "D3cold":
                self.tray_gpu.setIcon(self._create_labeled_tray_icon("GPU", "OFF", "#059669", "#064e3b"))
                g_tip = "Статус: ОТКЛ. (Сон D3cold, 0.0 Вт • активна Intel Arc)"
            elif dgpu_state == "D0_idle":
                self.tray_gpu.setIcon(self._create_labeled_tray_icon("GPU", "IDLE", "#0284c7", "#334155"))
                g_tip = "Статус: Простой (NVML закрыт, переход в сон)"
            elif gpu_t is not None:
                if gpu_t < tgt_temp - 4:
                    g_body = "#065f46"
                elif gpu_t <= tgt_temp + 1:
                    g_body = "#1e3a8a"
                elif gpu_t <= 75:
                    g_body = "#b45309"
                else:
                    g_body = "#b91c1c"
                self.tray_gpu.setIcon(self._create_labeled_tray_icon("GPU", str(gpu_t), "#7c3aed", g_body))
                g_tip = f"Температура: {gpu_t}°C | Питание: {pwr if pwr is not None else '--'} Вт | Нагрузка: {util if util is not None else '--'}%"
            else:
                self.tray_gpu.setIcon(self._create_labeled_tray_icon("GPU", "--", "#475569", "#1e293b"))
                g_tip = "Статус: чтение..."

            self.tray_gpu.setToolTip(
                f"ВИДЕОКАРТА (NVIDIA RTX 4060 Laptop)\n{g_tip}\n{f_str}"
            )

    def closeEvent(self, event):
        if self._save_debounce_timer.isActive():
            self._save_debounce_timer.stop()
            self.config.save()

        if self.chk_min_to_tray.isChecked():
            event.ignore()
            self.hide()
            if hasattr(self, "tray_cpu") and self.tray_cpu.isVisible():
                self.tray_cpu.showMessage(
                    "ThinkBook Fan Control",
                    "Свернуто в трей: иконки [CPU] и [GPU] показывают температуры раздельно.",
                    QSystemTrayIcon.Information,
                    2000,
                )
        else:
            event.accept()
            self.safe_exit()

    def safe_exit(self):
        """Safely flushes config, stops controller (restoring BIOS auto), and exits."""
        if self._save_debounce_timer.isActive():
            self._save_debounce_timer.stop()
            self.config.save()
        if hasattr(self, "tray_cpu"):
            self.tray_cpu.hide()
        if hasattr(self, "tray_gpu"):
            self.tray_gpu.hide()
        self.controller.stop()
        app = QApplication.instance()
        if app is not None:
            app.setProperty("_clean_fan_shutdown", True)
            app.quit()
