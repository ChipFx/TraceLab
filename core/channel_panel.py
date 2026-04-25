"""
core/channel_panel.py
Left-side channel list: toggle visibility, color, drag-to-reorder.
"""

import fnmatch

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QScrollArea, QMenu, QColorDialog, QSizePolicy,
    QAbstractItemView, QListWidget, QListWidgetItem, QFrame,
    QDialog, QRadioButton, QButtonGroup, QLineEdit, QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QMimeData, QObject, QEvent, QPoint
from PyQt6.QtGui import QColor, QFont, QDrag, QPixmap, QPainter, QCursor
from typing import Dict, List, Set
from core.trace_model import TraceModel


# ── Grouping dialog ───────────────────────────────────────────────────────────

class _GroupingDialog(QDialog):
    """Dialog for regrouping channels by unit, wildcard pattern, or visibility."""

    # Explicit radio-button indicators so they're always visible on dark backgrounds
    _STYLE = """
        QGroupBox {
            color: #a0a0c0;
            border: 1px solid #3a3a6a;
            border-radius: 4px;
            margin-top: 14px;
            padding-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            color: #8080c0;
        }
        QRadioButton {
            color: #c0c0e0;
            spacing: 8px;
        }
        QRadioButton::indicator {
            width: 13px;
            height: 13px;
            border: 2px solid #5050a0;
            border-radius: 7px;
            background: #0e0e22;
        }
        QRadioButton::indicator:checked {
            background: #4060c0;
            border: 2px solid #90a0f0;
        }
        QRadioButton::indicator:hover {
            border: 2px solid #9090f0;
        }
        QRadioButton:disabled { color: #505070; }
        QLineEdit {
            background: #1a1a2e;
            color: #c0c0e0;
            border: 1px solid #4040a0;
            border-radius: 3px;
            padding: 2px 4px;
        }
        QLabel { color: #a0a0c0; }
    """

    def __init__(self, existing_group_names: set = None, parent=None):
        super().__init__(parent)
        self._existing = existing_group_names or set()
        self.setWindowTitle("Group Channels")
        self.setMinimumWidth(480)
        self.setModal(True)
        self.setStyleSheet(self._STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Method ─────────────────────────────────────────────────────────────
        method_box = QGroupBox("Grouping method")
        ml = QVBoxLayout(method_box)
        ml.setSpacing(6)

        self.radio_unit = QRadioButton(
            "By Unit  —  group channels by their assigned unit (V, °C, A, …)")
        self.radio_unit.setChecked(True)
        ml.addWidget(self.radio_unit)

        pat_row = QHBoxLayout()
        self.radio_pattern = QRadioButton("By Pattern:")
        pat_row.addWidget(self.radio_pattern)
        self.edit_pattern = QLineEdit()
        self.edit_pattern.setPlaceholderText("e.g.  3??   or   internal*")
        self.edit_pattern.setEnabled(False)
        self.edit_pattern.setToolTip(
            "Wildcard: * matches any text, ? matches one character.\n"
            "Case-insensitive.  Matched against the channel display label.")
        pat_row.addWidget(self.edit_pattern)
        ml.addLayout(pat_row)

        self.radio_enabled = QRadioButton(
            "Group enabled (visible) channels  —  "
            "collect all currently-checked channels into one group")
        ml.addWidget(self.radio_enabled)

        bg_method = QButtonGroup(self)
        bg_method.addButton(self.radio_unit)
        bg_method.addButton(self.radio_pattern)
        bg_method.addButton(self.radio_enabled)
        self.radio_pattern.toggled.connect(self.edit_pattern.setEnabled)

        layout.addWidget(method_box)

        # ── Mode ───────────────────────────────────────────────────────────────
        mode_box = QGroupBox("When matches are found")
        mode_l = QVBoxLayout(mode_box)
        mode_l.setSpacing(6)
        self.radio_create_new = QRadioButton(
            "Create new group(s)  —  matched channels leave their current group")
        self.radio_create_new.setChecked(True)
        mode_l.addWidget(self.radio_create_new)
        self.radio_create_inside = QRadioButton(
            "Create sub-group inside existing group  —  "
            "unmatched channels stay in the original")
        mode_l.addWidget(self.radio_create_inside)
        bg_mode = QButtonGroup(self)
        bg_mode.addButton(self.radio_create_new)
        bg_mode.addButton(self.radio_create_inside)
        layout.addWidget(mode_box)

        # ── Optional name override ─────────────────────────────────────────────
        name_box = QGroupBox("New Group Name  (optional)")
        nl = QHBoxLayout(name_box)
        nl.addWidget(QLabel("Name:"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Leave blank for auto-generated name")
        self.edit_name.setToolTip(
            "Override the auto-generated group name.\n"
            "If the name already exists, a _001 … _999 suffix is added automatically.\n"
            "For multi-unit grouping this becomes a prefix: Name_V, Name_°C, …")
        nl.addWidget(self.edit_name)
        layout.addWidget(name_box)

        # ── Buttons ────────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_apply = QPushButton("Apply")
        btn_apply.setStyleSheet(
            "background: #2060a0; color: white; padding: 4px 16px; font-weight: bold;")
        btn_apply.setDefault(True)
        btn_cancel.clicked.connect(self.reject)
        btn_apply.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

    def get_config(self):
        """Returns (method, pattern, create_inside, custom_name).
        method: 'unit' | 'pattern' | 'enabled'"""
        if self.radio_unit.isChecked():
            method = 'unit'
        elif self.radio_pattern.isChecked():
            method = 'pattern'
        else:
            method = 'enabled'
        pattern = self.edit_pattern.text().strip()
        create_inside = self.radio_create_inside.isChecked()
        custom_name = self.edit_name.text().strip()
        return method, pattern, create_inside, custom_name


class _LabelClickFilter(QObject):
    """Event filter installed on a QLabel so clicking it toggles a checkbox.
    Tracks press position; only toggles on release if the mouse didn't move
    (i.e. it was a click, not the start of a drag)."""

    def __init__(self, checkbox, parent=None):
        super().__init__(parent)
        self._chk = checkbox
        self._press_pos: QPoint | None = None

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            return False   # let press propagate for drag initiation
        if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._press_pos is not None:
                delta = event.pos() - self._press_pos
                if abs(delta.x()) < 6 and abs(delta.y()) < 6:
                    self._chk.toggle()
            self._press_pos = None
            return False
        return False


class ChannelRow(QWidget):
    """One row: color swatch + checkbox + label."""
    visibility_changed = pyqtSignal(str, bool)
    color_changed      = pyqtSignal(str, str)
    remove_requested   = pyqtSignal(str)
    interp_changed     = pyqtSignal(str, str)
    reset_color        = pyqtSignal(str)         # name
    renamed            = pyqtSignal(str, str)    # (trace_name, new_label)
    segment_changed    = pyqtSignal(str)         # trace_name — primary or viewmode changed
    unit_changed       = pyqtSignal(str, str)    # (trace_name, new_unit)

    def __init__(self, trace: TraceModel, parent=None):
        super().__init__(parent)
        self.trace = trace
        self.scroll_primaries: bool = False  # set by ChannelPanel
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
        self.lbl.setToolTip("Click to toggle visibility")
        self.lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl_click_filter = _LabelClickFilter(self.chk_vis, self.lbl)
        self.lbl.installEventFilter(self._lbl_click_filter)
        layout.addWidget(self.lbl)

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(16, 16)
        btn_del.setToolTip("Remove trace")
        btn_del.setStyleSheet(
            "QPushButton { color: #884444; border: none; font-size: 9px; "
            "background: transparent; padding: 0; }"
            "QPushButton:hover { color: #ff6666; }")
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self.trace.name))
        layout.addWidget(btn_del)

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
        menu.addAction("Rename…").triggered.connect(self._rename)
        menu.addAction("Change Unit…").triggered.connect(self._change_unit)
        # Segment submenu — only shown when the trace has 2+ segments
        segs = getattr(self.trace, 'segments', None)
        if segs and len(segs) >= 2:
            menu.addSeparator()
            seg_menu = menu.addMenu("Segments")
            # Primary segment selector
            pri_menu = seg_menu.addMenu("Primary Segment")
            pri_ag = QActionGroup(pri_menu)
            pri_ag.setExclusive(True)
            cur_primary = getattr(self.trace, 'primary_segment', None)
            none_act = pri_menu.addAction("None (show all)")
            none_act.setCheckable(True)
            none_act.setChecked(cur_primary is None)
            pri_ag.addAction(none_act)
            none_act.triggered.connect(lambda _: self._set_primary_segment(None))
            for idx in range(len(segs)):
                a = pri_menu.addAction(f"Segment {idx}")
                a.setCheckable(True)
                a.setChecked(cur_primary == idx)
                pri_ag.addAction(a)
                a.triggered.connect(lambda _, i=idx: self._set_primary_segment(i))
            # Non-primary view mode
            seg_menu.addSeparator()
            mode_menu = seg_menu.addMenu("Non-primary View")
            mode_ag = QActionGroup(mode_menu)
            mode_ag.setExclusive(True)
            cur_mode = (getattr(self.trace, 'non_primary_viewmode', '') or '').strip()
            for mode_key, mode_lbl in [
                    ("hide",   "Hide"),
                    ("dimmed", "Dimmed"),
                    ("dashed", "Dashed"),
                    ("",       "Regular (full opacity)")]:
                ma = mode_menu.addAction(mode_lbl)
                ma.setCheckable(True)
                ma.setChecked(cur_mode == mode_key)
                mode_ag.addAction(ma)
                ma.triggered.connect(
                    lambda _, mk=mode_key: self._set_viewmode(mk))
        menu.addSeparator()
        menu.addAction("Reset Color to Default").triggered.connect(
            lambda: self.reset_color.emit(self.trace.name))
        menu.addSeparator()
        menu.addAction("Remove Trace").triggered.connect(
            lambda: self.remove_requested.emit(self.trace.name))
        menu.exec(event.globalPos())

    def _rename(self):
        from PyQt6.QtWidgets import QInputDialog
        new_label, ok = QInputDialog.getText(
            self, "Rename Trace", "New label:", text=self.trace.label)
        if ok and new_label.strip():
            new_label = new_label.strip()
            self.trace.label = new_label
            self.lbl.setText(new_label)
            self.renamed.emit(self.trace.name, new_label)

    def _change_unit(self):
        from PyQt6.QtWidgets import QInputDialog
        new_unit, ok = QInputDialog.getText(
            self, "Change Unit", "New unit:", text=self.trace.unit)
        if ok:
            new_unit = new_unit.strip()
            self.trace.unit = new_unit
            if self.trace.scaling:
                self.trace.scaling.unit = new_unit
            self.unit_changed.emit(self.trace.name, new_unit)

    def _set_interp(self, mode: str):
        self.trace._interp_mode_override = mode
        self.interp_changed.emit(self.trace.name, mode)

    def _set_primary_segment(self, idx):
        self.trace.primary_segment = idx
        self.segment_changed.emit(self.trace.name)

    def _set_viewmode(self, mode: str):
        self.trace.non_primary_viewmode = mode
        self.segment_changed.emit(self.trace.name)

    def wheelEvent(self, event):
        segs = getattr(self.trace, 'segments', None)
        cur = getattr(self.trace, 'primary_segment', None)
        if (self.scroll_primaries and segs and len(segs) >= 2
                and cur is not None and 0 <= cur < len(segs)):
            delta = event.angleDelta().y()
            step = 1 if delta < 0 else -1
            new_idx = max(0, min(len(segs) - 1, cur + step))
            if new_idx != cur:
                self.trace.primary_segment = new_idx
                self.segment_changed.emit(self.trace.name)
            event.accept()
        else:
            super().wheelEvent(event)


class _ChannelGroupHeader(QWidget):
    """Group header bar.
    Left-click  → fold / unfold
    Double-click → toggle visibility of all channels in group
    Right-click  → context menu (Show All / Hide All / Rename / Change All Units)
    """
    rename_requested           = pyqtSignal(str)        # group_name
    change_all_units_requested = pyqtSignal(str, str)   # group_name, new_unit

    _BTN = (
        "QPushButton {{ font-size: {fs}px; color: {fg}; border: none; "
        "background: transparent; padding: 0; }} "
        "QPushButton:hover {{ color: {hfg}; }}")

    def __init__(self, group_name: str, rows_ref: list,
                 on_toggle_collapse, parent=None):
        super().__init__(parent)
        self.group_name   = group_name
        self._rows_ref    = rows_ref           # shared list; populated after creation
        self._collapsed   = False
        self._on_toggle   = on_toggle_collapse
        self.setFixedHeight(30)
        self.setStyleSheet(
            "background: #161630; border-bottom: 1px solid #3a3a6a;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(6, 2, 4, 2)
        hl.setSpacing(4)

        # Fold indicator — non-interactive, part of the click-to-fold area
        self._lbl_arrow = QLabel("▼")
        self._lbl_arrow.setFixedWidth(14)
        self._lbl_arrow.setStyleSheet(
            "color: #8080c0; font-size: 11px; "
            "background: transparent; border: none;")
        hl.addWidget(self._lbl_arrow)

        self._lbl_name = QLabel(group_name)
        self._lbl_name.setFont(QFont("Courier New", 9))
        self._lbl_name.setStyleSheet(
            "color: #8080c0; font-weight: bold; "
            "background: transparent; border: none;")
        hl.addWidget(self._lbl_name, 1)

        btn_all = QPushButton("✓")
        btn_all.setFixedSize(22, 22)
        btn_all.setToolTip("Enable all in group  (right-click for more options)")
        btn_all.setStyleSheet(self._BTN.format(fs=13, fg="#60a060", hfg="#90e090"))
        btn_all.clicked.connect(
            lambda: [r.chk_vis.setChecked(True) for r in self._rows_ref])
        hl.addWidget(btn_all)

        btn_none = QPushButton("✕")
        btn_none.setFixedSize(22, 22)
        btn_none.setToolTip("Disable all in group  (right-click for more options)")
        btn_none.setStyleSheet(self._BTN.format(fs=13, fg="#a06060", hfg="#e08080"))
        btn_none.clicked.connect(
            lambda: [r.chk_vis.setChecked(False) for r in self._rows_ref])
        hl.addWidget(btn_none)

    # ── Click handling ────────────────────────────────────────────────────────

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._lbl_arrow.setText("▶" if self._collapsed else "▼")
        self._on_toggle(self.group_name, self._collapsed)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.pos())
            if not isinstance(child, QPushButton):
                self._toggle()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.pos())
            if not isinstance(child, QPushButton):
                # Toggle: if any visible → hide all; if all hidden → show all
                any_vis = any(r.chk_vis.isChecked() for r in self._rows_ref)
                for r in self._rows_ref:
                    r.chk_vis.setChecked(not any_vis)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Show All").triggered.connect(
            lambda: [r.chk_vis.setChecked(True) for r in self._rows_ref])
        menu.addAction("Hide All").triggered.connect(
            lambda: [r.chk_vis.setChecked(False) for r in self._rows_ref])
        menu.addSeparator()
        menu.addAction("Rename Group…").triggered.connect(
            lambda: self.rename_requested.emit(self.group_name))
        menu.addSeparator()
        menu.addAction("Change All Units…").triggered.connect(
            self._change_all_units)
        menu.exec(event.globalPos())

    def _change_all_units(self):
        from PyQt6.QtWidgets import QInputDialog
        current = next((r.trace.unit for r in self._rows_ref), "")
        new_unit, ok = QInputDialog.getText(
            self, "Change All Units",
            f"New unit for all {len(self._rows_ref)} channel(s) in '{self.group_name}':",
            text=current)
        if ok:
            new_unit = new_unit.strip()
            for r in self._rows_ref:
                r.trace.unit = new_unit
                if r.trace.scaling:
                    r.trace.scaling.unit = new_unit
            self.change_all_units_requested.emit(self.group_name, new_unit)


# ── Channel-panel sentinel for group-header list items ─────────────────────────
_GROUP_HEADER_ROLE = Qt.ItemDataRole.UserRole + 1   # item.data(this) == group name for headers


class ChannelPanel(QWidget):
    """
    Drag-to-reorder channel list.
    Uses a QListWidget with InternalMove drag so rows can be reordered.
    The actual ChannelRow widgets sit inside each QListWidgetItem.
    Group headers appear as non-draggable separator rows with enable/disable all
    and collapse/expand buttons.
    """

    visibility_changed     = pyqtSignal(str, bool)
    color_changed          = pyqtSignal(str, str)
    trace_removed          = pyqtSignal(str)
    order_changed          = pyqtSignal(list)
    interp_changed         = pyqtSignal(str, str)
    reset_color_requested  = pyqtSignal(str)     # trace name
    trace_renamed          = pyqtSignal(str, str)  # (trace_name, new_label)
    segment_changed        = pyqtSignal(str)       # trace_name
    group_renamed          = pyqtSignal(str, str)  # (old_name, new_name)
    unit_changed           = pyqtSignal(str, str)  # (trace_name, new_unit)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(170)
        self.setMaximumWidth(270)
        self._rows: Dict[str, ChannelRow] = {}   # name -> ChannelRow
        self._trace_order: List[str] = []        # names in display order
        self._group_rows: Dict[str, List[str]] = {}     # group -> [trace names]
        self._group_items: Dict[str, QListWidgetItem] = {}  # group -> header item
        self._group_hdr_rows: Dict[str, List] = {}      # group -> [ChannelRow refs]
        self._scroll_primaries: bool = False

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

        ctrl3 = QHBoxLayout()
        ctrl3.setContentsMargins(4, 0, 4, 4)
        btn_group = QPushButton("Group…")
        btn_group.setFixedHeight(20)
        btn_group.setToolTip("Group channels by unit or name pattern")
        btn_group.setStyleSheet("font-size: 9px;")
        btn_group.clicked.connect(self._open_grouping_dialog)
        ctrl3.addWidget(btn_group)
        layout.addLayout(ctrl3)

    def _insert_group_header(self, group: str, at_row: int):
        """Insert a non-draggable group header item at the given list row."""
        if group not in self._group_hdr_rows:
            self._group_hdr_rows[group] = []
        hdr_widget = _ChannelGroupHeader(
            group, self._group_hdr_rows[group],
            on_toggle_collapse=self._on_group_collapse)
        hdr_widget.rename_requested.connect(self._on_group_rename)
        hdr_widget.change_all_units_requested.connect(self._on_group_change_units)
        item = QListWidgetItem()
        item.setData(_GROUP_HEADER_ROLE, group)       # marks it as a group header
        item.setSizeHint(QSize(0, 30))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)      # no drag, no select
        self._list.insertItem(at_row, item)
        self._list.setItemWidget(item, hdr_widget)
        self._group_items[group] = item

    def _find_group_insert_pos(self, group: str) -> int:
        """Row index AFTER the last existing member of `group`, or end of list."""
        members = set(self._group_rows.get(group, []))
        last = -1
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) in members:
                last = i
        return last + 1 if last >= 0 else self._list.count()

    def add_trace(self, trace: TraceModel):
        if trace.name in self._rows:
            self._rows[trace.name].refresh()
            return
        row = ChannelRow(trace)
        row.scroll_primaries = self._scroll_primaries
        row.visibility_changed.connect(self.visibility_changed)
        row.color_changed.connect(self.color_changed)
        row.remove_requested.connect(self._on_remove)
        row.interp_changed.connect(self.interp_changed)
        row.reset_color.connect(self.reset_color_requested)
        row.renamed.connect(self.trace_renamed)
        row.segment_changed.connect(self.segment_changed)
        row.unit_changed.connect(self.unit_changed)

        group = getattr(trace, "col_group", "") or ""

        if group:
            if group not in self._group_rows:
                self._group_rows[group] = []
            if group not in self._group_items:
                # First trace in this group — insert header, then trace below it
                insert_at = self._list.count()
                self._insert_group_header(group, insert_at)
                insert_at += 1
            else:
                insert_at = self._find_group_insert_pos(group)
            self._group_rows[group].append(trace.name)
            if group in self._group_hdr_rows:
                self._group_hdr_rows[group].append(row)

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, trace.name)
            item.setSizeHint(QSize(0, 32))
            self._list.insertItem(insert_at, item)
            self._list.setItemWidget(item, row)
        else:
            # Ungrouped: append at end
            item = QListWidgetItem(self._list)
            item.setData(Qt.ItemDataRole.UserRole, trace.name)
            item.setSizeHint(QSize(0, 32))
            self._list.setItemWidget(item, row)

        self._rows[trace.name] = row
        self._trace_order.append(trace.name)

    def remove_trace(self, trace_name: str):
        if trace_name not in self._rows:
            return
        # Remove from group tracking
        for grp, names in list(self._group_rows.items()):
            if trace_name in names:
                names.remove(trace_name)
                # Also remove from hdr_rows ref list
                if grp in self._group_hdr_rows:
                    self._group_hdr_rows[grp] = [
                        r for r in self._group_hdr_rows[grp]
                        if r.trace.name != trace_name]
                # If group is now empty, remove its header too
                if not names:
                    hdr_item = self._group_items.pop(grp, None)
                    if hdr_item:
                        row = self._list.row(hdr_item)
                        if row >= 0:
                            self._list.takeItem(row)
                    self._group_rows.pop(grp, None)
                    self._group_hdr_rows.pop(grp, None)
                break
        # Remove the channel row item
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == trace_name:
                self._list.takeItem(i)
                break
        self._rows.pop(trace_name, None)
        if trace_name in self._trace_order:
            self._trace_order.remove(trace_name)

    def _on_group_collapse(self, group: str, collapsed: bool):
        """Show/hide list items belonging to `group`."""
        members = set(self._group_rows.get(group, []))
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) in members:
                item.setHidden(collapsed)

    def refresh_all(self):
        for row in self._rows.values():
            row.refresh()

    def get_ordered_names(self) -> List[str]:
        """Return trace names in current display order (skips group headers)."""
        names = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item:
                name = item.data(Qt.ItemDataRole.UserRole)
                if name is not None:   # None means it's a group header
                    names.append(name)
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

    def set_scroll_primaries(self, enabled: bool):
        """Enable/disable wheel-to-step-primary-segment on all rows."""
        self._scroll_primaries = enabled
        for row in self._rows.values():
            row.scroll_primaries = enabled

    def _set_all_interp(self, mode: str):
        for row in self._rows.values():
            row.trace._interp_mode_override = mode
            self.interp_changed.emit(row.trace.name, mode)

    # ── Grouping ──────────────────────────────────────────────────────────────

    def _full_rebuild(self):
        """Rebuild the list widget entirely from current trace.col_group state."""
        ordered_traces = [self._rows[n].trace
                          for n in self._trace_order if n in self._rows]
        self._list.clear()
        self._rows.clear()
        self._trace_order.clear()
        self._group_rows.clear()
        self._group_items.clear()
        self._group_hdr_rows.clear()
        for trace in ordered_traces:
            self.add_trace(trace)

    def _open_grouping_dialog(self):
        existing = set(self._group_rows.keys())
        dlg = _GroupingDialog(existing_group_names=existing, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        method, pattern, create_inside, custom_name = dlg.get_config()
        if method == 'unit':
            self._apply_group_by_unit(create_inside, custom_name)
        elif method == 'pattern':
            if not pattern:
                return
            self._apply_group_by_pattern(pattern, create_inside, custom_name)
        elif method == 'enabled':
            self._apply_group_enabled(custom_name)

    def _unique_group_name(self, base: str, also_exclude: set = None) -> str:
        """Return base if not already a group, else base_001 … base_999."""
        existing = set(self._group_rows.keys())
        if also_exclude:
            existing |= also_exclude
        if base not in existing:
            return base
        for i in range(1, 1000):
            candidate = f"{base}_{i:03d}"
            if candidate not in existing:
                return candidate
        return base

    def _on_group_rename(self, old_name: str):
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, "Rename Group", "New group name:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()
        for tname in list(self._group_rows.get(old_name, [])):
            if tname in self._rows:
                self._rows[tname].trace.col_group = new_name
        self._full_rebuild()
        self.group_renamed.emit(old_name, new_name)

    def _on_group_change_units(self, group_name: str, new_unit: str):
        """Propagate unit_changed for all traces in the group."""
        for tname in self._group_rows.get(group_name, []):
            self.unit_changed.emit(tname, new_unit)

    def _apply_group_by_unit(self, create_inside: bool, custom_name: str = ""):
        ordered_traces = [self._rows[n].trace
                          for n in self._trace_order if n in self._rows]
        # Pre-compute target names to avoid calling unique_group_name per trace
        target_map: Dict[tuple, str] = {}   # (old_g, unit) → new col_group
        allocated: set = set()

        def _alloc(base: str) -> str:
            name = self._unique_group_name(base, allocated)
            allocated.add(name)
            return name

        if create_inside:
            group_units: Dict[str, Set[str]] = {}
            for trace in ordered_traces:
                g = trace.col_group or "__ungrouped__"
                unit = trace.unit.strip() or "Other"
                group_units.setdefault(g, set()).add(unit)
            for old_g, units in group_units.items():
                if len(units) <= 1:
                    continue  # homogeneous group → no split
                for unit in units:
                    suffix = f"{custom_name}_{unit}" if custom_name else unit
                    if old_g == "__ungrouped__":
                        base = suffix
                    else:
                        base = f"{old_g}_{suffix}"
                    target_map[(old_g, unit)] = _alloc(base)
            for trace in ordered_traces:
                key = (trace.col_group or "__ungrouped__",
                       trace.unit.strip() or "Other")
                if key in target_map:
                    trace.col_group = target_map[key]
        else:
            unit_target: Dict[str, str] = {}
            for trace in ordered_traces:
                unit = trace.unit.strip() or "Other"
                if unit not in unit_target:
                    base = f"{custom_name}_{unit}" if custom_name else unit
                    unit_target[unit] = _alloc(base)
            for trace in ordered_traces:
                trace.col_group = unit_target[trace.unit.strip() or "Other"]
        self._full_rebuild()

    def _apply_group_by_pattern(self, pattern: str, create_inside: bool,
                                custom_name: str = ""):
        pat_lower = pattern.lower()
        name_repr = pattern.replace('*', '(ALL)')
        allocated: set = set()

        def _alloc(base: str) -> str:
            name = self._unique_group_name(base, allocated)
            allocated.add(name)
            return name

        ordered_traces = [self._rows[n].trace
                          for n in self._trace_order if n in self._rows]

        def _matches(trace) -> bool:
            label = (trace.label or trace.name or "").lower()
            return fnmatch.fnmatch(label, pat_lower)

        if create_inside:
            group_has_nonmatch: Dict[str, bool] = {}
            for trace in ordered_traces:
                g = trace.col_group or "__ungrouped__"
                if not _matches(trace):
                    group_has_nonmatch[g] = True
            # Pre-compute one target name per source group that has mixed membership
            group_target: Dict[str, str] = {}
            for g, has_nm in group_has_nonmatch.items():
                suffix = custom_name or name_repr
                base = suffix if g == "__ungrouped__" else f"{g}_{suffix}"
                group_target[g] = _alloc(base)
            for trace in ordered_traces:
                if not _matches(trace):
                    continue
                old_g = trace.col_group or "__ungrouped__"
                if old_g in group_target:
                    trace.col_group = group_target[old_g]
        else:
            base = custom_name or f"Group_{name_repr}"
            group_name = _alloc(base)
            for trace in ordered_traces:
                if _matches(trace):
                    trace.col_group = group_name
        self._full_rebuild()

    def _apply_group_enabled(self, custom_name: str = ""):
        """Collect all currently-visible traces into one new group."""
        base = custom_name or "Enabled"
        group_name = self._unique_group_name(base)
        ordered_traces = [self._rows[n].trace
                          for n in self._trace_order if n in self._rows]
        for trace in ordered_traces:
            if trace.visible:
                trace.col_group = group_name
        self._full_rebuild()
