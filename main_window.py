"""
Main GUI Window for Lenovo ThinkBook Fan Control
PySide6 Implementation with modern dark styling, real-time telemetry,
interactive curve editor, manual RPM control, and dedicated 65°C mining mode.
"""

import os
import sys
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
    QDoubleSpinBox,
    QComboBox,
    QStackedWidget,
    QFrame,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSystemTrayIcon,
    QMenu,
    QMessageBox,
    QApplication,
)
from PySide6.QtGui import QIcon, QFont, QColor, QAction, QPixmap, QPainter
from PySide6.QtCore import Qt, Slot

from config import ConfigManager, PRESETS, get_app_dir
from controller import FanControllerThread
from curve_widget import InteractiveCurveWidget

APP_DIR = get_app_dir()
ICON_PATH = os.path.join(APP_DIR, "app_icon.ico")
PNG_ICON_PATH = os.path.join(APP_DIR, "app_icon.png")


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
    padding: 12px;
}

QFrame.card:hover {
    border-color: #3b4256;
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
    font-size: 26px;
    font-weight: bold;
}

/* Mode Bar Radio Buttons styled as tabs/buttons */
QRadioButton {
    background-color: #1e2230;
    color: #94a3b8;
    border: 1px solid #2d3348;
    border-radius: 8px;
    padding: 8px 14px;
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
    padding: 7px 14px;
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

QSpinBox, QDoubleSpinBox {
    background-color: #1e2230;
    border: 1px solid #333a4f;
    border-radius: 6px;
    color: #ffffff;
    padding: 4px 8px;
    font-size: 13px;
    font-weight: bold;
}

QTableWidget {
    background-color: #161922;
    border: 1px solid #282d3d;
    gridline-color: #242938;
    border-radius: 6px;
    color: #e2e8f0;
}

QHeaderView::section {
    background-color: #1e2230;
    color: #94a3b8;
    padding: 4px;
    border: 1px solid #282d3d;
    font-size: 11px;
    font-weight: 600;
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
        self.setWindowTitle("ThinkBook Fan Control — RTX 4060 & CPU")
        self.resize(920, 720)
        self.setMinimumSize(840, 640)

        app_icon = QIcon(ICON_PATH if os.path.exists(ICON_PATH) else PNG_ICON_PATH)
        self.setWindowIcon(app_icon)
        self.setStyleSheet(DARK_STYLESHEET)

        # Background controller thread
        self.controller = FanControllerThread(self.config, self)
        self.controller.telemetry_updated.connect(self.on_telemetry_updated)

        self._init_ui()
        self._init_tray(app_icon)

        # Start controller thread
        self.controller.start()

    def _init_ui(self):
        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(18, 14, 18, 14)
        main_layout.setSpacing(12)

        # 1. Header Bar
        header_layout = QHBoxLayout()
        header_info = QVBoxLayout()
        header_info.setSpacing(2)

        title = QLabel("ThinkBook Fan Control (Lenovo 14 G6+ IMH)")
        title.setObjectName("titleLabel")
        subtitle = QLabel("Meteor Lake Core Ultra • NVIDIA RTX 4060 Laptop GPU • Direct EC WMI Control")
        subtitle.setObjectName("subtitleLabel")
        header_info.addWidget(title)
        header_info.addWidget(subtitle)
        header_layout.addLayout(header_info)

        header_layout.addStretch()

        self.lbl_mode_badge = QLabel("⚡ РЕЖИМ: ЦЕЛЕВАЯ 65°C")
        self.lbl_mode_badge.setStyleSheet(
            "background-color: #0284c7; color: #ffffff; font-weight: bold; "
            "padding: 6px 14px; border-radius: 12px; font-size: 12px;"
        )
        header_layout.addWidget(self.lbl_mode_badge)
        main_layout.addLayout(header_layout)

        # 2. Telemetry Cards (5 horizontal cards)
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(10)

        # Card 1: GPU Temp
        card1, self.lbl_gpu_temp, self.lbl_gpu_sub = self._create_card("GPU TEMP", "-- °C", "#10b981", "Load: --%")
        cards_layout.addWidget(card1)

        # Card 2: CPU Temp
        card2, self.lbl_cpu_temp, self.lbl_cpu_sub = self._create_card("CPU TEMP", "-- °C", "#38bdf8", "WMI Sensor")
        cards_layout.addWidget(card2)

        # Card 3: Fan 1 Speed
        card3, self.lbl_fan1_rpm, self.lbl_fan1_sub = self._create_card("FAN 1 (CPU)", "-- RPM", "#818cf8", "Target: --")
        cards_layout.addWidget(card3)

        # Card 4: Fan 2 Speed
        card4, self.lbl_fan2_rpm, self.lbl_fan2_sub = self._create_card("FAN 2 (GPU)", "-- RPM", "#a78bfa", "Target: --")
        cards_layout.addWidget(card4)

        # Card 5: GPU Power
        card5, self.lbl_gpu_pwr, self.lbl_gpu_pwr_sub = self._create_card("GPU POWER", "-- W", "#f43f5e", "NVML Telemetry")
        cards_layout.addWidget(card5)

        main_layout.addLayout(cards_layout)

        # 3. Mode Selection Button Group
        mode_box = QHBoxLayout()
        mode_box.setSpacing(8)

        self.btn_group_modes = QButtonGroup(self)

        self.rb_target65 = QRadioButton("⚡ Целевая 65°C (Майнинг)")
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

        # 4. Stacked Pages for Modes
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

        # Connect mode switching
        self.btn_group_modes.idClicked.connect(self.on_mode_changed)

        # Initial active mode
        cur_mode = self.config.get("mode", "target_65")
        mode_map = {"target_65": 0, "curve": 1, "manual": 2, "auto": 3, "boost": 4}
        idx = mode_map.get(cur_mode, 0)
        self._set_mode_from_ui(idx)

        # 5. Bottom Toolbar
        bottom_layout = QHBoxLayout()
        self.chk_min_to_tray = QCheckBox("В трей при закрытии")
        self.chk_min_to_tray.setChecked(bool(self.config.get("minimize_to_tray_on_close", True)))
        self.chk_min_to_tray.toggled.connect(
            lambda v: self.config.set("minimize_to_tray_on_close", bool(v))
        )
        bottom_layout.addWidget(self.chk_min_to_tray)

        self.chk_autostart = QCheckBox("Автозапуск с Windows")
        self.chk_autostart.setChecked(self.config.is_autostart_enabled())
        self.chk_autostart.toggled.connect(
            lambda v: self.config.set_autostart(bool(v))
        )
        bottom_layout.addWidget(self.chk_autostart)

        self.chk_safety = QCheckBox("Защита от перегрева (>80°C)")
        self.chk_safety.setChecked(bool(self.config.get("safety_override", True)))
        self.chk_safety.toggled.connect(
            lambda v: self.config.set("safety_override", bool(v))
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
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

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

    # ------------------ Mode Pages ------------------

    def _build_page_target65(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(14)

        # Banner card
        banner = QFrame()
        banner.setProperty("class", "card")
        b_layout = QVBoxLayout(banner)
        b_layout.setSpacing(6)

        b_title = QLabel("⚡ Автоматическое поддержание целевой температуры (Майнинг)")
        b_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #38bdf8;")
        b_desc = QLabel(
            "Регулятор непрерывно контролирует нагрев RTX 4060 под нагрузкой OneZeroMiner и "
            "динамически подстраивает обороты вентиляторов около ~4150 RPM для стабилизации ровно на выбранной температуре."
        )
        b_desc.setWordWrap(True)
        b_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")

        b_layout.addWidget(b_title)
        b_layout.addWidget(b_desc)
        layout.addWidget(banner)

        # Controls card
        ctrl_card = QFrame()
        ctrl_card.setProperty("class", "card")
        c_layout = QGridLayout(ctrl_card)
        c_layout.setSpacing(14)

        lbl_target = QLabel("Целевая температура GPU:")
        lbl_target.setStyleSheet("font-size: 13px; font-weight: bold; color: #f1f5f9;")

        self.spin_target_temp = QSpinBox()
        self.spin_target_temp.setRange(50, 80)
        self.spin_target_temp.setValue(int(self.config.get("target_temp", 65)))
        self.spin_target_temp.setSuffix(" °C")
        self.spin_target_temp.setFixedWidth(110)
        self.spin_target_temp.valueChanged.connect(self.on_target_temp_changed)

        c_layout.addWidget(lbl_target, 0, 0)
        c_layout.addWidget(self.spin_target_temp, 0, 1)

        # Quick preset buttons
        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(8)
        for t in [60, 65, 68, 70]:
            btn = QPushButton(f"{t}°C {'(Оптимум)' if t == 65 else ''}")
            btn.clicked.connect(lambda _, temp=t: self.spin_target_temp.setValue(temp))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()

        c_layout.addWidget(QLabel("Быстрые пресеты:"), 1, 0)
        c_layout.addLayout(quick_layout, 1, 1)

        # Live status message
        self.lbl_target65_status = QLabel("Статус регулятора: активен (цель 65°C)")
        self.lbl_target65_status.setStyleSheet(
            "background-color: #1e293b; color: #10b981; font-weight: bold; "
            "padding: 10px 14px; border-radius: 6px; font-size: 13px;"
        )
        c_layout.addWidget(self.lbl_target65_status, 2, 0, 1, 2)

        layout.addWidget(ctrl_card)
        layout.addStretch()
        return w

    def _build_page_curve(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        # Sensor selection row
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
            lambda: self.config.set("curve_sensor", self.combo_curve_sensor.currentData())
        )
        sensor_box.addWidget(self.combo_curve_sensor)
        sensor_box.addStretch()
        layout.addLayout(sensor_box)

        # Interactive Graph
        self.curve_widget = InteractiveCurveWidget(self)
        self.curve_widget.set_points(self.config.get("curve_points"))
        self.curve_widget.curve_changed.connect(self.on_curve_modified)
        layout.addWidget(self.curve_widget, 1)

        # Graph toolbar
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
        btn_reset_def.clicked.connect(lambda: self.apply_curve_preset(self.config.get("curve_points")))
        tb_layout.addWidget(btn_reset_def)

        layout.addLayout(tb_layout)

        hint = QLabel("💡 Совет: перетаскивайте контрольные точки на графике мышкой. Двойной клик — добавить точку, правый клик — удалить.")
        hint.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(hint)

        return w

    def _build_page_manual(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(14)

        t_lbl = QLabel("Ручная установка фиксированных оборотов (RPM)")
        t_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #f8fafc;")
        c_layout.addWidget(t_lbl)

        # Slider + Value box
        slider_box = QHBoxLayout()
        slider_box.setSpacing(14)

        self.slider_manual_rpm = QSlider(Qt.Horizontal)
        self.slider_manual_rpm.setRange(2400, 7500)
        self.slider_manual_rpm.setSingleStep(50)
        self.slider_manual_rpm.setPageStep(200)
        self.slider_manual_rpm.setValue(int(self.config.get("manual_rpm", 4150)))

        self.spin_manual_rpm = QSpinBox()
        self.spin_manual_rpm.setRange(2400, 7500)
        self.spin_manual_rpm.setSingleStep(50)
        self.spin_manual_rpm.setValue(self.slider_manual_rpm.value())
        self.spin_manual_rpm.setSuffix(" RPM")
        self.spin_manual_rpm.setFixedWidth(130)

        self.slider_manual_rpm.valueChanged.connect(self.spin_manual_rpm.setValue)
        self.spin_manual_rpm.valueChanged.connect(self.slider_manual_rpm.setValue)
        self.slider_manual_rpm.valueChanged.connect(self.on_manual_rpm_changed)

        slider_box.addWidget(self.slider_manual_rpm, 1)
        slider_box.addWidget(self.spin_manual_rpm)
        c_layout.addLayout(slider_box)

        # Quick preset buttons
        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(8)
        quick_layout.addWidget(QLabel("Быстрый выбор:"))
        for r in [2500, 3000, 3600, 4200, 5000, 6000, 7000, 7500]:
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
        layout.setSpacing(14)

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(12)

        t_lbl = QLabel("🍃 Штатный автоматический режим BIOS")
        t_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #10b981;")
        c_layout.addWidget(t_lbl)

        d_lbl = QLabel(
            "В этом режиме управление вентиляторами передается стандартной прошивке "
            "Lenovo Embedded Controller (EC / BIOS). Все программные переопределения отключены."
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
        layout.setSpacing(14)

        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(14)

        t_lbl = QLabel("🚀 Режим максимального охлаждения (100% Турбо)")
        t_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #f43f5e;")
        c_layout.addWidget(t_lbl)

        d_lbl = QLabel(
            "Включает непрерывную продувку на физическом максимуме вентиляторов (~7300–7700 RPM). "
            "Контроллер циклично удерживает максимальный ШИМ-сигнал EC без затухания и просадок. "
            "Идеально для мгновенного сброса температур или майнинга в жарком помещении."
        )
        d_lbl.setWordWrap(True)
        d_lbl.setStyleSheet("color: #cbd5e1; font-size: 13px; line-height: 1.4;")
        c_layout.addWidget(d_lbl)

        self.lbl_boost_status = QLabel("Статус: 🚀 100% ТУРБО АКТИВЕН (Обороты максимальные ~7300–7700 RPM)")
        self.lbl_boost_status.setStyleSheet(
            "background-color: #2a1215; color: #f43f5e; font-size: 13px; "
            "font-weight: bold; padding: 10px 14px; border-radius: 6px; border: 1px solid #7f1d1d;"
        )
        c_layout.addWidget(self.lbl_boost_status)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_boost = QPushButton("🔥 АКТИВИРОВАТЬ 100% ТУРБО")
        self.btn_boost.setObjectName("turboBtn")
        self.btn_boost.setFixedHeight(38)
        self.btn_boost.clicked.connect(lambda: self._set_mode_from_ui(4))
        btn_layout.addWidget(self.btn_boost)

        self.btn_boost_return_m65 = QPushButton("⚡ Вернуться к Целевой 65°C")
        self.btn_boost_return_m65.setFixedHeight(38)
        self.btn_boost_return_m65.setStyleSheet(
            "background-color: #0284c7; color: #ffffff; font-weight: bold; border-radius: 6px; padding: 6px 14px;"
        )
        self.btn_boost_return_m65.clicked.connect(lambda: self._set_mode_from_ui(0))
        btn_layout.addWidget(self.btn_boost_return_m65)

        self.btn_boost_return_auto = QPushButton("🍃 Сброс на BIOS (Auto)")
        self.btn_boost_return_auto.setFixedHeight(38)
        self.btn_boost_return_auto.clicked.connect(lambda: self._set_mode_from_ui(3))
        btn_layout.addWidget(self.btn_boost_return_auto)

        btn_layout.addStretch()
        c_layout.addLayout(btn_layout)

        layout.addWidget(card)
        layout.addStretch()
        return w

    # ------------------ System Tray ------------------

    def _create_tray_icon(self, temp: int | None) -> QIcon:
        """Renders current GPU temperature dynamically on a tray icon."""
        if temp is None:
            return self._base_icon

        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background pill / badge
        if temp < 60:
            bg_color = QColor("#10b981")  # green
        elif temp <= 66:
            bg_color = QColor("#0284c7")  # cyan / optimum target
        elif temp <= 74:
            bg_color = QColor("#f59e0b")  # amber
        else:
            bg_color = QColor("#ef4444")  # red

        painter.setBrush(bg_color)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(1, 1, 30, 30, 6, 6)

        # Draw temperature number
        painter.setPen(QColor("#ffffff"))
        font = QFont("Segoe UI", 15, QFont.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, str(temp))
        painter.end()

        return QIcon(pixmap)

    def _init_tray(self, app_icon):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self._base_icon = app_icon
        self.tray = QSystemTrayIcon(self._base_icon, self)
        self.tray.setToolTip("ThinkBook Fan Control")

        tray_menu = QMenu(self)
        action_show = QAction("Показать окно", self)
        action_show.triggered.connect(self.show_and_raise)
        tray_menu.addAction(action_show)

        tray_menu.addSeparator()

        action_m65 = QAction("⚡ Целевая 65°C (Майнинг)", self)
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

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

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

    @Slot(int)
    def on_mode_changed(self, mode_id: int):
        modes = {0: "target_65", 1: "curve", 2: "manual", 3: "auto", 4: "boost"}
        mode = modes.get(mode_id, "target_65")
        self.stack.setCurrentIndex(mode_id)
        self.controller.set_mode(mode)

        badge_texts = {
            "target_65": "⚡ РЕЖИМ: ЦЕЛЕВАЯ 65°C",
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

        if hasattr(self, "lbl_boost_status"):
            if mode == "boost":
                self.lbl_boost_status.setText("Статус: 🚀 100% ТУРБО АКТИВЕН (Обороты максимальные ~7300–7700 RPM)")
                self.lbl_boost_status.setStyleSheet(
                    "background-color: #2a1215; color: #f43f5e; font-size: 13px; font-weight: bold; "
                    "padding: 10px 14px; border-radius: 6px; border: 1px solid #7f1d1d;"
                )
            else:
                self.lbl_boost_status.setText("Статус: ожидает активации (нажмите кнопку ниже или переключите вкладку)")
                self.lbl_boost_status.setStyleSheet(
                    "background-color: #1e2230; color: #94a3b8; font-size: 12px; font-weight: 600; "
                    "padding: 10px 14px; border-radius: 6px; border: 1px solid #2d3348;"
                )

    def toggle_turbo(self):
        """Toggles between Turbo mode and Target 65°C mining mode."""
        cur_mode = self.controller._current_mode
        if cur_mode == "boost":
            self._set_mode_from_ui(0)
        else:
            self._set_mode_from_ui(4)

    def _set_mode_from_ui(self, mode_id: int):
        btn = self.btn_group_modes.button(mode_id)
        if btn:
            btn.setChecked(True)
        self.on_mode_changed(mode_id)

    @Slot(int)
    def on_target_temp_changed(self, temp: int):
        self.controller.set_target_temp(temp)

    @Slot(int)
    def on_manual_rpm_changed(self, rpm: int):
        self.controller.set_manual_rpm(rpm)

    @Slot(list)
    def on_curve_modified(self, points: list):
        self.controller.set_curve_points(points)

    def apply_curve_preset(self, points: list):
        self.curve_widget.set_points(points)
        self.controller.set_curve_points(points)

    def add_curve_point(self):
        self.curve_widget.add_point_smart()

    @Slot(dict)
    def on_telemetry_updated(self, data: dict):
        gpu_t = data.get("gpu_temp")
        cpu_t = data.get("cpu_temp")
        f1 = data.get("fan1_rpm")
        f2 = data.get("fan2_rpm")
        pwr = data.get("gpu_pwr")
        util = data.get("gpu_util")
        tgt = data.get("target_rpm", 0)

        # GPU Temp color
        if gpu_t is not None:
            if gpu_t < 60:
                t_color = "#10b981"  # green
            elif gpu_t <= 66:
                t_color = "#38bdf8"  # cyan / ideal
            elif gpu_t <= 74:
                t_color = "#f59e0b"  # amber
            else:
                t_color = "#ef4444"  # red
            self.lbl_gpu_temp.setText(f"{gpu_t} °C")
            self.lbl_gpu_temp.setStyleSheet(f"color: {t_color};")
            self.lbl_gpu_sub.setText(f"Load: {util if util is not None else '--'}%")
        else:
            self.lbl_gpu_temp.setText("-- °C")

        # CPU Temp
        if cpu_t is not None:
            self.lbl_cpu_temp.setText(f"{cpu_t} °C")
        else:
            self.lbl_cpu_temp.setText("-- °C")

        # Fans
        self.lbl_fan1_rpm.setText(f"{f1 if f1 is not None else '--'} RPM")
        self.lbl_fan1_sub.setText(f"Target: {tgt} RPM" if tgt else "Target: Auto")

        self.lbl_fan2_rpm.setText(f"{f2 if f2 is not None else '--'} RPM")
        self.lbl_fan2_sub.setText(f"Target: {tgt} RPM" if tgt else "Target: Auto")

        # GPU Power
        if pwr is not None:
            self.lbl_gpu_pwr.setText(f"{pwr:.1f} W")
        else:
            self.lbl_gpu_pwr.setText("-- W")

        # Status text in Mining mode
        if data.get("mode") == "target_65":
            desc = data.get("mode_desc", "")
            self.lbl_target65_status.setText(f"Регулятор активен ➔ {desc}")
        elif data.get("mode") == "boost" and hasattr(self, "lbl_boost_status"):
            f1_str = f"{f1} RPM" if f1 else "--"
            f2_str = f"{f2} RPM" if f2 else "--"
            self.lbl_boost_status.setText(
                f"Статус: 🚀 100% ТУРБО АКТИВЕН ➔ Fan 1: {f1_str} | Fan 2: {f2_str} (ФИЗИЧЕСКИЙ МАКСИМУМ)"
            )
            self.lbl_boost_status.setStyleSheet("color: #f43f5e; font-size: 12px; font-weight: bold;")

        # Update curve live marker
        self.curve_widget.set_live_point(gpu_t, f2 or f1)

        # Update System Tray icon and tooltip
        if hasattr(self, "tray") and self.tray.isVisible():
            if gpu_t is not None:
                self.tray.setIcon(self._create_tray_icon(gpu_t))
            t_str = f"GPU: {gpu_t}°C" if gpu_t is not None else "GPU: --"
            c_str = f"CPU: {cpu_t}°C" if cpu_t is not None else "CPU: --"
            f_str = f"Fans: {f1 or '--'} / {f2 or '--'} RPM" if (f1 or f2) else "Fans: Auto"
            self.tray.setToolTip(f"ThinkBook Fan Control\n{t_str} | {c_str}\n{f_str}\n{data.get('mode_desc', '')}")

    def closeEvent(self, event):
        if self.chk_min_to_tray.isChecked():
            event.ignore()
            self.hide()
            if hasattr(self, "tray") and self.tray.isVisible():
                self.tray.showMessage(
                    "ThinkBook Fan Control",
                    "Приложение свернуто в системный трей и продолжает регулировать вентиляторы.",
                    QSystemTrayIcon.Information,
                    2000,
                )
        else:
            self.safe_exit()

    def safe_exit(self):
        """Safely stops controller, resets fans to BIOS auto, and terminates application."""
        if hasattr(self, "tray"):
            self.tray.hide()
        self.controller.stop()
        QApplication.quit()
