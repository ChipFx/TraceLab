"""
core/apply_maths_dialog.py
Dialog for creating or editing a Maths trace.

Maths identifiers (A, B, C …) are GLOBAL: they live on trace.maths_id and
are managed by the channel panel.  This dialog shows currently-assigned
traces as locked rows and lets you assign new ones via a dropdown + Add row.

Layout
------
  ┌─ Channel identifiers ────────────────────────────────────────────────┐
  │  [A]  Sine_1Hz       [Filtered ▼]  [-]   ← locked, already assigned │
  │  [B]  Gate_2Hz       [Filtered ▼]  [-]                               │
  │  ── (scrollable, max 8 rows) ──                                       │
  │  [C]  [unassigned dropdown ▼]  [Add]      ← always visible below     │
  ├─ Expression ──────────────────────────────────────────────────────────┤
  │  [A] [B]  |  [+][-][*][/][(][)][**]                                   │
  │           |  [abs()][sqrt()][sin()][cos()][arcsin()][arccos()]…       │
  │  ┌─────────────────────────────────────────────────────────────────┐  │
  │  │ (A + B) / C                                                     │  │
  │  └─────────────────────────────────────────────────────────────────┘  │
  │  [Clear]  [<-]                                                         │
  ├─ Output ──────────────────────────────────────────────────────────────┤
  │  Name: [Maths 000]   Unit: [  ]                                       │
  └──────────────────────────────────────────────────────────── [Apply]  ─┘
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore    import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QComboBox, QPushButton, QLineEdit,
    QRadioButton, QButtonGroup, QSizePolicy, QScrollArea,
    QWidget, QMessageBox, QFrame,
)

from PyQt6.QtCore import QTimer
from pytraceview.trace_model import TraceModel
from pytraceview.maths_engine import (
    MathsRecipe, MathsEvalError, evaluate_maths, infer_unit, canon_unit,
)

_MAX_SCROLL_ROWS = 8
_ROW_H           = 32    # px per locked row (approx)


class _UnitLineEdit(QLineEdit):
    """QLineEdit that selects all content on focus and tracks whether its
    current value was placed there by unit inference or typed by the user."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_inferred: bool = False
        # Mark as manually-set on any keystroke that leaves non-empty content.
        # Empty result is handled separately in _on_unit_edited() so that
        # clearing the field re-enables auto-fill.
        self.textEdited.connect(
            lambda t: setattr(self, "is_inferred", False) if t.strip() else None)

    def set_inferred(self, text: str):
        """Fill with an inferred unit.  Marks as inferred until the user edits."""
        self.is_inferred = True
        self.setText(text)   # setText does not emit textEdited

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)


def _next_available_id(traces: List[TraceModel]) -> str:
    """Return the next unassigned maths identifier across all traces."""
    used = {t.maths_id for t in traces if t.maths_id}
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if c not in used:
            return c
    for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if a + b not in used:
                return a + b
    return "?"


class ApplyMathsDialog(QDialog):
    """Create or edit a Maths trace using globally-assigned channel identifiers."""

    # (recipe: MathsRecipe, result_trace: TraceModel)
    maths_applied = pyqtSignal(object, object)

    def __init__(
        self,
        traces:           List[TraceModel],
        existing_recipes: Dict[str, MathsRecipe],
        next_name:        str = "Maths_000",
        existing_recipe:  Optional[MathsRecipe] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._traces           = traces          # main window's list — do NOT append here
        self._session_traces:  List[TraceModel] = []  # added during this dialog session
        self._existing_recipes = existing_recipes
        self._next_name        = next_name
        self._edit_recipe      = existing_recipe
        self._inferred_unit:   str = ""
        # filter_mode per maths_id — pre-filled from existing recipe or default
        self._filter_modes: Dict[str, str] = {}
        if existing_recipe:
            self._filter_modes = dict(existing_recipe.filter_mode)

        self.setWindowTitle(
            self.tr("Apply Maths") if existing_recipe is None
            else self.tr("Edit Maths Trace"))
        self.setMinimumWidth(540)

        self._build_ui()

    def _all_traces(self) -> List[TraceModel]:
        """All traces available in this dialog: original + added this session."""
        return self._traces + self._session_traces

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Create the alias button layout early — _refresh_alias_buttons() is
        # called during identifier-section population, before the Expression
        # section exists, so the layout object must exist first.
        self._alias_btn_layout = QHBoxLayout()
        self._alias_btn_layout.setSpacing(2)

        # ── Channel identifiers ───────────────────────────────────────────
        ids_group = QGroupBox(self.tr("Channel identifiers"))
        ids_vl    = QVBoxLayout(ids_group)
        ids_vl.setSpacing(2)

        # Scrollable locked-rows area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._locked_container = QWidget()
        self._locked_layout    = QVBoxLayout(self._locked_container)
        self._locked_layout.setSpacing(2)
        self._locked_layout.setContentsMargins(0, 0, 0, 0)
        self._locked_layout.addStretch()
        self._scroll.setWidget(self._locked_container)
        ids_vl.addWidget(self._scroll)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        ids_vl.addWidget(sep)

        # Add row (always visible below the scroll)
        self._add_row_widget = _AddRow(self._traces, self)
        self._add_row_widget.add_requested.connect(self._on_add_requested)
        ids_vl.addWidget(self._add_row_widget)

        root.addWidget(ids_group)

        # Populate locked rows from current global assignments
        for trace in self._traces:
            if trace.maths_id:
                self._insert_locked_row(trace)
        self._refresh_scroll_height()
        self._refresh_add_row()
        self._refresh_alias_buttons()

        # ── Alignment ─────────────────────────────────────────────────────
        align_group  = QGroupBox(self.tr("Time alignment"))
        align_layout = QHBoxLayout(align_group)
        self._rb_fastest = QRadioButton(self.tr("Fastest rate"))
        self._rb_fastest.setChecked(True)
        self._rb_ref     = QRadioButton(self.tr("Reference:"))
        self._align_bg   = QButtonGroup(self)
        self._align_bg.addButton(self._rb_fastest, 0)
        self._align_bg.addButton(self._rb_ref,     1)
        self._align_combo = QComboBox()
        self._align_combo.setEnabled(False)
        self._rb_ref.toggled.connect(
            lambda on: self._align_combo.setEnabled(on))
        align_layout.addWidget(self._rb_fastest)
        align_layout.addSpacing(16)
        align_layout.addWidget(self._rb_ref)
        align_layout.addWidget(self._align_combo, stretch=1)
        self._refresh_align_combo()
        root.addWidget(align_group)

        # Pre-fill alignment from existing recipe
        if self._edit_recipe and self._edit_recipe.alignment_ref != "fastest":
            self._rb_ref.setChecked(True)
            idx = self._align_combo.findData(self._edit_recipe.alignment_ref)
            if idx >= 0:
                self._align_combo.setCurrentIndex(idx)

        # ── Expression ────────────────────────────────────────────────────
        expr_group  = QGroupBox(self.tr("Expression"))
        expr_layout = QVBoxLayout(expr_group)

        # Alias shortcut buttons (rebuilt as IDs are added/removed)
        alias_row = QHBoxLayout()
        alias_row.setSpacing(4)
        alias_row.addWidget(QLabel(self.tr("IDs:")))
        alias_row.addLayout(self._alias_btn_layout)
        alias_row.addStretch()
        expr_layout.addLayout(alias_row)

        # Arithmetic operators
        ops_row = QHBoxLayout()
        ops_row.setSpacing(4)
        ops_row.addWidget(QLabel(self.tr("Ops:")))
        for label, text, tip in [
            ("+",  "+",  ""),
            ("-",  "-",  ""),
            ("*",  "*",  ""),
            ("/",  "/",  ""),
            ("(",  "(",  ""),
            (")",  ")",  ""),
            ("^",  "^",  "Power of  (same as **)"),
            ("**", "**", "Power of  (e.g. A**2 = A squared)"),
        ]:
            b = QPushButton(label)
            b.setFixedWidth(36)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(lambda _=False, t=text: self._insert_text(t))
            ops_row.addWidget(b)
        ops_row.addStretch()
        expr_layout.addLayout(ops_row)

        # Math functions (no fixed width — scale with font)
        fns_row = QHBoxLayout()
        fns_row.setSpacing(4)
        fns_row.addWidget(QLabel(self.tr("Fns:")))
        for label, text, tip in [
            ("abs()",    "abs(",    "Absolute value"),
            ("sqrt()",   "sqrt(",   "Square root"),
            ("sin()",    "sin(",    "Sine  (radians)"),
            ("cos()",    "cos(",    "Cosine  (radians)"),
            ("arcsin()", "arcsin(", "Inverse sine  (out: radians, in: -1..1)"),
            ("arccos()", "arccos(", "Inverse cosine  (out: 0..pi, in: -1..1)"),
            ("arctan()", "arctan(", "Inverse tangent  (out: radians)"),
            ("integ()",  "integ(",  "Cumulative integral  (unit x s)"),
            ("diff()",   "diff(",   "Numerical derivative  (unit / s)"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, t=text: self._insert_text(t))
            fns_row.addWidget(b)
        fns_row.addStretch()
        expr_layout.addLayout(fns_row)

        self._expr_edit = QLineEdit()
        self._expr_edit.setPlaceholderText(self.tr("e.g.  (A + B) / C"))
        if self._edit_recipe:
            self._expr_edit.setText(self._edit_recipe.expression)
        self._expr_edit.textChanged.connect(self._on_expr_changed)
        expr_layout.addWidget(self._expr_edit)

        clear_row = QHBoxLayout()
        clear_btn = QPushButton(self.tr("Clear"))
        clear_btn.clicked.connect(lambda: self._expr_edit.clear())
        back_btn  = QPushButton("<-")
        back_btn.setToolTip(self.tr("Delete last character"))
        back_btn.clicked.connect(self._backspace)
        clear_row.addWidget(clear_btn)
        clear_row.addWidget(back_btn)
        clear_row.addStretch()
        expr_layout.addLayout(clear_row)
        root.addWidget(expr_group)

        # ── Output ────────────────────────────────────────────────────────
        out_group  = QGroupBox(self.tr("Output"))
        out_layout = QHBoxLayout(out_group)
        out_layout.addWidget(QLabel(self.tr("Name:")))
        default_label = (self._edit_recipe.result_label
                         if self._edit_recipe else
                         self._next_name.replace("_", " "))
        self._name_edit = QLineEdit(default_label)
        self._name_edit.setMinimumWidth(120)
        self._name_edit.setToolTip(
            self.tr("Display name for this maths trace."))
        out_layout.addWidget(self._name_edit)
        out_layout.addSpacing(16)
        out_layout.addWidget(QLabel(self.tr("Unit:")))
        self._unit_edit = _UnitLineEdit()
        self._unit_edit.setFixedWidth(80)
        self._unit_edit.setPlaceholderText(self.tr("V, A, W, ..."))
        if self._edit_recipe and self._edit_recipe.result_unit:
            self._unit_edit.setText(self._edit_recipe.result_unit)
        self._unit_edit.textEdited.connect(self._on_unit_edited)
        out_layout.addWidget(self._unit_edit)
        self._unit_hint = QLabel("")
        self._unit_hint.setStyleSheet("color: #888; font-style: italic;")
        out_layout.addWidget(self._unit_hint)
        out_layout.addStretch()
        root.addWidget(out_group)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        if self._edit_recipe:
            upd_btn = QPushButton(self.tr("Update"))
            upd_btn.setDefault(True)
            upd_btn.clicked.connect(lambda: self._on_apply(close_after=True))
            btn_row.addWidget(upd_btn)
        else:
            self._add_btn = QPushButton(self.tr("Add"))
            self._add_btn.setToolTip(
                self.tr("Add this maths trace and keep the dialog open"))
            self._add_btn.clicked.connect(lambda: self._on_apply(close_after=False))
            btn_row.addWidget(self._add_btn)

            self._add_close_btn = QPushButton(self.tr("Add and Close"))
            self._add_close_btn.setDefault(True)
            self._add_close_btn.clicked.connect(lambda: self._on_apply(close_after=True))
            btn_row.addWidget(self._add_close_btn)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Locked rows ────────────────────────────────────────────────────────────

    def _insert_locked_row(self, trace: TraceModel):
        fmode = self._filter_modes.get(trace.maths_id, "filtered")
        row = _LockedRow(trace, fmode, self)
        row.clear_requested.connect(self._on_clear_requested)
        row.filter_mode_changed.connect(self._on_filter_mode_changed)
        # Insert before the stretch at the end
        count = self._locked_layout.count()
        self._locked_layout.insertWidget(count - 1, row)

    def _remove_locked_row(self, trace_name: str):
        for i in range(self._locked_layout.count()):
            item = self._locked_layout.itemAt(i)
            if item and isinstance(item.widget(), _LockedRow):
                if item.widget().trace_name == trace_name:
                    w = self._locked_layout.takeAt(i).widget()
                    w.deleteLater()
                    return

    def _locked_row_count(self) -> int:
        return sum(
            1 for i in range(self._locked_layout.count())
            if isinstance(self._locked_layout.itemAt(i).widget(), _LockedRow)
        )

    def _refresh_scroll_height(self):
        n = min(self._locked_row_count(), _MAX_SCROLL_ROWS)
        h = max(n * (_ROW_H + 2) + 4, 4)
        self._scroll.setFixedHeight(h)
        self._scroll.setVisible(n > 0)

    def _refresh_add_row(self):
        all_t   = self._all_traces()
        next_id = _next_available_id(all_t)
        unassigned = [t for t in all_t if not t.maths_id]
        self._add_row_widget.refresh(next_id, unassigned)

    def _refresh_alias_buttons(self):
        while self._alias_btn_layout.count():
            item = self._alias_btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for trace in self._all_traces():
            if trace.maths_id:
                b = QPushButton(trace.maths_id)
                b.setFixedWidth(32)
                b.setToolTip(f"{trace.maths_id} = {trace.label}")
                mid = trace.maths_id
                b.clicked.connect(lambda _=False, m=mid: self._insert_text(m))
                self._alias_btn_layout.addWidget(b)

    def _refresh_align_combo(self):
        prev = self._align_combo.currentData()
        self._align_combo.blockSignals(True)
        self._align_combo.clear()
        for trace in self._all_traces():
            if trace.maths_id:
                self._align_combo.addItem(
                    f"{trace.maths_id}: {trace.label}", trace.name)
        idx = self._align_combo.findData(prev)
        if idx >= 0:
            self._align_combo.setCurrentIndex(idx)
        self._align_combo.blockSignals(False)

    # ── Unit inference ─────────────────────────────────────────────────────────

    def _on_expr_changed(self):
        """Update unit inference whenever the expression changes."""
        if not hasattr(self, "_unit_hint"):
            return
        alias_units = {
            t.maths_id: canon_unit(t.unit)
            for t in self._all_traces() if t.maths_id and t.unit
        }
        inferred = infer_unit(self._expr_edit.text(), alias_units)
        self._inferred_unit = inferred

        current_text = self._unit_edit.text().strip()
        can_autofill = self._unit_edit.is_inferred or not current_text

        if inferred:
            if can_autofill:
                self._unit_edit.set_inferred(inferred)
                self._unit_hint.setText(self.tr("(inferred)"))
            elif current_text == inferred:
                self._unit_hint.setText(self.tr("(matches inferred)"))
            else:
                self._unit_hint.setText(f"(inferred: {inferred})")
        else:
            if self._unit_edit.is_inferred:
                self._unit_edit.set_inferred("")
            self._unit_hint.setText("")

    def _on_unit_edited(self, text: str):
        """User manually edited the unit field."""
        if not text.strip() and self._inferred_unit:
            # Field was cleared — re-enable auto-fill
            self._unit_edit.is_inferred = True
            self._on_expr_changed()
        elif self._inferred_unit:
            if text.strip() == self._inferred_unit:
                self._unit_hint.setText(self.tr("(matches inferred)"))
            else:
                self._unit_hint.setText(f"(inferred: {self._inferred_unit})")
        else:
            self._unit_hint.setText("")

    # ── Event handlers ─────────────────────────────────────────────────────────

    def _on_add_requested(self, trace_name: str, maths_id: str):
        trace = next((t for t in self._traces if t.name == trace_name), None)
        if trace is None:
            return
        trace.maths_id = maths_id
        self._filter_modes.setdefault(maths_id, "filtered")
        self._insert_locked_row(trace)
        self._refresh_scroll_height()
        self._refresh_add_row()
        self._refresh_alias_buttons()
        self._refresh_align_combo()

    def _on_clear_requested(self, trace_name: str):
        trace = next((t for t in self._traces if t.name == trace_name), None)
        if trace:
            self._filter_modes.pop(trace.maths_id, None)
            trace.maths_id = ""
        self._remove_locked_row(trace_name)
        self._refresh_scroll_height()
        self._refresh_add_row()
        self._refresh_alias_buttons()
        self._refresh_align_combo()

    def _on_filter_mode_changed(self, maths_id: str, mode: str):
        self._filter_modes[maths_id] = mode

    # ── Expression helpers ─────────────────────────────────────────────────────

    def _insert_text(self, text: str):
        pos = self._expr_edit.cursorPosition()
        cur = self._expr_edit.text()
        self._expr_edit.setText(cur[:pos] + text + cur[pos:])
        self._expr_edit.setCursorPosition(pos + len(text))
        self._expr_edit.setFocus()

    def _backspace(self):
        pos = self._expr_edit.cursorPosition()
        if pos > 0:
            cur = self._expr_edit.text()
            self._expr_edit.setText(cur[:pos - 1] + cur[pos:])
            self._expr_edit.setCursorPosition(pos - 1)
        self._expr_edit.setFocus()

    # ── Name helpers ───────────────────────────────────────────────────────────

    def _compute_next_name(self) -> str:
        existing = {t.name for t in self._all_traces()}
        i = 0
        while True:
            candidate = f"Maths_{i:03d}"
            if candidate not in existing:
                return candidate
            i += 1

    # ── Apply ──────────────────────────────────────────────────────────────────

    def _on_apply(self, close_after: bool = True):
        source_map = {t.maths_id: t.name
                      for t in self._all_traces() if t.maths_id}

        if not source_map:
            QMessageBox.warning(
                self, self.tr("Apply Maths"),
                self.tr("Assign at least one channel identifier before applying."))
            return

        expr = self._expr_edit.text().strip()
        if not expr:
            QMessageBox.warning(
                self, self.tr("Apply Maths"),
                self.tr("Please enter an expression."))
            return

        filter_mode = {mid: self._filter_modes.get(mid, "filtered")
                       for mid in source_map}

        if self._rb_fastest.isChecked():
            align_ref = "fastest"
        else:
            align_ref = self._align_combo.currentData() or "fastest"

        user_label = self._name_edit.text().strip() or \
                     self._next_name.replace("_", " ")
        if self._edit_recipe:
            result_name  = self._edit_recipe.result_name
            result_label = user_label
        else:
            result_label = user_label
            result_name  = user_label.replace(" ", "_")

        result_unit = self._unit_edit.text().strip()

        recipe = MathsRecipe(
            expression    = expr,
            source_map    = source_map,
            filter_mode   = filter_mode,
            alignment_ref = align_ref,
            result_name   = result_name,
            result_label  = result_label,
            result_unit   = result_unit,
        )

        traces_by_name = {t.name: t for t in self._all_traces()}
        try:
            result_trace = evaluate_maths(recipe, traces_by_name)
        except MathsEvalError as exc:
            QMessageBox.critical(self, self.tr("Maths Error"), str(exc))
            return

        # Track in session list (NOT in self._traces) so the main window's
        # _on_maths_applied takes the "new trace" path and calls add_trace()
        # properly — which creates the Maths group and adds the channel row.
        self._session_traces.append(result_trace)
        self.maths_applied.emit(recipe, result_trace)

        if close_after:
            self.accept()
        else:
            # Stay open: advance name, refresh dropdown and alias buttons
            next_name = self._compute_next_name()
            self._name_edit.setText(next_name.replace("_", " "))
            self._refresh_add_row()
            self._refresh_alias_buttons()
            self._refresh_align_combo()


# ── Locked row ─────────────────────────────────────────────────────────────────

class _LockedRow(QWidget):
    """One assigned identifier row: [ID badge] [trace label] [Filt combo] [clear]."""

    clear_requested    = pyqtSignal(str)   # trace_name
    filter_mode_changed = pyqtSignal(str, str)  # maths_id, mode

    def __init__(self, trace: TraceModel, filter_mode: str = "filtered",
                 parent=None):
        super().__init__(parent)
        self.trace_name = trace.name
        self._maths_id  = trace.maths_id
        self.setFixedHeight(32)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(2, 1, 2, 1)
        hl.setSpacing(6)

        badge = QPushButton(trace.maths_id)
        badge.setFixedSize(28, 22)
        badge.setEnabled(False)
        badge.setStyleSheet(
            "QPushButton { background: #886600; color: #ffffff; "
            "border: none; border-radius: 2px; font-weight: bold; "
            "font-size: 9px; }")
        hl.addWidget(badge)

        lbl = QLabel(trace.label)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hl.addWidget(lbl)

        self._filt_combo = QComboBox()
        self._filt_combo.addItem("Filtered", "filtered")
        self._filt_combo.addItem("Raw",      "raw")
        self._filt_combo.setFixedWidth(90)
        fi = self._filt_combo.findData(filter_mode)
        if fi >= 0:
            self._filt_combo.setCurrentIndex(fi)
        self._filt_combo.currentIndexChanged.connect(self._on_filt_changed)
        hl.addWidget(self._filt_combo)

        clear_btn = QPushButton("-")
        clear_btn.setFixedSize(22, 22)
        clear_btn.setToolTip("Clear this identifier assignment")
        clear_btn.clicked.connect(
            lambda: self.clear_requested.emit(self.trace_name))
        hl.addWidget(clear_btn)

    def _on_filt_changed(self):
        self.filter_mode_changed.emit(
            self._maths_id, self._filt_combo.currentData() or "filtered")


# ── Add row ────────────────────────────────────────────────────────────────────

class _AddRow(QWidget):
    """Pending assignment row: [next-ID label] [unassigned dropdown] [Add]."""

    add_requested = pyqtSignal(str, str)   # trace_name, maths_id

    def __init__(self, traces: List[TraceModel], parent=None):
        super().__init__(parent)
        self._all_traces = traces
        self.setFixedHeight(32)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(2, 1, 2, 1)
        hl.setSpacing(6)

        self._id_lbl = QLabel("?:")
        self._id_lbl.setFixedWidth(28)
        self._id_lbl.setStyleSheet("font-weight: bold;")
        hl.addWidget(self._id_lbl)

        self._combo = QComboBox()
        self._combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._combo.currentIndexChanged.connect(self._update_add_btn)
        hl.addWidget(self._combo)

        self._add_btn = QPushButton("Add")
        self._add_btn.setFixedWidth(48)
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._on_add)
        hl.addWidget(self._add_btn)

    def refresh(self, next_id: str, unassigned: List[TraceModel]):
        self._next_id = next_id
        self._id_lbl.setText(f"{next_id}:")
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem("", "")   # blank placeholder
        for t in unassigned:
            self._combo.addItem(t.label, t.name)
        self._combo.blockSignals(False)
        self._update_add_btn()

    def _update_add_btn(self):
        self._add_btn.setEnabled(bool(self._combo.currentData()))

    def _on_add(self):
        tname = self._combo.currentData()
        if tname:
            self.add_requested.emit(tname, self._next_id)
