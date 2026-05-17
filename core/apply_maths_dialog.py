"""
core/apply_maths_dialog.py
Dialog for creating or editing a Maths trace.

Layout
------
  ┌─ Inputs ─────────────────────────────────────────────────────────────┐
  │  A: [trace dropdown ▼]  [Filtered ▼]   [−]                          │
  │  B: [trace dropdown ▼]  [Raw      ▼]   [−]                          │
  │                                         [+ Add input]               │
  ├─ Alignment ───────────────────────────────────────────────────────────┤
  │  ● Fastest rate   ○ Reference: [Input A (CH1) ▼]                    │
  ├─ Expression ──────────────────────────────────────────────────────────┤
  │  Inputs: [A] [B] [C]   Ops: [+] [−] [×] [÷] [(] [)] [**] [abs()]   │
  │  ┌──────────────────────────────────────────────────────────────────┐ │
  │  │ (A + B) / C                                                      │ │
  │  └──────────────────────────────────────────────────────────────────┘ │
  │  [Clear]  [⌫]                                                         │
  ├─ Output ──────────────────────────────────────────────────────────────┤
  │  Name (display): Maths 000     Unit: [    ]                          │
  └───────────────────────────────────────────────────────────── [Apply] ─┘

In edit mode (existing_recipe provided) the Apply button becomes "Update"
and the result_name is fixed to the existing trace's name.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore    import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QComboBox, QPushButton, QLineEdit,
    QRadioButton, QButtonGroup, QSizePolicy,
    QWidget, QMessageBox,
)

from pytraceview.trace_model import TraceModel
from pytraceview.maths_engine import MathsRecipe, MathsEvalError, evaluate_maths

# Maximum number of inputs the dialog supports (A … P)
_MAX_INPUTS = 16
_ALIASES    = "ABCDEFGHIJKLMNOP"


class ApplyMathsDialog(QDialog):
    """Create or edit a Maths trace."""

    # Emitted when the user clicks Apply/Update.
    # args: (recipe: MathsRecipe, result_trace: TraceModel)
    maths_applied = pyqtSignal(object, object)

    def __init__(
        self,
        traces:           List[TraceModel],
        existing_recipes: Dict[str, "MathsRecipe"],
        next_name:        str = "Maths_000",
        existing_recipe:  Optional[MathsRecipe] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._traces           = traces
        self._existing_recipes = existing_recipes
        self._next_name        = next_name
        self._edit_recipe      = existing_recipe   # None = create mode
        self._input_rows: List[_InputRow] = []

        self.setWindowTitle(
            self.tr("Apply Maths") if existing_recipe is None
            else self.tr("Edit Maths Trace"))
        self.setMinimumWidth(520)

        self._build_ui()
        self._populate(existing_recipe)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Inputs ────────────────────────────────────────────────────────
        self._inputs_group = QGroupBox(self.tr("Inputs"))
        ig_layout = QVBoxLayout(self._inputs_group)
        ig_layout.setSpacing(4)

        self._inputs_container = QWidget()
        self._inputs_layout    = QGridLayout(self._inputs_container)
        self._inputs_layout.setSpacing(4)
        self._inputs_layout.setColumnStretch(1, 1)
        ig_layout.addWidget(self._inputs_container)

        add_btn = QPushButton(self.tr("+ Add input"))
        add_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        add_btn.clicked.connect(self._add_input_row)
        ig_layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._inputs_group)

        # ── Alignment ─────────────────────────────────────────────────────
        align_group  = QGroupBox(self.tr("Time alignment"))
        align_layout = QHBoxLayout(align_group)
        self._rb_fastest = QRadioButton(self.tr("Fastest rate"))
        self._rb_fastest.setChecked(True)
        self._rb_ref     = QRadioButton(self.tr("Reference input:"))
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
        root.addWidget(align_group)

        # ── Expression ────────────────────────────────────────────────────
        expr_group  = QGroupBox(self.tr("Expression"))
        expr_layout = QVBoxLayout(expr_group)

        # Alias buttons row (populated dynamically)
        alias_row = QHBoxLayout()
        alias_row.setSpacing(4)
        alias_label = QLabel(self.tr("Inputs:"))
        alias_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        alias_row.addWidget(alias_label)
        self._alias_btn_layout = QHBoxLayout()
        self._alias_btn_layout.setSpacing(2)
        alias_row.addLayout(self._alias_btn_layout)
        alias_row.addStretch()
        expr_layout.addLayout(alias_row)

        # Row 1: arithmetic operators (compact fixed-width — single characters)
        ops_row = QHBoxLayout()
        ops_row.setSpacing(4)
        ops_row.addWidget(QLabel(self.tr("Ops:")))
        for label, text in [
            ("+", "+"), ("-", "-"), ("*", "*"), ("/", "/"),
            ("(", "("), (")", ")"), ("**", "**"),
        ]:
            b = QPushButton(label)
            b.setFixedWidth(36)
            b.clicked.connect(lambda _=False, t=text: self._insert_text(t))
            ops_row.addWidget(b)
        ops_row.addStretch()
        expr_layout.addLayout(ops_row)

        # Row 2: math functions — no fixed width so they scale with font
        fns_row = QHBoxLayout()
        fns_row.setSpacing(4)
        fns_row.addWidget(QLabel(self.tr("Fns:")))
        # Trig/inverse take radians
        for label, text, tip in [
            ("abs()",    "abs(",    "Absolute value"),
            ("sqrt()",   "sqrt(",   "Square root"),
            ("sin()",    "sin(",    "Sine  (radians input)"),
            ("cos()",    "cos(",    "Cosine  (radians input)"),
            ("arcsin()", "arcsin(", "Inverse sine  (output: radians, input: -1..1)"),
            ("arccos()", "arccos(", "Inverse cosine  (output: 0..pi, input: -1..1)"),
            ("arctan()", "arctan(", "Inverse tangent  (output: radians)"),
            ("integ()",  "integ(",  "Cumulative integral  (output unit: input*s)"),
            ("diff()",   "diff(",   "Numerical derivative  (output unit: input/s)"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, t=text: self._insert_text(t))
            fns_row.addWidget(b)
        fns_row.addStretch()
        expr_layout.addLayout(fns_row)

        # Expression text field
        self._expr_edit = QLineEdit()
        self._expr_edit.setPlaceholderText(self.tr("e.g.  (A + B) / C"))
        expr_layout.addWidget(self._expr_edit)

        # Clear / Backspace
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
        self._name_edit = QLineEdit(self._next_name.replace("_", " "))
        self._name_edit.setMinimumWidth(120)
        self._name_edit.setToolTip(
            self.tr("Display name for this maths trace.  "
                    "Auto-numbered names are only a suggestion."))
        out_layout.addWidget(self._name_edit)
        out_layout.addSpacing(16)
        out_layout.addWidget(QLabel(self.tr("Unit:")))
        self._unit_edit = QLineEdit()
        self._unit_edit.setFixedWidth(80)
        self._unit_edit.setPlaceholderText(self.tr("V, A, W, ..."))
        out_layout.addWidget(self._unit_edit)
        out_layout.addStretch()
        root.addWidget(out_group)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        apply_lbl = self.tr("Update") if self._edit_recipe else self.tr("Apply")
        self._apply_btn = QPushButton(apply_lbl)
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self._on_apply)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._apply_btn)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    # ── Input row management ───────────────────────────────────────────────────

    def _add_input_row(self, trace_name: str = "", filter_mode: str = "filtered"):
        idx   = len(self._input_rows)
        if idx >= _MAX_INPUTS:
            return
        alias = _ALIASES[idx]
        row   = _InputRow(alias, self._traces, trace_name, filter_mode, self)
        row.remove_clicked.connect(lambda: self._remove_input_row(row))
        row.selection_changed.connect(self._refresh_alias_buttons)
        row.selection_changed.connect(self._refresh_align_combo)
        self._input_rows.append(row)

        r = idx
        self._inputs_layout.addWidget(row.alias_lbl,     r, 0)
        self._inputs_layout.addWidget(row.trace_combo,   r, 1)
        self._inputs_layout.addWidget(row.filter_combo,  r, 2)
        self._inputs_layout.addWidget(row.remove_btn,    r, 3)

        self._refresh_alias_buttons()
        self._refresh_align_combo()

    def _remove_input_row(self, row: "_InputRow"):
        if len(self._input_rows) <= 1:
            return
        idx = self._input_rows.index(row)
        self._input_rows.pop(idx)

        # Remove widgets from grid
        for w in (row.alias_lbl, row.trace_combo, row.filter_combo, row.remove_btn):
            self._inputs_layout.removeWidget(w)
            w.setParent(None)
            w.deleteLater()

        # Re-letter remaining rows
        for i, r2 in enumerate(self._input_rows):
            r2.alias_lbl.setText(f"{_ALIASES[i]}:")

        self._refresh_alias_buttons()
        self._refresh_align_combo()

    def _refresh_alias_buttons(self):
        """Rebuild the alias shortcut buttons above the expression field."""
        while self._alias_btn_layout.count():
            item = self._alias_btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, row in enumerate(self._input_rows):
            alias = _ALIASES[i]
            b = QPushButton(alias)
            b.setFixedWidth(32)
            b.setToolTip(row.selected_trace_label())
            b.clicked.connect(lambda _=False, a=alias: self._insert_text(a))
            self._alias_btn_layout.addWidget(b)

    def _refresh_align_combo(self):
        """Rebuild the reference combo for the alignment selector."""
        prev = self._align_combo.currentText()
        self._align_combo.blockSignals(True)
        self._align_combo.clear()
        for i, row in enumerate(self._input_rows):
            alias = _ALIASES[i]
            lbl   = row.selected_trace_label()
            self._align_combo.addItem(f"{alias}: {lbl}", _ALIASES[i])
        idx = self._align_combo.findText(prev)
        if idx >= 0:
            self._align_combo.setCurrentIndex(idx)
        self._align_combo.blockSignals(False)

    # ── Pre-population (edit mode) ─────────────────────────────────────────────

    def _populate(self, recipe: Optional[MathsRecipe]):
        if recipe is None:
            # Create mode: start with two empty input rows
            self._add_input_row()
            self._add_input_row()
            return

        # Edit mode: fill from existing recipe
        self._name_edit.setText(recipe.result_label or recipe.result_name)
        self._unit_edit.setText(recipe.result_unit)
        self._expr_edit.setText(recipe.expression)

        for alias, tname in recipe.source_map.items():
            fmode = recipe.filter_mode.get(alias, "filtered")
            self._add_input_row(trace_name=tname, filter_mode=fmode)

        if not self._input_rows:
            self._add_input_row()
            self._add_input_row()

        # Alignment
        if recipe.alignment_ref == "fastest":
            self._rb_fastest.setChecked(True)
        else:
            self._rb_ref.setChecked(True)
            ref_alias = next(
                (a for a, n in recipe.source_map.items()
                 if n == recipe.alignment_ref), None)
            if ref_alias:
                idx = self._align_combo.findData(ref_alias)
                if idx >= 0:
                    self._align_combo.setCurrentIndex(idx)

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

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _on_apply(self):
        # Build source_map and filter_mode
        source_map:  Dict[str, str] = {}
        filter_mode: Dict[str, str] = {}
        for i, row in enumerate(self._input_rows):
            alias = _ALIASES[i]
            tname = row.selected_trace_name()
            if not tname:
                QMessageBox.warning(
                    self, self.tr("Apply Maths"),
                    self.tr(f"Input {alias} has no trace selected."))
                return
            source_map[alias]  = tname
            filter_mode[alias] = row.filter_mode()

        expr = self._expr_edit.text().strip()
        if not expr:
            QMessageBox.warning(
                self, self.tr("Apply Maths"),
                self.tr("Please enter an expression."))
            return

        # Alignment reference
        if self._rb_fastest.isChecked():
            align_ref = "fastest"
        else:
            align_ref_alias = self._align_combo.currentData()
            if align_ref_alias and align_ref_alias in source_map:
                align_ref = source_map[align_ref_alias]
            else:
                align_ref = "fastest"

        # Result name — label is user-editable; internal name is derived from it
        user_label = self._name_edit.text().strip() or self._next_name.replace("_", " ")
        if self._edit_recipe:
            result_name  = self._edit_recipe.result_name   # internal key never changes on edit
            result_label = user_label
        else:
            # Derive a Python-identifier-safe internal name from the display label
            result_label = user_label
            result_name  = user_label.replace(" ", "_")

        recipe = MathsRecipe(
            expression    = expr,
            source_map    = source_map,
            filter_mode   = filter_mode,
            alignment_ref = align_ref,
            result_name   = result_name,
            result_label  = result_label,
            result_unit   = self._unit_edit.text().strip(),
        )

        # Validate by running the engine
        traces_by_name = {t.name: t for t in self._traces}
        try:
            result_trace = evaluate_maths(recipe, traces_by_name)
        except MathsEvalError as exc:
            QMessageBox.critical(
                self, self.tr("Maths Error"),
                str(exc))
            return

        self.maths_applied.emit(recipe, result_trace)
        self.accept()


# ── Input row helper ───────────────────────────────────────────────────────────

class _InputRow(QObject):
    """Logical row in the Inputs section (not a visual widget itself).

    Owns the four individual widgets that get placed into the grid layout by
    the dialog.  Using QObject rather than QWidget avoids the row appearing as
    a floating, invisible widget drawn over the dialog.
    """

    remove_clicked    = pyqtSignal()
    selection_changed = pyqtSignal()

    def __init__(
        self,
        alias:       str,
        traces:      List[TraceModel],
        trace_name:  str = "",
        filter_mode: str = "filtered",
        parent=None,
    ):
        super().__init__(parent)
        self._traces = traces

        self.alias_lbl    = QLabel(f"{alias}:")
        self.alias_lbl.setFixedWidth(24)

        self.trace_combo  = QComboBox()
        self.trace_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.filter_combo = QComboBox()
        self.filter_combo.addItem("Filtered", "filtered")
        self.filter_combo.addItem("Raw",      "raw")
        self.filter_combo.setFixedWidth(90)

        self.remove_btn   = QPushButton("-")
        self.remove_btn.setFixedWidth(28)
        self.remove_btn.setToolTip("Remove this input")
        self.remove_btn.clicked.connect(self.remove_clicked)

        # Populate trace combo
        for t in traces:
            self.trace_combo.addItem(t.label, t.name)
        if trace_name:
            idx = self.trace_combo.findData(trace_name)
            if idx >= 0:
                self.trace_combo.setCurrentIndex(idx)

        # Set filter mode
        fi = self.filter_combo.findData(filter_mode)
        if fi >= 0:
            self.filter_combo.setCurrentIndex(fi)

        self.trace_combo.currentIndexChanged.connect(
            lambda _: self.selection_changed.emit())

    def selected_trace_name(self) -> str:
        return self.trace_combo.currentData() or ""

    def selected_trace_label(self) -> str:
        return self.trace_combo.currentText()

    def filter_mode(self) -> str:
        return self.filter_combo.currentData() or "filtered"
