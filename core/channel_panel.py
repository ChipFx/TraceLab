"""
core/channel_panel.py
Left-side channel list: toggle visibility, color, drag-to-reorder.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QScrollArea, QMenu, QColorDialog, QSizePolicy,
    QAbstractItemView, QListWidget, QListWidgetItem, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QMimeData, QObject, QEvent, QPoint
from PyQt6.QtGui import QColor, QFont, QDrag, QPixmap, QPainter, QCursor
from typing import List, Dict
from core.trace_model import TraceModel


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

    def _set_interp(self, mode: str):
        self.trace._interp_mode_override = mode
        self.interp_changed.emit(self.trace.name, mode)

    def _set_primary_segment(self, idx):
        self.trace.primary_segment = idx
        self.segment_changed.emit(self.trace.name)

    def _set_viewmode(self, mode: str):
        self.trace.non_primary_viewmode = mode
        self.segment_changed.emit(self.trace.name)


class _ChannelGroupHeader(QWidget):
    """Compact group header: collapse toggle + group name + All/None buttons."""
    def __init__(self, group_name: str, rows_ref: list,
                 on_toggle_collapse, parent=None):
        super().__init__(parent)
        self.group_name   = group_name
        self._rows_ref    = rows_ref           # shared list; populated after creation
        self._collapsed   = False
        self._on_toggle   = on_toggle_collapse
        self.setFixedHeight(24)
        self.setStyleSheet(
            "background: #161630; border-bottom: 1px solid #3a3a6a;")
        hl = QHBoxLayout(self)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)

        self._btn_collapse = QPushButton("▼")
        self._btn_collapse.setFixedSize(16, 16)
        self._btn_collapse.setStyleSheet(
            "QPushButton { color: #8080c0; border: none; font-size: 9px; "
            "background: transparent; padding: 0; } "
            "QPushButton:hover { color: #c0c0ff; }")
        self._btn_collapse.setToolTip("Collapse/expand group")
        self._btn_collapse.clicked.connect(self._toggle)
        hl.addWidget(self._btn_collapse)

        lbl = QLabel(group_name)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(
            "color: #8080c0; font-weight: bold; background: transparent; border: none;")
        hl.addWidget(lbl, 1)

        btn_all = QPushButton("✓")
        btn_all.setFixedSize(16, 16)
        btn_all.setToolTip("Enable all in group")
        btn_all.setStyleSheet(
            "QPushButton { font-size: 9px; color: #60a060; border: none; "
            "background: transparent; padding: 0; } "
            "QPushButton:hover { color: #80e080; }")
        btn_all.clicked.connect(
            lambda: [r.chk_vis.setChecked(True) for r in self._rows_ref])
        hl.addWidget(btn_all)

        btn_none = QPushButton("✕")
        btn_none.setFixedSize(16, 16)
        btn_none.setToolTip("Disable all in group")
        btn_none.setStyleSheet(
            "QPushButton { font-size: 9px; color: #a06060; border: none; "
            "background: transparent; padding: 0; } "
            "QPushButton:hover { color: #e08080; }")
        btn_none.clicked.connect(
            lambda: [r.chk_vis.setChecked(False) for r in self._rows_ref])
        hl.addWidget(btn_none)

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._btn_collapse.setText("▶" if self._collapsed else "▼")
        self._on_toggle(self.group_name, self._collapsed)


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(170)
        self.setMaximumWidth(270)
        self._rows: Dict[str, ChannelRow] = {}   # name -> ChannelRow
        self._trace_order: List[str] = []        # names in display order
        self._group_rows: Dict[str, List[str]] = {}     # group -> [trace names]
        self._group_items: Dict[str, QListWidgetItem] = {}  # group -> header item
        self._group_hdr_rows: Dict[str, List] = {}      # group -> [ChannelRow refs]

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

    def _insert_group_header(self, group: str, at_row: int):
        """Insert a non-draggable group header item at the given list row."""
        if group not in self._group_hdr_rows:
            self._group_hdr_rows[group] = []
        hdr_widget = _ChannelGroupHeader(
            group, self._group_hdr_rows[group],
            on_toggle_collapse=self._on_group_collapse)
        item = QListWidgetItem()
        item.setData(_GROUP_HEADER_ROLE, group)       # marks it as a group header
        item.setSizeHint(QSize(0, 24))
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
        row.visibility_changed.connect(self.visibility_changed)
        row.color_changed.connect(self.color_changed)
        row.remove_requested.connect(self._on_remove)
        row.interp_changed.connect(self.interp_changed)
        row.reset_color.connect(self.reset_color_requested)
        row.renamed.connect(self.trace_renamed)
        row.segment_changed.connect(self.segment_changed)

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

    def _set_all_interp(self, mode: str):
        for row in self._rows.values():
            row.trace._interp_mode_override = mode
            self.interp_changed.emit(row.trace.name, mode)
