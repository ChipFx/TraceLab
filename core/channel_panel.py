"""
core/channel_panel.py
Left-side channel list panel: toggle visibility, color, name.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QScrollArea, QFrame, QColorDialog, QSizePolicy,
    QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPixmap, QIcon
from typing import List, Dict
from core.trace_model import TraceModel


class ChannelRow(QWidget):
    """One row in the channel panel for a single trace."""
    visibility_changed = pyqtSignal(str, bool)   # name, visible
    color_changed = pyqtSignal(str, str)          # name, color
    remove_requested = pyqtSignal(str)            # name

    def __init__(self, trace: TraceModel, parent=None):
        super().__init__(parent)
        self.trace = trace
        self.setMaximumHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        # Color swatch / enable toggle
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(20, 20)
        self._update_color_btn()
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        # Visibility checkbox
        self.chk_vis = QCheckBox()
        self.chk_vis.setChecked(trace.visible)
        self.chk_vis.setToolTip("Toggle trace visibility")
        self.chk_vis.stateChanged.connect(self._toggle_vis)
        layout.addWidget(self.chk_vis)

        # Name label
        self.lbl = QLabel(trace.label)
        self.lbl.setFont(QFont("Courier New", 9))
        self.lbl.setStyleSheet(f"color: {trace.color};")
        self.lbl.setSizePolicy(QSizePolicy.Policy.Expanding,
                                QSizePolicy.Policy.Preferred)
        layout.addWidget(self.lbl)

    def _update_color_btn(self):
        self.btn_color.setStyleSheet(
            f"background-color: {self.trace.color}; "
            f"border: 1px solid #555; border-radius: 2px;")

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
        opacity = "1.0" if vis else "0.4"
        self.lbl.setStyleSheet(
            f"color: {self.trace.color}; opacity: {opacity};")
        self.visibility_changed.emit(self.trace.name, vis)

    def refresh(self):
        self.lbl.setText(self.trace.label)
        self.lbl.setStyleSheet(f"color: {self.trace.color};")
        self._update_color_btn()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_remove = menu.addAction("Remove Trace")
        act_remove.triggered.connect(
            lambda: self.remove_requested.emit(self.trace.name))
        menu.exec(event.globalPos())


class ChannelPanel(QWidget):
    """Panel showing all loaded traces with controls."""

    visibility_changed = pyqtSignal(str, bool)
    color_changed = pyqtSignal(str, str)
    trace_removed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.setMaximumWidth(260)
        self._rows: Dict[str, ChannelRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QLabel("CHANNELS")
        header.setStyleSheet(
            "background: #1a1a1a; color: #888; padding: 5px 8px; "
            "font-size: 9px; font-weight: bold; letter-spacing: 1px;")
        header.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(header)

        # Scroll area for rows
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._rows_layout = QVBoxLayout(self._container)
        self._rows_layout.setSpacing(1)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll)

        # Bottom controls
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(4, 4, 4, 4)
        btn_all = QPushButton("All")
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
            return
        row = ChannelRow(trace)
        row.visibility_changed.connect(self.visibility_changed)
        row.color_changed.connect(self.color_changed)
        row.remove_requested.connect(self.trace_removed)
        self._rows[trace.name] = row
        # Insert before the stretch
        count = self._rows_layout.count()
        self._rows_layout.insertWidget(count - 1, row)

    def remove_trace(self, trace_name: str):
        if trace_name in self._rows:
            row = self._rows.pop(trace_name)
            self._rows_layout.removeWidget(row)
            row.deleteLater()

    def refresh_all(self):
        for row in self._rows.values():
            row.refresh()

    def _set_all_visible(self, visible: bool):
        for row in self._rows.values():
            row.chk_vis.setChecked(visible)
