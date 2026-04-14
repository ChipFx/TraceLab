"""
core/channel_panel.py
Left-side channel list: toggle visibility, color, drag-to-reorder.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QScrollArea, QMenu, QColorDialog, QSizePolicy,
    QAbstractItemView, QListWidget, QListWidgetItem, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QMimeData
from PyQt6.QtGui import QColor, QFont, QDrag, QPixmap, QPainter, QCursor
from typing import List, Dict
from core.trace_model import TraceModel


class ChannelRow(QWidget):
    """One row: color swatch + checkbox + label."""
    visibility_changed = pyqtSignal(str, bool)
    color_changed      = pyqtSignal(str, str)
    remove_requested   = pyqtSignal(str)
    interp_changed     = pyqtSignal(str, str)
    reset_color        = pyqtSignal(str)         # name

    def __init__(self, trace: TraceModel, parent=None):
        super().__init__(parent)
        self.trace = trace
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        # Drag handle indicator
        grip = QLabel("⠿")
        grip.setStyleSheet("color: #555; font-size: 13px;")
        grip.setFixedWidth(12)
        layout.addWidget(grip)

        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(18, 18)
        self._update_color_btn()
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        self.chk_vis = QCheckBox()
        self.chk_vis.setChecked(trace.visible)
        self.chk_vis.setToolTip("Toggle visibility")
        self.chk_vis.stateChanged.connect(self._toggle_vis)
        layout.addWidget(self.chk_vis)

        self.lbl = QLabel(trace.label)
        self.lbl.setFont(QFont("Courier New", 9))
        self.lbl.setStyleSheet(f"color: {trace.color};")
        self.lbl.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Preferred)
        layout.addWidget(self.lbl)

    def _update_color_btn(self):
        self.btn_color.setStyleSheet(
            f"background-color: {self.trace.color}; "
            f"border: 1px solid #666; border-radius: 2px;")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self.trace.color), self)
        if c.isValid():
            self.trace.set_user_color(c.name())
            self._update_color_btn()
            self.lbl.setStyleSheet(f"color: {self.trace.color};")
            self.color_changed.emit(self.trace.name, self.trace.color)

    def _toggle_vis(self, state):
        vis = bool(state)
        self.trace.visible = vis
        alpha = "1.0" if vis else "0.35"
        self.lbl.setStyleSheet(
            f"color: {self.trace.color}; opacity: {alpha};")
        self.visibility_changed.emit(self.trace.name, vis)

    def refresh(self):
        self.lbl.setText(self.trace.label)
        self.lbl.setStyleSheet(f"color: {self.trace.color};")
        self._update_color_btn()

    def contextMenuEvent(self, event):
        from PyQt6.QtGui import QActionGroup
        menu = QMenu(self)
        interp_menu = menu.addMenu("Interpolation")
        mode = getattr(self.trace, '_interp_mode_override', 'linear')
        ag = QActionGroup(interp_menu)
        ag.setExclusive(True)
        for m, lbl in [("linear", "Linear"),
                        ("cubic",  "Cubic Spline"),
                        ("sinc",   "Sinc (sin(x)/x)")]:
            a = interp_menu.addAction(lbl)
            a.setCheckable(True)
            a.setChecked(mode == m)
            ag.addAction(a)
            a.triggered.connect(lambda _, _m=m: self._set_interp(_m))
        menu.addSeparator()
        menu.addAction("Reset Color to Default").triggered.connect(
            lambda: self.reset_color.emit(self.trace.name))
        menu.addSeparator()
        menu.addAction("Remove Trace").triggered.connect(
            lambda: self.remove_requested.emit(self.trace.name))
        menu.exec(event.globalPos())

    def _set_interp(self, mode: str):
        self.trace._interp_mode_override = mode
        self.interp_changed.emit(self.trace.name, mode)


class ChannelPanel(QWidget):
    """
    Drag-to-reorder channel list.
    Uses a QListWidget with InternalMove drag so rows can be reordered.
    The actual ChannelRow widgets sit inside each QListWidgetItem.
    """

    visibility_changed     = pyqtSignal(str, bool)
    color_changed          = pyqtSignal(str, str)
    trace_removed          = pyqtSignal(str)
    order_changed          = pyqtSignal(list)
    interp_changed         = pyqtSignal(str, str)
    reset_color_requested  = pyqtSignal(str)     # trace name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(170)
        self.setMaximumWidth(270)
        self._rows: Dict[str, ChannelRow] = {}   # name -> ChannelRow
        self._trace_order: List[str] = []        # names in display order

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel("CHANNELS")
        header.setStyleSheet(
            "background: #1a1a1a; color: #888; padding: 5px 8px; "
            "font-size: 10px; font-weight: bold; letter-spacing: 1px;")
        header.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(header)

        # QListWidget provides built-in drag reorder
        self._list = QListWidget()
        self._list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setSpacing(1)
        self._list.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { padding: 0px; }"
            "QListWidget::item:selected { background: #2a2a3a; }")
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self._list)

        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(4, 4, 4, 4)
        ctrl.setSpacing(3)
        btn_all  = QPushButton("All")
        btn_none = QPushButton("None")
        btn_all.setFixedHeight(22)
        btn_none.setFixedHeight(22)
        btn_all.clicked.connect(lambda: self._set_all_visible(True))
        btn_none.clicked.connect(lambda: self._set_all_visible(False))
        ctrl.addWidget(btn_all)
        ctrl.addWidget(btn_none)
        layout.addLayout(ctrl)

        ctrl2 = QHBoxLayout()
        ctrl2.setContentsMargins(4, 0, 4, 4)
        ctrl2.setSpacing(3)
        btn_lin  = QPushButton("All Lin")
        btn_cub  = QPushButton("All Cub")
        btn_sinc = QPushButton("All Sinc")
        btn_lin.setFixedHeight(20)
        btn_cub.setFixedHeight(20)
        btn_sinc.setFixedHeight(20)
        btn_lin.setToolTip("Set all channels to Linear interpolation")
        btn_cub.setToolTip("Set all channels to Cubic Spline interpolation")
        btn_sinc.setToolTip("Set all channels to Sinc (sin(x)/x) interpolation")
        btn_lin.setStyleSheet("font-size: 9px;")
        btn_cub.setStyleSheet("font-size: 9px; color: #cc88ff;")
        btn_sinc.setStyleSheet("font-size: 9px; color: #ff8888;")
        btn_lin.clicked.connect(lambda: self._set_all_interp("linear"))
        btn_cub.clicked.connect(lambda: self._set_all_interp("cubic"))
        btn_sinc.clicked.connect(lambda: self._set_all_interp("sinc"))
        ctrl2.addWidget(btn_lin)
        ctrl2.addWidget(btn_cub)
        ctrl2.addWidget(btn_sinc)
        layout.addLayout(ctrl2)

    def add_trace(self, trace: TraceModel):
        if trace.name in self._rows:
            # Refresh existing
            self._rows[trace.name].refresh()
            return
        row = ChannelRow(trace)
        row.visibility_changed.connect(self.visibility_changed)
        row.color_changed.connect(self.color_changed)
        row.remove_requested.connect(self._on_remove)
        row.interp_changed.connect(self.interp_changed)
        row.reset_color.connect(self.reset_color_requested)

        item = QListWidgetItem(self._list)
        item.setData(Qt.ItemDataRole.UserRole, trace.name)
        item.setSizeHint(QSize(0, 32))
        self._list.setItemWidget(item, row)

        self._rows[trace.name] = row
        self._trace_order.append(trace.name)

    def remove_trace(self, trace_name: str):
        if trace_name not in self._rows:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == trace_name:
                self._list.takeItem(i)
                break
        self._rows.pop(trace_name, None)
        if trace_name in self._trace_order:
            self._trace_order.remove(trace_name)

    def refresh_all(self):
        for row in self._rows.values():
            row.refresh()

    def get_ordered_names(self) -> List[str]:
        """Return trace names in current display order."""
        names = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item:
                names.append(item.data(Qt.ItemDataRole.UserRole))
        return names

    def _on_remove(self, name: str):
        self.remove_trace(name)
        self.trace_removed.emit(name)

    def _on_rows_moved(self, *args):
        new_order = self.get_ordered_names()
        self._trace_order = new_order
        self.order_changed.emit(new_order)

    def _set_all_visible(self, visible: bool):
        for row in self._rows.values():
            row.chk_vis.setChecked(visible)

    def _set_all_interp(self, mode: str):
        for row in self._rows.values():
            row.trace._interp_mode_override = mode
            self.interp_changed.emit(row.trace.name, mode)
