"""
core/edit_filter_stack_dialog.py
Per-trace filter stack editor.

Borrowed pattern from pytraceview's channel_panel: QListWidget with
InternalMove drag-reorder, each item containing a _StackRow widget that
shows the recipe description and a delete button.

Live behaviour: every drag or delete emits stack_changed(new_list);
the [+ Add Filter…] button emits add_filter_requested() so the host
can pop up the main FilterDialog targeted at this one trace.

There is no Apply/Cancel — changes are immediate, matching how the
channel panel works.  The Close button just dismisses the dialog.
"""

from typing import List

from PyQt6.QtCore    import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QAbstractItemView, QWidget, QSizePolicy,
)

from core.filter_engine import FilterRecipe, describe_recipe


# ── Row widget ────────────────────────────────────────────────────────────────

class _StackRow(QWidget):
    """One row: drag handle hint + description label + delete button."""

    delete_requested = pyqtSignal(object)   # FilterRecipe instance

    def __init__(self, recipe: FilterRecipe, parent=None):
        super().__init__(parent)
        self.recipe = recipe
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(6)

        grip = QLabel("⠿")
        grip.setStyleSheet("color: #666; font-size: 13px;")
        grip.setFixedWidth(14)
        hl.addWidget(grip)

        lbl = QLabel(describe_recipe(recipe))
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hl.addWidget(lbl)

        btn = QPushButton("×")
        btn.setFixedSize(20, 20)
        btn.setToolTip("Remove this filter from the stack")
        btn.setStyleSheet(
            "QPushButton { color: #884444; border: none; font-size: 12px; "
            "background: transparent; padding: 0; }"
            "QPushButton:hover { color: #ff6666; }")
        btn.clicked.connect(lambda: self.delete_requested.emit(self.recipe))
        hl.addWidget(btn)


# ── Dialog ────────────────────────────────────────────────────────────────────

class EditFilterStackDialog(QDialog):
    """Edit one trace's filter stack.  Reorder by drag, delete via [×],
    add another filter via [+ Add Filter…].

    Signals:
        stack_changed(list[FilterRecipe]) — fires on every reorder/delete
        add_filter_requested()           — fires when [+ Add Filter…] is clicked
    """

    stack_changed        = pyqtSignal(list)
    add_filter_requested = pyqtSignal()

    def __init__(self, trace_label: str, stack: List[FilterRecipe], parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr(f"Filter Stack — {trace_label}"))
        self.resize(420, 320)
        self._trace_label = trace_label

        root = QVBoxLayout(self)
        root.setSpacing(6)

        hdr = QLabel(self.tr(
            f"Filter stack for <b>{trace_label}</b> — drag to reorder, "
            "click × to remove. Filters are applied top-to-bottom."))
        hdr.setWordWrap(True)
        hdr.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(hdr)

        self._list = QListWidget()
        self._list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setSpacing(1)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        root.addWidget(self._list, stretch=1)

        # Populate with the incoming stack
        for recipe in stack:
            self._add_row(recipe)

        # Buttons
        btn_row = QHBoxLayout()
        add_btn = QPushButton(self.tr("+ Add Filter…"))
        add_btn.setToolTip(self.tr("Open the filter dialog to add another "
                                   "filter to this trace's stack."))
        add_btn.clicked.connect(self.add_filter_requested)
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh_from_external(self, new_stack: List[FilterRecipe]):
        """Re-populate from an externally-provided stack.  Useful after the
        host appends a new filter via the FilterDialog spawned from
        [+ Add Filter…]."""
        self._list.clear()
        for recipe in new_stack:
            self._add_row(recipe)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _add_row(self, recipe: FilterRecipe):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 30))
        row = _StackRow(recipe)
        row.delete_requested.connect(self._on_delete)
        self._list.addItem(item)
        self._list.setItemWidget(item, row)

    def _current_order(self) -> List[FilterRecipe]:
        out = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            row  = self._list.itemWidget(item)
            if row is not None:
                out.append(row.recipe)
        return out

    def _on_rows_moved(self, *_):
        self.stack_changed.emit(self._current_order())

    def _on_delete(self, recipe: FilterRecipe):
        # Remove the matching item by identity
        for i in range(self._list.count()):
            item = self._list.item(i)
            row  = self._list.itemWidget(item)
            if row is not None and row.recipe is recipe:
                self._list.takeItem(i)
                break
        self.stack_changed.emit(self._current_order())
