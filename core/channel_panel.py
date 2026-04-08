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
            self.trace.color = c.name()
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
        menu = QMenu(self)
        menu.addAction("Remove Trace").triggered.connect(
            lambda: self.remove_requested.emit(self.trace.name))
        menu.exec(event.globalPos())


class ChannelPanel(QWidget):
    """
    Drag-to-reorder channel list.
    Uses a QListWidget with InternalMove drag so rows can be reordered.
    The actual ChannelRow widgets sit inside each QListWidgetItem.
    """

    visibility_changed = pyqtSignal(str, bool)
    color_changed      = pyqtSignal(str, str)
    trace_removed      = pyqtSignal(str)
    order_changed      = pyqtSignal(list)   # emits list of trace names in new order

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
        btn_all  = QPushButton("All")
        btn_none = QPushButton("None")
        btn_all.setFixedHeight(22)
        btn_none.setFixedHeight(22)
        btn_all.clicked.connect(lambda: self._set_all_visible(True))
        btn_none.clicked.connect(lambda: self._set_all_visible(False))
        ctrl.addWidget(btn_all)
        ctrl.addWidget(btn_none)
        layout.addLayout(ctrl)

    def add_trace(self, trace: TraceModel):
        if trace.name in self._rows:
            # Refresh existing
            self._rows[trace.name].refresh()
            return
        row = ChannelRow(trace)
        row.visibility_changed.connect(self.visibility_changed)
        row.color_changed.connect(self.color_changed)
        row.remove_requested.connect(self._on_remove)

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
