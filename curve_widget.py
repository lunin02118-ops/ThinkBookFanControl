"""
Interactive Fan Curve Widget for PySide6
Provides:
- Visual plotting of temperature vs RPM
- Gradient fill under curve
- Draggable points for real-time visual adjustment
- Live operating point marker (showing current GPU temp and fan RPM)
- Double click to add point, right click to delete point
"""

from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QLinearGradient,
    QFont,
    QPolygonF,
    QPainterPath,
)
from PySide6.QtCore import Qt, QPointF, Signal


class InteractiveCurveWidget(QWidget):
    """Custom graphical widget displaying and editing fan curve."""

    curve_changed = Signal(list)

    TEMP_MIN = 30.0
    TEMP_MAX = 90.0
    RPM_MIN = 2000.0
    RPM_MAX = 7500.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 260)
        self.setMouseTracking(True)

        self.points = [
            {"temp": 45, "rpm": 2400},
            {"temp": 55, "rpm": 3000},
            {"temp": 62, "rpm": 3600},
            {"temp": 65, "rpm": 4150},
            {"temp": 72, "rpm": 4600},
            {"temp": 80, "rpm": 5000},
        ]

        self.drag_index = None
        self.hover_index = None
        self._last_tooltip = ""

        # Current live telemetry point
        self.live_temp = None
        self.live_rpm = None

        # Margins for axes
        self.margin_left = 65
        self.margin_right = 25
        self.margin_top = 25
        self.margin_bottom = 45

    def set_points(self, points: list[dict]):
        self.points = sorted(points, key=lambda p: p["temp"])
        self.update()

    def get_points(self) -> list[dict]:
        return list(self.points)

    def set_live_point(self, temp: float | None, rpm: float | None):
        if self.live_temp != temp or self.live_rpm != rpm:
            self.live_temp = temp
            self.live_rpm = rpm
            self.update()

    # Coordinate conversions
    def _plot_rect(self):
        w = self.width() - self.margin_left - self.margin_right
        h = self.height() - self.margin_top - self.margin_bottom
        return self.margin_left, self.margin_top, max(10, w), max(10, h)

    def _val_to_pixel(self, temp: float, rpm: float) -> QPointF:
        x0, y0, w, h = self._plot_rect()
        nx = (temp - self.TEMP_MIN) / (self.TEMP_MAX - self.TEMP_MIN)
        ny = (rpm - self.RPM_MIN) / (self.RPM_MAX - self.RPM_MIN)
        px = x0 + nx * w
        py = y0 + (1.0 - ny) * h
        return QPointF(px, py)

    def _pixel_to_val(self, px: float, py: float) -> tuple[float, float]:
        x0, y0, w, h = self._plot_rect()
        nx = max(0.0, min(1.0, (px - x0) / w))
        ny = max(0.0, min(1.0, 1.0 - ((py - y0) / h)))
        temp = self.TEMP_MIN + nx * (self.TEMP_MAX - self.TEMP_MIN)
        rpm = self.RPM_MIN + ny * (self.RPM_MAX - self.RPM_MIN)
        return temp, rpm

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        x0, y0, w, h = self._plot_rect()

        # 1. Background
        painter.fillRect(self.rect(), QColor("#14161d"))
        painter.fillRect(int(x0), int(y0), int(w), int(h), QColor("#1b1e28"))

        # 2. Grid lines & Labels
        font_axis = QFont("Segoe UI", 8)
        font_axis_bold = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(font_axis)

        # Horizontal RPM lines (2000 to 5000 every 500)
        pen_grid = QPen(QColor(50, 56, 75, 160), 1, Qt.DashLine)
        pen_axis = QPen(QColor(90, 100, 130), 1)

        painter.setPen(pen_grid)
        for rpm_val in range(2000, 7500, 500):
            p = self._val_to_pixel(self.TEMP_MIN, rpm_val)
            painter.setPen(pen_grid)
            painter.drawLine(int(x0), int(p.y()), int(x0 + w), int(p.y()))

            # Y-axis label
            painter.setPen(QColor(160, 175, 200))
            painter.drawText(
                5,
                int(p.y() - 8),
                self.margin_left - 12,
                16,
                Qt.AlignRight | Qt.AlignVCenter,
                f"{rpm_val}",
            )

        # Vertical Temp lines (30 to 90 every 10)
        for temp_val in range(30, 100, 10):
            p = self._val_to_pixel(temp_val, self.RPM_MIN)
            painter.setPen(pen_grid)
            painter.drawLine(int(p.x()), int(y0), int(p.x()), int(y0 + h))

            # X-axis label
            painter.setPen(QColor(160, 175, 200))
            painter.drawText(
                int(p.x() - 25),
                int(y0 + h + 8),
                50,
                20,
                Qt.AlignHCenter | Qt.AlignTop,
                f"{temp_val}°C",
            )

        # Plot Border
        painter.setPen(pen_axis)
        painter.drawRect(int(x0), int(y0), int(w), int(h))

        # 3. Target 65°C Indicator band
        p65_top = self._val_to_pixel(65, self.RPM_MAX)
        p65_bot = self._val_to_pixel(65, self.RPM_MIN)
        painter.setPen(QPen(QColor(255, 193, 7, 70), 2, Qt.DotLine))
        painter.drawLine(int(p65_top.x()), int(p65_top.y()), int(p65_bot.x()), int(p65_bot.y()))

        # 4. Fill Area Under Curve
        if self.points:
            curve_path = QPainterPath()
            first_pt = self._val_to_pixel(self.points[0]["temp"], self.points[0]["rpm"])
            curve_path.moveTo(x0, first_pt.y())
            curve_path.lineTo(first_pt)

            for pt in self.points[1:]:
                pix = self._val_to_pixel(pt["temp"], pt["rpm"])
                curve_path.lineTo(pix)

            last_pt = self._val_to_pixel(self.points[-1]["temp"], self.points[-1]["rpm"])
            curve_path.lineTo(x0 + w, last_pt.y())
            curve_path.lineTo(x0 + w, y0 + h)
            curve_path.lineTo(x0, y0 + h)
            curve_path.closeSubpath()

            grad = QLinearGradient(0, y0, 0, y0 + h)
            grad.setColorAt(0.0, QColor(0, 180, 216, 120))
            grad.setColorAt(1.0, QColor(0, 119, 182, 10))
            painter.fillPath(curve_path, QBrush(grad))

            # 5. Curve Line
            painter.setPen(QPen(QColor(0, 180, 216), 3))
            p_prev = first_pt
            painter.drawLine(QPointF(x0, first_pt.y()), first_pt)
            for pt in self.points[1:]:
                pix = self._val_to_pixel(pt["temp"], pt["rpm"])
                painter.drawLine(p_prev, pix)
                p_prev = pix
            painter.drawLine(p_prev, QPointF(x0 + w, last_pt.y()))

        # 6. Draw Control Points
        for i, pt in enumerate(self.points):
            pix = self._val_to_pixel(pt["temp"], pt["rpm"])
            is_hover = (i == self.hover_index) or (i == self.drag_index)
            radius = 7 if is_hover else 5

            # Glow effect
            if is_hover:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(72, 202, 228, 80))
                painter.drawEllipse(pix, radius + 4, radius + 4)

            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QColor("#0096c7" if not is_hover else "#48cae4"))
            painter.drawEllipse(pix, radius, radius)

        # 7. Draw Live Telemetry Point (Current operating condition)
        if self.live_temp is not None and self.live_rpm is not None:
            live_pix = self._val_to_pixel(self.live_temp, self.live_rpm)

            # Crosshairs
            painter.setPen(QPen(QColor(76, 201, 240, 120), 1, Qt.DashLine))
            painter.drawLine(int(x0), int(live_pix.y()), int(x0 + w), int(live_pix.y()))
            painter.drawLine(int(live_pix.x()), int(y0), int(live_pix.x()), int(y0 + h))

            # Glowing live marker
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(56, 176, 0, 120))
            painter.drawEllipse(live_pix, 10, 10)

            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QColor("#38b000"))
            painter.drawEllipse(live_pix, 6, 6)

            # Small live badge
            badge_text = f"Текущий: {self.live_temp:.0f}°C @ {self.live_rpm} RPM"
            painter.setFont(font_axis_bold)
            painter.setPen(QColor("#38b000"))
            painter.drawText(int(x0 + 10), int(y0 + 20), badge_text)

        painter.end()

    # Mouse Interaction for point dragging
    def _find_point_near(self, pos: QPointF, max_dist=14.0) -> int | None:
        for i, pt in enumerate(self.points):
            pix = self._val_to_pixel(pt["temp"], pt["rpm"])
            dist = ((pix.x() - pos.x()) ** 2 + (pix.y() - pos.y()) ** 2) ** 0.5
            if dist <= max_dist:
                return i
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            idx = self._find_point_near(event.position())
            if idx is not None:
                self.drag_index = idx
                self.update()
        elif event.button() == Qt.RightButton:
            # Delete point if more than 2 points remain
            idx = self._find_point_near(event.position())
            if idx is not None and len(self.points) > 2:
                self.points.pop(idx)
                self.curve_changed.emit(self.points)
                self.update()

    def add_point_smart(self):
        """Adds a new control point at the largest interval."""
        if not self.points:
            self.points = [{"temp": 45, "rpm": 2500}, {"temp": 75, "rpm": 4500}]
            self.update()
            self.curve_changed.emit(self.points)
            return

        # Find largest gap
        max_gap = 0
        insert_idx = 0
        for i in range(len(self.points) - 1):
            gap = self.points[i + 1]["temp"] - self.points[i]["temp"]
            if gap > max_gap:
                max_gap = gap
                insert_idx = i

        if max_gap >= 4:
            p0 = self.points[insert_idx]
            p1 = self.points[insert_idx + 1]
            new_temp = (p0["temp"] + p1["temp"]) // 2
            ratio = (new_temp - p0["temp"]) / (p1["temp"] - p0["temp"])
            new_rpm = int(round((p0["rpm"] + ratio * (p1["rpm"] - p0["rpm"])) / 50.0) * 50)
            self.points.insert(insert_idx + 1, {"temp": new_temp, "rpm": new_rpm})
        else:
            if self.points[-1]["temp"] < 88:
                self.points.append({"temp": min(90, self.points[-1]["temp"] + 5), "rpm": 7500})
            elif self.points[0]["temp"] > 35:
                self.points.insert(0, {"temp": max(30, self.points[0]["temp"] - 5), "rpm": 2200})
        self.update()
        self.curve_changed.emit(self.points)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self.drag_index is not None:
            temp, rpm = self._pixel_to_val(pos.x(), pos.y())
            # Clamp point boundaries relative to neighbors
            temp_min_bound = self.TEMP_MIN if self.drag_index == 0 else self.points[self.drag_index - 1]["temp"] + 1
            temp_max_bound = self.TEMP_MAX if self.drag_index == len(self.points) - 1 else self.points[self.drag_index + 1]["temp"] - 1
            if temp_min_bound > temp_max_bound:
                temp_min_bound = temp_max_bound

            clamped_temp = int(round(max(temp_min_bound, min(temp_max_bound, temp))))
            # Round RPM to nearest 50
            clamped_rpm = int(round(max(self.RPM_MIN, min(self.RPM_MAX, rpm)) / 50.0) * 50)

            self.points[self.drag_index]["temp"] = clamped_temp
            self.points[self.drag_index]["rpm"] = clamped_rpm

            tip_text = f"{clamped_temp}°C : {clamped_rpm} RPM"
            if tip_text != self._last_tooltip:
                self._last_tooltip = tip_text
                QToolTip.showText(event.globalPosition().toPoint(), tip_text, self)

            self.update()
            self.curve_changed.emit(self.points)
        else:
            near_idx = self._find_point_near(pos)
            if near_idx != self.hover_index:
                self.hover_index = near_idx
                self.setCursor(Qt.PointingHandCursor if near_idx is not None else Qt.ArrowCursor)
                self.update()
                if near_idx is not None:
                    pt = self.points[near_idx]
                    tip_text = f"Точка #{near_idx+1}: {pt['temp']}°C ➔ {pt['rpm']} RPM"
                    self._last_tooltip = tip_text
                    QToolTip.showText(event.globalPosition().toPoint(), tip_text, self)
                else:
                    self._last_tooltip = ""

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_index is not None:
            self.drag_index = None
            self.update()
            self.curve_changed.emit(self.points)

    def mouseDoubleClickEvent(self, event):
        # Double click to add or update point
        if event.button() == Qt.LeftButton:
            x0, y0, w, h = self._plot_rect()
            pos = event.position()
            if x0 <= pos.x() <= x0 + w and y0 <= pos.y() <= y0 + h:
                temp, rpm = self._pixel_to_val(pos.x(), pos.y())
                t_int = int(round(temp))
                r_int = int(round(rpm / 50.0) * 50)

                # Check if close to existing point
                for pt in self.points:
                    if abs(pt["temp"] - t_int) <= 2:
                        pt["rpm"] = r_int
                        self.update()
                        self.curve_changed.emit(self.points)
                        return

                self.points.append({"temp": t_int, "rpm": r_int})
                self.points.sort(key=lambda p: p["temp"])
                self.update()
                self.curve_changed.emit(self.points)
