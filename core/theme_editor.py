"""
core/theme_editor.py
Theme editor window — edit all colours and trace palette for any theme.

Features:
  - List all discovered themes; select to edit
  - Colour pickers for every plotview and statusbar key
  - Trace colour list: add, edit, delete, reorder; live preview swatch
  - Save (overwrites theme file) / Save As (new file) / Discard
  - on_apply callback so the main window can hot-reload the active theme
"""

import os
import json
import copy
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QScrollArea,
    QGroupBox, QColorDialog, QInputDialog, QMessageBox, QTabWidget,
    QFrame, QSizePolicy, QFileDialog, QComboBox, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap
from typing import Callable, Optional


def _color_btn(hex_color: str, size: int = 28) -> QPushButton:
    """Create a small square colour swatch button."""
    btn = QPushButton()
    btn.setFixedSize(size, size)
    btn.setStyleSheet(
        f"background:{hex_color}; border:1px solid #666; border-radius:3px;")
    btn.setToolTip(hex_color)
    return btn


def _swatch(hex_color: str, size: int = 20) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    lbl.setStyleSheet(
        f"background:{hex_color}; border:1px solid #888; border-radius:2px;")
    return lbl


# Keys and human labels for plotview section
_PV_KEYS = [
    ("bg",           "Window background"),
    ("bg_panel",     "Panel background"),
    ("bg_plot",      "Plot background"),
    ("scope_bg",     "Scope plot background"),
    ("scope_grid",   "Scope grid"),
    ("grid_major",   "Major grid lines"),
    ("grid_minor",   "Minor grid lines"),
    ("text",         "Primary text"),
    ("text_dim",     "Dimmed text"),
    ("accent",       "Accent / highlight"),
    ("border",       "Borders / separators"),
    ("cursor_a",     "Cursor A colour"),
    ("cursor_b",     "Cursor B colour"),
    ("toolbar_bg",   "Toolbar background"),
    ("statusbar_bg", "Qt status bar background"),
]

_SB_KEYS = [
    ("bar_bg",    "Status bar background"),
    ("info_bg",   "Info block background"),
    ("info_text", "Info block text"),
    ("info_dim",  "Dimmed label text"),
    ("trig_text", "Trigger info text"),
    ("sep",       "Block separators"),
    ("logo_bg",   "Logo block background"),
    ("logo_text", "Logo primary text"),
    ("logo_sub",  "Logo subtitle text"),
]


class ColorRow(QWidget):
    """One row: label | swatch | hex edit | pick button."""
    changed = pyqtSignal(str, str)  # key, new_hex

    def __init__(self, key: str, label: str, hex_color: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._hex = hex_color

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(6)

        lbl = QLabel(label)
        lbl.setFixedWidth(200)
        lbl.setFont(QFont("Segoe UI", 9))
        layout.addWidget(lbl)

        self._swatch = _swatch(hex_color)
        layout.addWidget(self._swatch)

        self._edit = QLineEdit(hex_color)
        self._edit.setFixedWidth(80)
        self._edit.setFont(QFont("Courier New", 9))
        self._edit.editingFinished.connect(self._on_edit)
        layout.addWidget(self._edit)

        btn = QPushButton("Pick…")
        btn.setFixedWidth(50)
        btn.clicked.connect(self._pick)
        layout.addWidget(btn)
        layout.addStretch()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._hex), self, f"Pick colour for {self._key}")
        if c.isValid():
            self._set(c.name())

    def _on_edit(self):
        t = self._edit.text().strip()
        if t.startswith("#") and len(t) in (4, 7, 9):
            self._set(t)

    def _set(self, hex_color: str):
        self._hex = hex_color
        self._edit.setText(hex_color)
        self._swatch.setStyleSheet(
            f"background:{hex_color}; border:1px solid #888; border-radius:2px;")
        self.changed.emit(self._key, hex_color)

    def get_hex(self) -> str:
        return self._hex


class TraceColorList(QWidget):
    """Editable list of trace colours with add/delete/move/pick."""
    changed = pyqtSignal()

    def __init__(self, colors: list, parent=None):
        super().__init__(parent)
        self._colors = list(colors)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.setDragDropMode(self._list.DragDropMode.InternalMove)
        self._list.model().rowsMoved.connect(self._sync_order)
        layout.addWidget(self._list)

        btns = QHBoxLayout()
        for label, fn in [
            ("Add",    self._add),
            ("Edit",   self._edit_selected),
            ("Delete", self._delete),
            ("↑",      lambda: self._move(-1)),
            ("↓",      lambda: self._move(1)),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(24)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch()
        layout.addLayout(btns)

        self._rebuild()

    def _rebuild(self):
        self._list.clear()
        for i, c in enumerate(self._colors):
            item = QListWidgetItem(f"  {i+1:2d}.  {c}")
            px = QPixmap(16, 16)
            px.fill(QColor(c))
            item.setIcon(QIcon(px))
            item.setData(Qt.ItemDataRole.UserRole, c)
            self._list.addItem(item)

    def _sync_order(self):
        self._colors = [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        ]
        self._rebuild()
        self.changed.emit()

    def _add(self):
        c = QColorDialog.getColor(QColor("#ffffff"), self, "New trace colour")
        if c.isValid():
            self._colors.append(c.name())
            self._rebuild()
            self.changed.emit()

    def _edit_selected(self):
        row = self._list.currentRow()
        if row < 0:
            return
        old = self._colors[row]
        c = QColorDialog.getColor(QColor(old), self, f"Edit trace colour {row+1}")
        if c.isValid():
            self._colors[row] = c.name()
            self._rebuild()
            self.changed.emit()

    def _delete(self):
        row = self._list.currentRow()
        if row < 0 or len(self._colors) <= 1:
            return
        self._colors.pop(row)
        self._rebuild()
        self.changed.emit()

    def _move(self, delta: int):
        row = self._list.currentRow()
        new = row + delta
        if 0 <= new < len(self._colors):
            self._colors.insert(new, self._colors.pop(row))
            self._rebuild()
            self._list.setCurrentRow(new)
            self.changed.emit()

    def get_colors(self) -> list:
        return list(self._colors)


class ThemeEditorWindow(QMainWindow):
    """Main theme editor window."""

    def __init__(self, theme_manager, parent=None,
                 on_apply: Optional[Callable] = None):
        super().__init__(parent)
        self._tm = theme_manager
        self._on_apply = on_apply
        self._editing: dict = {}   # working copy of active theme data
        self._dirty = False
        self.setWindowTitle("ChipFX TraceLab — Theme Editor")
        self.resize(720, 700)
        self._build_ui()
        self._load_theme(self._tm.theme_name)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Top: theme selector
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Editing theme:"))
        self._combo = QComboBox()
        for fid, td in self._tm.available_themes.items():
            self._combo.addItem(td.name, fid)
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        sel_row.addWidget(self._combo)

        btn_new = QPushButton("New Theme…")
        btn_new.clicked.connect(self._new_theme)
        sel_row.addWidget(btn_new)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Tabs: Plot View | Status Bar | Trace Colours
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # ── Tab: Plot View colours ────────────────────────────────────
        pv_scroll = QScrollArea()
        pv_scroll.setWidgetResizable(True)
        pv_w = QWidget()
        pv_l = QVBoxLayout(pv_w)
        pv_l.setSpacing(2)
        self._pv_rows = {}
        for key, label in _PV_KEYS:
            row = ColorRow(key, label, "#ffffff")
            row.changed.connect(self._on_pv_changed)
            self._pv_rows[key] = row
            pv_l.addWidget(row)
        pv_l.addStretch()
        pv_scroll.setWidget(pv_w)
        self._tabs.addTab(pv_scroll, "Plot View")

        # ── Tab: Status Bar colours ───────────────────────────────────
        sb_scroll = QScrollArea()
        sb_scroll.setWidgetResizable(True)
        sb_w = QWidget()
        sb_l = QVBoxLayout(sb_w)
        sb_l.setSpacing(2)
        self._sb_rows = {}
        for key, label in _SB_KEYS:
            row = ColorRow(key, label, "#888888")
            row.changed.connect(self._on_sb_changed)
            self._sb_rows[key] = row
            sb_l.addWidget(row)
        sb_l.addStretch()
        sb_scroll.setWidget(sb_w)
        self._tabs.addTab(sb_scroll, "Status Bar")

        # ── Tab: Trace Colours ────────────────────────────────────────
        tc_w = QWidget()
        tc_l = QVBoxLayout(tc_w)
        tc_l.addWidget(QLabel(
            "Trace colours are used in order, wrapping around if there are\n"
            "more traces than colours. Drag rows to reorder."))
        self._trace_list = TraceColorList([])
        self._trace_list.changed.connect(self._on_traces_changed)
        tc_l.addWidget(self._trace_list)
        self._tabs.addTab(tc_w, "Trace Colours")

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("Apply to App")
        btn_apply.setStyleSheet(
            "background:#1a6a1a;color:#80ff80;padding:5px 14px;font-weight:bold;")
        btn_apply.clicked.connect(self._apply)
        btn_save = QPushButton("Save Theme File")
        btn_save.setStyleSheet(
            "background:#1a3a6a;color:#80c0ff;padding:5px 14px;")
        btn_save.clicked.connect(self._save)
        btn_saveas = QPushButton("Save As New…")
        btn_saveas.clicked.connect(self._save_as)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        for b in [btn_apply, btn_save, btn_saveas, btn_close]:
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet("color:#ffaa44; padding:2px 8px;")
        layout.addWidget(self._dirty_lbl)

    def _load_theme(self, file_id: str):
        themes = self._tm.available_themes
        if file_id not in themes:
            return
        td = themes[file_id]
        self._editing = td.to_json()

        # Set combo without triggering reload
        idx = self._combo.findData(file_id)
        if idx >= 0:
            self._combo.blockSignals(True)
            self._combo.setCurrentIndex(idx)
            self._combo.blockSignals(False)

        pv = self._editing.get("plotview", {})
        for key, row in self._pv_rows.items():
            row._set(pv.get(key, "#888888"))

        sb = self._editing.get("statusbar", {})
        for key, row in self._sb_rows.items():
            row._set(sb.get(key, "#888888"))

        tc = self._editing.get("trace_colors", ["#ffffff"])
        self._trace_list._colors = list(tc)
        self._trace_list._rebuild()

        self._dirty = False
        self._dirty_lbl.setText("")

    def _on_combo_changed(self, idx: int):
        fid = self._combo.itemData(idx)
        if fid:
            if self._dirty:
                r = QMessageBox.question(
                    self, "Unsaved Changes",
                    "Discard changes to current theme?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r != QMessageBox.StandardButton.Yes:
                    return
            self._load_theme(fid)

    def _on_pv_changed(self, key: str, hex_color: str):
        self._editing.setdefault("plotview", {})[key] = hex_color
        self._mark_dirty()

    def _on_sb_changed(self, key: str, hex_color: str):
        self._editing.setdefault("statusbar", {})[key] = hex_color
        self._mark_dirty()

    def _on_traces_changed(self):
        self._editing["trace_colors"] = self._trace_list.get_colors()
        self._mark_dirty()

    def _mark_dirty(self):
        self._dirty = True
        self._dirty_lbl.setText("● Unsaved changes")

    def _apply(self):
        """Push current edits to ThemeManager and call on_apply."""
        fid = self._combo.currentData()
        themes = self._tm.available_themes
        if fid in themes:
            td = themes[fid]
            # Patch ThemeData in-place without saving to disk
            td._plotview.update(self._editing.get("plotview", {}))
            td._statusbar.update(self._editing.get("statusbar", {}))
            tc = self._editing.get("trace_colors")
            if tc:
                td._traces = list(tc)
        if self._on_apply:
            self._on_apply(fid)

    def _save(self):
        fid = self._combo.currentData()
        themes = self._tm.available_themes
        if fid not in themes:
            return
        td = themes[fid]
        # Update ThemeData fields from editing dict
        td._plotview.update(self._editing.get("plotview", {}))
        td._statusbar.update(self._editing.get("statusbar", {}))
        tc = self._editing.get("trace_colors")
        if tc:
            td._traces = list(tc)
        td.name    = self._editing.get("name", td.name)
        td.tooltip = self._editing.get("tooltip", td.tooltip)
        td.save()
        self._dirty = False
        self._dirty_lbl.setText(f"Saved → {os.path.basename(td.path)}")

    def _save_as(self):
        name, ok = QInputDialog.getText(
            self, "New Theme", "Theme file name (without .json):")
        if not ok or not name.strip():
            return
        fname = name.strip().lower().replace(" ", "_") + ".json"
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "themes", fname)
        data = dict(self._editing)
        data["name"] = name.strip()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # Reload into theme manager
        self._tm.discover()
        # Rebuild combo
        self._combo.blockSignals(True)
        self._combo.clear()
        for fid2, td2 in self._tm.available_themes.items():
            self._combo.addItem(td2.name, fid2)
        new_fid = os.path.splitext(fname)[0]
        idx = self._combo.findData(new_fid)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)
        self._dirty = False
        self._dirty_lbl.setText(f"Saved as → {fname}")

    def _new_theme(self):
        self._save_as()

    def closeEvent(self, event):
        if self._dirty:
            r = QMessageBox.question(
                self, "Unsaved Changes",
                "Close without saving?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()
