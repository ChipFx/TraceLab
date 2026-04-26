"""
core/import_dialog.py
Import dialog with locale-safe number inputs and working gain/offset scaling.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QPushButton, QScrollArea,
    QWidget, QGroupBox, QTabWidget, QApplication,
    QMessageBox, QRadioButton, QButtonGroup, QFrame
)
from PyQt6.QtCore import Qt, QMimeData, QPoint
from PyQt6.QtGui import QFont, QDrag, QPixmap, QPainter, QColor
from core.grouping_dialog import GroupingDialog
import numpy as np
from typing import Dict, List, Optional
from core.data_loader import LoadResult, is_numeric_column, CsvMetadata, parse_value
from core.trace_model import TraceModel, ScalingConfig


# ── Locale-safe number input ──────────────────────────────────────────────────

class SciLineEdit(QLineEdit):
    """
    A QLineEdit for scientific/engineering numbers.
    - Accepts both '.' and ',' as decimal separator regardless of locale.
    - Supports fractions: 2.5/4096
    - Supports metric suffixes: 10k, 2.2M
    - On focus-in: selects all text (easy to replace with new value)
    """

    def __init__(self, default: str = "1", parent=None):
        super().__init__(default, parent)
        self.setToolTip(
            "Enter a number. Both '.' and ',' work as decimal separator.\n"
            "Fractions supported: 2.5/4096\n"
            "Metric suffixes: 10k, 2.2M, 100n")

    def focusInEvent(self, event):
        super().focusInEvent(event)
        # Delay selectAll so it fires AFTER the click fully resolves on Windows.
        # Without the timer the click position caret overwrites the selection.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self.selectAll)

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        self._update_parse_style()

    def _update_parse_style(self):
        """Tint field red when the current text cannot be parsed."""
        text = _normalise_decimal(self.text().strip())
        try:
            if text:
                parse_value(text)
            self.setStyleSheet("")
        except Exception:
            if text:
                self.setStyleSheet("background: #3a1010;")


    def get_value(self, default: float = 1.0) -> float:
        """Parse with locale tolerance: comma → dot."""
        text = self.text().strip()
        # Replace comma-as-decimal with dot, but only when it looks like
        # a decimal separator (i.e. not followed by 3 digits = thousands sep)
        import re
        # Normalise: if single comma present and not thousands-style, treat as decimal
        text = _normalise_decimal(text)
        try:
            return parse_value(text)
        except Exception:
            return default


def _is_plain_epoch(text: str) -> bool:
    """Return True if text looks like a bare Unix epoch number (no date separators)."""
    t = text.strip()
    if not t:
        return False
    try:
        float(t)
        # If fromisoformat also accepts it, it's not a plain number
        from datetime import datetime
        try:
            datetime.fromisoformat(t)
            return False   # it parsed as ISO → not a bare epoch
        except ValueError:
            return True    # float but not ISO → bare epoch
    except ValueError:
        return False


def _parse_wallclock_input(text: str, epoch_local: bool = False) -> str:
    """Parse a wall-clock string and return a normalised ISO 8601 string.

    Accepts:
      ISO 8601   — "2024-03-15T14:23:00", "2024-03-15 14:23:00.000"
      Unix epoch — a plain integer or float
                   e.g. 1713450000  or  1713450000.123
                   epoch_local=False (default) → treat as UTC
                   epoch_local=True            → treat as local wall-clock time,
                                                 convert to UTC for storage

    Raises ValueError if neither format succeeds.
    """
    from datetime import datetime, timezone
    text = text.strip()
    if not text:
        raise ValueError("empty input")
    # Try ISO 8601 first (handles most datetime strings including with T separator)
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        pass
    # Try as a plain Unix epoch number
    try:
        epoch = float(text)
        if epoch_local:
            # Interpret as a standard UTC epoch, then express in local timezone.
            # The stored ISO string will carry the local tz offset (e.g. +02:00),
            # so the real-time axis displays the local wall-clock time correctly.
            local_tz = datetime.now().astimezone().tzinfo
            dt = datetime.fromtimestamp(epoch, tz=local_tz)
        else:
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.isoformat()
    except (ValueError, OSError, OverflowError, TypeError):
        pass
    raise ValueError(
        f"Cannot parse '{text}' as ISO 8601 or Unix epoch.\n"
        "ISO examples: 2024-03-15T14:23:00  or  2024-03-15 14:23:00.000\n"
        "Epoch example: 1713450000  or  1713450000.123"
    )


def _normalise_decimal(s: str) -> str:
    """Convert locale decimal comma to dot for parse_value."""
    import re
    # Already has a dot → leave as-is (parse_value handles it)
    if '.' in s:
        return s
    # Replace comma that looks like decimal separator:
    # "1,25" → "1.25"  but "1,250,000" → leave (rare in our context)
    # Simple rule: replace the LAST comma if there's only one
    parts = s.split(',')
    if len(parts) == 2:
        return parts[0] + '.' + parts[1]
    return s


_COL_DRAG_MIME = "application/x-tl-import-col"


# ── Drag handle for ColumnConfigRow ──────────────────────────────────────────

class _ColDragHandle(QLabel):
    """Small grip icon that initiates a QDrag carrying the column name."""

    def __init__(self, col_name: str, parent=None):
        super().__init__("⠿", parent)
        self._col_name = col_name
        self._drag_start: QPoint | None = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedWidth(14)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("Drag onto a group banner to reassign")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            event.accept()          # must accept so Qt delivers subsequent move events here

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            return
        if ((event.pos() - self._drag_start).manhattanLength()
                < QApplication.startDragDistance()):
            return
        # Build a small label pixmap for drag feedback
        pm = QPixmap(160, 20)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setPen(QColor("#c0c0e0"))
        p.setFont(self.font())
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignVCenter, f"  {self._col_name}")
        p.end()

        mime = QMimeData()
        mime.setData(_COL_DRAG_MIME, self._col_name.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(8, 10))
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None


# ── Column config row ─────────────────────────────────────────────────────────

class ColumnConfigRow(QWidget):
    def __init__(self, col_name: str, data: np.ndarray,
                 is_time_candidate: bool = False,
                 metadata: CsvMetadata = None,
                 col_info=None,          # ColumnInfo from parser plugin, or None
                 parent=None):
        super().__init__(parent)
        self.col_name = col_name
        self.data = data
        self._is_numeric = is_numeric_column(data)
        meta = metadata or CsvMetadata()
        # col_info overrides the global CsvMetadata for per-column defaults
        self._col_info = col_info
        # Group assignment — updated by the import dialog's grouping dialog
        self.col_group: str = col_info.group if (col_info and getattr(col_info, "group", "")) else ""
        # Drag state — row initiates drag from anywhere (no click action on the row)
        self._drag_start: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        layout.addWidget(_ColDragHandle(col_name))

        # Default-skip: plugin may mark alarm/marker columns as skip=True
        _plugin_skip = col_info.skip if col_info is not None else False

        self.chk_enable = QCheckBox()
        self.chk_enable.setChecked(
            self._is_numeric and not is_time_candidate and not _plugin_skip)
        self.chk_enable.setToolTip("Import this column as a trace")
        layout.addWidget(self.chk_enable)

        lbl = QLabel(col_name)
        lbl.setMinimumWidth(110)
        lbl.setMaximumWidth(180)
        lbl.setFont(QFont("Courier New", 9))
        # Stats shown as a tooltip rather than inline to save horizontal space
        if self._is_numeric and len(data) > 0:
            try:
                d = data.astype(float)
                valid = d[np.isfinite(d)]
                if len(valid):
                    lbl.setToolTip(
                        f"n={len(data)}  min={valid.min():.3g}  max={valid.max():.3g}")
                else:
                    lbl.setToolTip(f"n={len(data)}  (no finite values)")
            except Exception:
                lbl.setToolTip(f"n={len(data)}")
        layout.addWidget(lbl)

        _default_label = (col_info.display_name if col_info and col_info.display_name
                          else col_name)
        self.edit_label = SciLineEdit(_default_label)
        self.edit_label.setToolTip("Display label for this trace")
        self.edit_label.setMinimumWidth(90)
        self.edit_label.setMaximumWidth(140)
        layout.addWidget(self.edit_label)

        # ── Scaling ──────────────────────────────────────────────────
        self.chk_scale = QCheckBox("Scale")
        self.chk_scale.setChecked(False)
        self.chk_scale.toggled.connect(self._toggle_scaling)
        layout.addWidget(self.chk_scale)

        self.scale_widget = QWidget()
        sl = QHBoxLayout(self.scale_widget)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)

        sl.addWidget(QLabel("Gain:"))
        self.edit_gain = SciLineEdit("1")
        self.edit_gain.setFixedWidth(75)
        self.edit_gain.setToolTip(
            "Multiplier: output = raw × gain + offset\n"
            "Fractions OK: 2.5/4096  Suffixes OK: 10k")
        sl.addWidget(self.edit_gain)

        sl.addWidget(QLabel("Offset:"))
        self.edit_offset = SciLineEdit("0")
        self.edit_offset.setFixedWidth(75)
        self.edit_offset.setToolTip(
            "Additive offset after gain (in output units)\n"
            "Decimal: use '.' or ',' — both accepted")
        sl.addWidget(self.edit_offset)

        self.scale_widget.setEnabled(False)
        layout.addWidget(self.scale_widget)

        # Unit is always visible/editable — useful even without gain/offset scaling
        _default_unit = (col_info.unit if col_info and col_info.unit
                         else (meta.unit or "V"))
        layout.addWidget(QLabel("Unit:"))
        self.edit_unit = SciLineEdit(_default_unit)
        self.edit_unit.setFixedWidth(38)
        self.edit_unit.setToolTip("Physical unit label (V, A, °C, …)")
        layout.addWidget(self.edit_unit)

        layout.addStretch()
        # (Stats shown as tooltip on the column name label above)

        if not self._is_numeric:
            self.chk_enable.setChecked(False)
            self.chk_enable.setEnabled(False)
            self.chk_scale.setEnabled(False)

        # Pre-fill scaling: col_info (per-column plugin data) takes precedence
        # over the global CsvMetadata values.
        _gain   = col_info.gain   if col_info is not None else (meta.gain   or 1.0)
        _offset = col_info.offset if col_info is not None else (meta.offset or 0.0)
        if _gain != 1.0:
            self.chk_scale.setChecked(True)
            self.edit_gain.setText(str(_gain))
        if _offset != 0.0:
            self.chk_scale.setChecked(True)
            self.edit_offset.setText(str(_offset))

    # ── Row-level drag (whole row is a drag source) ───────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Don't start a drag if the click landed on an interactive child
            child = self.childAt(event.pos())
            if not isinstance(child, (QCheckBox, QLineEdit)):
                self._drag_start = event.pos()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            return
        if ((event.pos() - self._drag_start).manhattanLength()
                < QApplication.startDragDistance()):
            return
        pm = QPixmap(200, 20)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setPen(QColor("#c0c0e0"))
        p.setFont(self.font())
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignVCenter, f"  {self.col_name}")
        p.end()
        mime = QMimeData()
        mime.setData(_COL_DRAG_MIME, self.col_name.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(8, 10))
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None

    def _toggle_scaling(self, enabled: bool):
        self.scale_widget.setEnabled(enabled)


    def get_scaling(self) -> ScalingConfig:
        gain   = self.edit_gain.get_value(1.0)
        offset = self.edit_offset.get_value(0.0)
        enabled = self.chk_scale.isChecked()
        unit = self.edit_unit.text().strip() or "V"
        return ScalingConfig(
            enabled=enabled,
            use_gain_offset=True,
            gain=gain,
            offset=offset,
            unit=unit,
        )

    def apply_scale_from(self, source: "ColumnConfigRow"):
        self.chk_scale.setChecked(source.chk_scale.isChecked())
        self.edit_gain.setText(source.edit_gain.text())
        self.edit_offset.setText(source.edit_offset.text())
        self.edit_unit.setText(source.edit_unit.text())


# ── Import dialog ─────────────────────────────────────────────────────────────

class ImportDialog(QDialog):
    def __init__(self, load_result: LoadResult,
                 persistent_settings: dict = None, theme=None, parent=None):
        super().__init__(parent)
        self.load_result = load_result
        self.result_traces: List[TraceModel] = []
        self._col_rows: Dict[str, ColumnConfigRow] = {}
        self._settings = persistent_settings or {}
        self._theme = theme          # ThemeData / ThemeManager — for GroupingDialog

        self.setWindowTitle(f"Import: {load_result.filename}")
        self.setMinimumSize(900, 580)
        self.resize(1100, 660)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        meta = self.load_result.metadata

        # ── Info bar ──────────────────────────────────────────────────
        info_parts = [
            f"File: <b>{self.load_result.filename}</b>",
            f"Rows: <b>{self.load_result.n_rows}</b>",
            f"Columns: <b>{len(self.load_result.columns)}</b>",
        ]
        meta_hints = []
        if meta.sample_rate:
            meta_hints.append(f"SPS={meta.sample_rate:.4g}")
        if meta.gain is not None:
            meta_hints.append(f"Gain={meta.gain:.6g}")
        if meta.offset is not None and meta.offset != 0:
            meta_hints.append(f"Offset={meta.offset:.6g}")
        if meta.unit:
            meta_hints.append(f"Unit={meta.unit}")
        if self.load_result.parser_name:
            info_parts.append(
                f"<span style='color:#80a0ff'>🔌 Parser: "
                f"{self.load_result.parser_name}</span>")
        if meta_hints:
            info_parts.append(
                f"<span style='color:#80c080'>📋 Metadata: "
                f"{', '.join(meta_hints)}</span>")

        info = QLabel("  |  ".join(info_parts))
        info.setStyleSheet(
            "padding: 6px; background: #1a1a2e; border-radius: 4px;")
        layout.addWidget(info)

        if self.load_result.suggested_time_col:
            banner = QLabel(
                f"⏱  Time column auto-detected: "
                f"<b>{self.load_result.suggested_time_col}</b>"
                f"  — verify on the Time Base tab.")
            banner.setStyleSheet(
                "padding: 5px 10px; background: #1a3020; color: #60e090; "
                "border-left: 3px solid #40c060; border-radius: 2px;")
            layout.addWidget(banner)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Tab 1: Columns ────────────────────────────────────────────
        col_tab = QWidget()
        cl = QVBoxLayout(col_tab)

        # ── Global scale panel ────────────────────────────────────────
        # Last-used gain/offset loaded from settings; two apply buttons.
        last_gain   = self._settings.get("last_gain",   "1")
        last_offset = self._settings.get("last_offset", "0")
        last_unit   = self._settings.get("last_unit",   "V")

        scale_box = QGroupBox("Global Scaling (last used)")
        scale_box.setStyleSheet(
            "QGroupBox { border: 1px solid #3a3a5a; border-radius: 4px; "
            "margin-top: 14px; padding-top: 6px; } "
            "QGroupBox::title { color: #8080c0; subcontrol-origin: margin; "
            "left: 8px; }")
        sbl = QHBoxLayout(scale_box)
        sbl.setSpacing(8)

        sbl.addWidget(QLabel("Gain:"))
        self.edit_global_gain = SciLineEdit(str(last_gain))
        self.edit_global_gain.setFixedWidth(90)
        self.edit_global_gain.setToolTip(
            "Gain for bulk apply. Fractions OK: 2.048/4096")
        sbl.addWidget(self.edit_global_gain)

        sbl.addWidget(QLabel("Offset:"))
        self.edit_global_offset = SciLineEdit(str(last_offset))
        self.edit_global_offset.setFixedWidth(80)
        self.edit_global_offset.setToolTip("Offset added after gain")
        sbl.addWidget(self.edit_global_offset)

        sbl.addWidget(QLabel("Unit:"))
        self.edit_global_unit = SciLineEdit(str(last_unit))
        self.edit_global_unit.setFixedWidth(40)
        sbl.addWidget(self.edit_global_unit)

        sbl.addSpacing(12)
        btn_apply_all = QPushButton("Apply to ALL  (enable all)")
        btn_apply_all.setToolTip(
            "Enable ALL numeric columns and apply this gain/offset/unit to them")
        btn_apply_all.setStyleSheet(
            "background: #1a3a1a; color: #80e080; border: 1px solid #3a6a3a;")
        btn_apply_all.clicked.connect(self._global_apply_all)
        sbl.addWidget(btn_apply_all)

        btn_apply_sel = QPushButton("Apply to selected")
        btn_apply_sel.setToolTip(
            "Apply this gain/offset/unit only to already-enabled columns")
        btn_apply_sel.setStyleSheet(
            "background: #1a1a3a; color: #8080e0; border: 1px solid #3a3a6a;")
        btn_apply_sel.clicked.connect(self._global_apply_selected)
        sbl.addWidget(btn_apply_sel)

        sbl.addStretch()
        cl.addWidget(scale_box)

        # ── Selection toolbar ─────────────────────────────────────────
        tb = QHBoxLayout()
        for label, fn in [
            ("Select All",    lambda: self._select_all(True)),
            ("Select None",   lambda: self._select_all(False)),
            ("Select Numeric", self._select_numeric),
        ]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            tb.addWidget(b)
        tb.addSpacing(16)
        btn_group = QPushButton("Group…")
        btn_group.setToolTip(
            "Group columns by unit, name pattern, or enabled state.\n"
            "Assigned groups are used when traces are added to the channel panel.")
        btn_group.clicked.connect(self._open_grouping_dialog)
        tb.addWidget(btn_group)
        tb.addStretch()
        cl.addLayout(tb)

        # ── Column header row ─────────────────────────────────────────
        col_hdr = QWidget()
        col_hdr.setStyleSheet(
            "background: #12122a; border-bottom: 1px solid #2a2a4a;")
        hdr_layout = QHBoxLayout(col_hdr)
        hdr_layout.setContentsMargins(4, 2, 4, 2)
        hdr_layout.setSpacing(8)
        _hdr_style = "color: #6060a0; font-size: 9px;"
        for _txt, _w, _stretch in [
            ("✓",             20, 0),
            ("Column",       120, 0),
            ("Display Label", 90, 0),
            ("Scale",         50, 0),
            ("Gain / Offset", 160, 1),
            ("Unit",          38, 0),
        ]:
            _h = QLabel(_txt)
            _h.setStyleSheet(_hdr_style)
            if _stretch:
                _h.setMinimumWidth(_w)
                hdr_layout.addWidget(_h, 1)
            else:
                _h.setFixedWidth(_w)
                hdr_layout.addWidget(_h)
        cl.addWidget(col_hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sw = QWidget()
        self._col_scroll_layout = QVBoxLayout(sw)
        self._col_scroll_layout.setSpacing(2)

        # ── Create all ColumnConfigRow widgets (no layout placement yet) ──
        # col_group is initialised from parser-supplied group info inside
        # ColumnConfigRow.__init__ (via col_info.group).
        for col_name in self.load_result.columns:
            data     = self.load_result.columns[col_name]
            is_time  = col_name == self.load_result.suggested_time_col
            col_info = self.load_result.column_infos.get(col_name)
            row = ColumnConfigRow(col_name, data,
                                  is_time_candidate=is_time, metadata=meta,
                                  col_info=col_info)
            row.setParent(sw)
            self._col_rows[col_name] = row

        self._group_rows: Dict[str, List] = {}
        self._rebuild_column_list()

        scroll.setWidget(sw)
        cl.addWidget(scroll)
        tabs.addTab(col_tab, "Columns && Scaling")

        # ── Tab 2: Time Base ──────────────────────────────────────────
        time_tab = QWidget()
        tl = QVBoxLayout(time_tab)
        tl.setAlignment(Qt.AlignmentFlag.AlignTop)

        tg_box = QGroupBox("Time Base Configuration")
        tg = QGridLayout(tg_box)

        self.radio_sps = QRadioButton("Fixed Sample Rate")
        self.radio_dt  = QRadioButton("Fixed dt (period)")
        self.radio_time_col = QRadioButton("Use Time Column")
        bg = QButtonGroup(self)
        for r in (self.radio_sps, self.radio_dt, self.radio_time_col):
            bg.addButton(r)

        default_sps = meta.sample_rate or self._settings.get("default_sample_rate", 1000.0)
        default_dt  = meta.dt or (1.0 / default_sps if default_sps else 0.001)

        tg.addWidget(self.radio_sps, 0, 0)
        self.edit_sps = SciLineEdit(f"{default_sps:.6g}")
        self.edit_sps.setToolTip("Samples per second. Use suffix: 10k, 2.2M")
        self.edit_sps.editingFinished.connect(self._sps_changed)
        tg.addWidget(self.edit_sps, 0, 1)
        tg.addWidget(QLabel("Sa/s"), 0, 2)

        tg.addWidget(self.radio_dt, 1, 0)
        self.edit_dt = SciLineEdit(f"{default_dt:.9g}")
        self.edit_dt.setToolTip("Seconds per sample. Use suffix: 100n, 1u")
        self.edit_dt.editingFinished.connect(self._dt_changed)
        tg.addWidget(self.edit_dt, 1, 1)
        tg.addWidget(QLabel("s"), 1, 2)

        tg.addWidget(self.radio_time_col, 2, 0)
        self.combo_time_col = QComboBox()
        numeric_names = [n for n, d in self.load_result.columns.items()
                         if is_numeric_column(d)]
        self.combo_time_col.addItems(numeric_names)
        suggested = self.load_result.suggested_time_col
        if suggested and suggested in numeric_names:
            self.combo_time_col.setCurrentText(suggested)
            self.radio_time_col.setChecked(True)
        else:
            self.radio_sps.setChecked(True)
        tg.addWidget(self.combo_time_col, 2, 1)

        self.lbl_duration = QLabel()
        tg.addWidget(QLabel("Estimated duration:"), 3, 0)
        tg.addWidget(self.lbl_duration, 3, 1)
        tl.addWidget(tg_box)

        # Time offset
        tz_box = QGroupBox("Time Zero Offset")
        tzl = QHBoxLayout(tz_box)
        tzl.addWidget(QLabel("t=0 at sample #:"))
        self.edit_t0_sample = SciLineEdit("0")
        self.edit_t0_sample.setFixedWidth(80)
        self.edit_t0_sample.setToolTip(
            "Set this sample index as t=0. Points before it get negative time.\n"
            "Also set by #zerotime=N in CSV headers.")
        tzl.addWidget(self.edit_t0_sample)
        tzl.addWidget(QLabel("  or time value:"))
        self.edit_t0_time = SciLineEdit("0")
        self.edit_t0_time.setFixedWidth(90)
        self.edit_t0_time.setToolTip(
            "Subtract this time value from all time points.\n"
            "E.g. enter 0.5 to make t=0.5 the new zero.")
        tzl.addWidget(self.edit_t0_time)
        tzl.addStretch()
        tl.addWidget(tz_box)

        # Pre-fill zerotime from metadata
        if hasattr(meta, 'zerotime') and meta.zerotime is not None:
            self.edit_t0_sample.setText(str(meta.zerotime))

        for r in (self.radio_sps, self.radio_dt, self.radio_time_col):
            r.toggled.connect(self._update_duration_label)
        self.combo_time_col.currentTextChanged.connect(self._update_duration_label)
        self._update_duration_label()

        # Wall-clock anchor override
        wc_box = QGroupBox("Wall-Clock Anchor (t=0)")
        wcl = QVBoxLayout(wc_box)
        wc_row = QHBoxLayout()
        self.chk_t0_override = QCheckBox("Override t=0 wall clock")
        self.chk_t0_override.setChecked(
            self._settings.get("import_t0_override_enabled", False))
        self.chk_t0_override.setToolTip(
            "Enter an ISO 8601 datetime to use as the real-world moment\n"
            "corresponding to t=0, overriding any value from the parser.")
        wc_row.addWidget(self.chk_t0_override)
        self.edit_t0_wallclock = QLineEdit()
        self.edit_t0_wallclock.setPlaceholderText(
            "ISO: 2024-03-15T14:23:00   or   Unix epoch: 1713450000")
        self.edit_t0_wallclock.setMinimumWidth(280)
        self.edit_t0_wallclock.setToolTip(
            "ISO 8601: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD HH:MM:SS[.mmm]\n"
            "Unix epoch: plain integer or decimal seconds since 1970-01-01 00:00 UTC\n"
            "  e.g. 1713450000  or  1713450000.123\n"
            "Leave blank to keep the parser-supplied wall-clock anchor.")
        wc_row.addWidget(self.edit_t0_wallclock)
        wc_row.addStretch()
        wcl.addLayout(wc_row)

        # Epoch timezone row — shown only when the field contains a bare epoch number
        self._epoch_tz_widget = QWidget()
        epoch_tz_row = QHBoxLayout(self._epoch_tz_widget)
        epoch_tz_row.setContentsMargins(0, 0, 0, 0)
        epoch_tz_row.addSpacing(20)
        epoch_tz_row.addWidget(QLabel("If Epoch:"))
        self.rb_epoch_utc   = QRadioButton("GMT / UTC")
        self.rb_epoch_local = QRadioButton("Local time")
        self._epoch_tz_group = QButtonGroup(self)
        self._epoch_tz_group.addButton(self.rb_epoch_utc,   0)
        self._epoch_tz_group.addButton(self.rb_epoch_local, 1)
        _epoch_local_saved = self._settings.get("import_epoch_local", False)
        self.rb_epoch_local.setChecked(_epoch_local_saved)
        self.rb_epoch_utc.setChecked(not _epoch_local_saved)
        epoch_tz_row.addWidget(self.rb_epoch_utc)
        epoch_tz_row.addWidget(self.rb_epoch_local)
        epoch_tz_row.addStretch()
        self._epoch_tz_widget.setVisible(False)
        wcl.addWidget(self._epoch_tz_widget)

        _hint_parts = []
        if self.load_result.t0_wall_clock:
            _hint_parts.append(f"Parser supplied: {self.load_result.t0_wall_clock}")
        if self.load_result.source_time_format == "unix_epoch":
            _hint_parts.append("(time column was Unix epoch — already converted)")
        if _hint_parts:
            _hint = QLabel("  ".join(_hint_parts))
            _hint.setStyleSheet("color: #6060a0; font-size: 9px;")
            wcl.addWidget(_hint)
        self.edit_t0_wallclock.setEnabled(self.chk_t0_override.isChecked())
        self.chk_t0_override.toggled.connect(self.edit_t0_wallclock.setEnabled)
        self.edit_t0_wallclock.textChanged.connect(self._validate_t0_wallclock_input)
        tl.addWidget(wc_box)

        tl.addStretch()
        tabs.addTab(time_tab, "Time Base")

        # ── Import options ────────────────────────────────────────────
        opt_box = QGroupBox("Import Options")
        og = QHBoxLayout(opt_box)
        self.chk_replace = QCheckBox("Replace existing data")
        self.chk_replace.setChecked(self._settings.get("import_replace", True))
        self.chk_replace.setToolTip(
            "Clear all current traces before importing.\n"
            "Uncheck to add alongside existing traces.")
        og.addWidget(self.chk_replace)
        self.chk_reset_view = QCheckBox("Reset view after import")
        self.chk_reset_view.setChecked(self._settings.get("import_reset_view", True))
        og.addWidget(self.chk_reset_view)
        self.chk_reset_retrigger = QCheckBox("Reset retrigger to Off")
        self.chk_reset_retrigger.setChecked(
            self._settings.get("import_reset_retrigger", True))
        self.chk_reset_retrigger.setToolTip(
            "Switch the retrigger / persistence mode to Off before loading.\n"
            "Avoids averaging or persistence from a previous session being\n"
            "applied immediately to new data before you have had a chance\n"
            "to configure the trigger for the new file.")
        og.addWidget(self.chk_reset_retrigger)
        self.chk_remove_cursors = QCheckBox("Remove cursors on import")
        self.chk_remove_cursors.setChecked(
            self._settings.get("import_remove_cursors", True))
        self.chk_remove_cursors.setToolTip(
            "Clear both cursors and their readouts when import completes.")
        og.addWidget(self.chk_remove_cursors)
        self.chk_honor_skip_rows = QCheckBox("Honor parser skip-row hints")
        self.chk_honor_skip_rows.setChecked(
            self._settings.get("import_honor_skip_rows", True))
        self.chk_honor_skip_rows.setToolTip(
            "If the parser plugin marks certain rows to skip (e.g. repeated\n"
            "headers between segments), they are dropped during file load.\n"
            "Change takes effect on the next import.")
        og.addWidget(self.chk_honor_skip_rows)
        og.addStretch()
        layout.addWidget(opt_box)

        # ── Buttons ───────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_ok = QPushButton("Import")
        btn_ok.setDefault(True)
        btn_ok.setStyleSheet(
            "background: #2060c0; color: white; padding: 6px 20px; "
            "font-weight: bold;")
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self._do_import)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def _validate_t0_wallclock_input(self, text: str):
        """Live red-highlight when the wall-clock override field can't be parsed."""
        text = text.strip()
        if not text:
            self.edit_t0_wallclock.setStyleSheet("")
            self._epoch_tz_widget.setVisible(False)
            return
        self._epoch_tz_widget.setVisible(_is_plain_epoch(text))
        try:
            _parse_wallclock_input(text)
            self.edit_t0_wallclock.setStyleSheet("")
        except ValueError:
            self.edit_t0_wallclock.setStyleSheet("background: #3a1010;")

    def _sps_changed(self):
        try:
            sps = self.edit_sps.get_value(0)
            if sps > 0:
                dt = 1.0 / sps
                self.edit_dt.setText(f"{dt:.9g}")
                self._update_duration_label()
        except Exception:
            pass

    def _dt_changed(self):
        try:
            dt = self.edit_dt.get_value(0)
            if dt > 0:
                sps = 1.0 / dt
                self.edit_sps.setText(f"{sps:.6g}")
                self._update_duration_label()
        except Exception:
            pass

    def _update_duration_label(self):
        n = self.load_result.n_rows
        if self.radio_time_col.isChecked():
            col = self.combo_time_col.currentText()
            arr = self.load_result.columns.get(col)
            if arr is not None and is_numeric_column(arr) and len(arr) > 1:
                dur = float(arr[-1]) - float(arr[0])
            else:
                dur = 0.0
        else:
            try:
                dt = self.edit_dt.get_value(0)
                dur = n * dt if dt > 0 else 0.0
            except Exception:
                dur = 0.0
        self.lbl_duration.setText(f"{_fmt_duration(dur)}  ({n} samples)")

    def _select_all(self, state):
        for row in self._col_rows.values():
            if row.chk_enable.isEnabled():
                row.chk_enable.setChecked(state)

    def _select_numeric(self):
        for row in self._col_rows.values():
            row.chk_enable.setChecked(
                row._is_numeric and row.chk_enable.isEnabled())

    def _apply_scale_to_all(self):
        source = next((r for r in self._col_rows.values()
                       if r.chk_enable.isChecked() and r.chk_scale.isChecked()),
                      None)
        if not source:
            QMessageBox.information(self, "Apply Scale",
                "Enable scaling on at least one selected column first.")
            return
        for row in self._col_rows.values():
            if row.chk_enable.isChecked() and row is not source:
                row.apply_scale_from(source)

    def _global_apply_all(self):
        """Enable all numeric columns and apply global gain/offset/unit."""
        gain   = self.edit_global_gain.text().strip()
        offset = self.edit_global_offset.text().strip()
        unit   = self.edit_global_unit.text().strip() or "V"
        for row in self._col_rows.values():
            if not row._is_numeric:
                continue
            row.chk_enable.setChecked(True)
            row.chk_scale.setChecked(True)
            row.edit_gain.setText(gain)
            row.edit_offset.setText(offset)
            row.edit_unit.setText(unit)

    def _global_apply_selected(self):
        """Apply global gain/offset/unit only to already-enabled columns."""
        gain   = self.edit_global_gain.text().strip()
        offset = self.edit_global_offset.text().strip()
        unit   = self.edit_global_unit.text().strip() or "V"
        for row in self._col_rows.values():
            if not row.chk_enable.isChecked():
                continue
            row.chk_scale.setChecked(True)
            row.edit_gain.setText(gain)
            row.edit_offset.setText(offset)
            row.edit_unit.setText(unit)

    # ── Column list rebuild ───────────────────────────────────────────────────

    def _on_col_dropped_on_group(self, col_name: str, group_name: str):
        """Called when a ColumnConfigRow drag is dropped onto a group header."""
        row = self._col_rows.get(col_name)
        if row is None:
            return
        row.col_group = "" if group_name == "__ungrouped__" else group_name
        self._rebuild_column_list()

    def _rebuild_column_list(self):
        """Rebuild the scroll-area column list to reflect current row.col_group values.
        Existing ColumnConfigRow widgets are reused in-place — all user edits survive.
        Called once at construction and again after every grouping operation."""
        sl = self._col_scroll_layout

        # Remove everything currently in the layout (headers, dividers, rows).
        # setParent(None) detaches each widget without deleting it.
        while sl.count():
            item = sl.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        # Re-group: follow load_result.columns order so the original file order
        # is preserved within each group.
        groups_order: List[str] = []
        groups_cols: Dict[str, List[str]] = {}
        ungrouped: List[str] = []
        for col_name in self.load_result.columns:
            row = self._col_rows.get(col_name)
            if row is None:
                continue
            grp = row.col_group
            if grp:
                if grp not in groups_cols:
                    groups_order.append(grp)
                    groups_cols[grp] = []
                groups_cols[grp].append(col_name)
            else:
                ungrouped.append(col_name)

        self._group_rows = {}

        if groups_order:
            for grp in groups_order:
                self._group_rows[grp] = []
                sl.addWidget(_ImportGroupHeader(
                    grp, self._group_rows[grp],
                    on_drop=self._on_col_dropped_on_group,
                    theme=self._theme))
                for col_name in groups_cols[grp]:
                    row = self._col_rows[col_name]
                    self._group_rows[grp].append(row)
                    sl.addWidget(row)
                    row.show()
            if ungrouped:
                self._group_rows["__ungrouped__"] = []
                sl.addWidget(_ImportGroupHeader(
                    "Ungrouped", self._group_rows["__ungrouped__"],
                    on_drop=self._on_col_dropped_on_group))
                for col_name in ungrouped:
                    row = self._col_rows[col_name]
                    self._group_rows["__ungrouped__"].append(row)
                    sl.addWidget(row)
                    row.show()
        else:
            for i, col_name in enumerate(self.load_result.columns):
                row = self._col_rows.get(col_name)
                if row is None:
                    continue
                if i > 0 and i % 5 == 0:
                    line = QFrame()
                    line.setFrameShape(QFrame.Shape.HLine)
                    line.setStyleSheet("color: #333;")
                    sl.addWidget(line)
                sl.addWidget(row)
                row.show()

        sl.addStretch()

    # ── Grouping ──────────────────────────────────────────────────────────────

    def _open_grouping_dialog(self):
        existing = {row.col_group for row in self._col_rows.values() if row.col_group}
        dlg = GroupingDialog(existing_group_names=existing,
                             theme=self._theme, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        method, pattern, create_inside, custom_name = dlg.get_config()
        if method == "unit":
            self._apply_import_group_by_unit(create_inside, custom_name)
        elif method == "pattern":
            if not pattern:
                return
            self._apply_import_group_by_pattern(pattern, create_inside, custom_name)
        elif method == "enabled":
            self._apply_import_group_enabled(custom_name)

    def _unique_import_group_name(self, base: str, allocated: set = None) -> str:
        existing = {row.col_group for row in self._col_rows.values() if row.col_group}
        if allocated:
            existing |= allocated
        if base not in existing:
            return base
        for i in range(1, 1000):
            candidate = f"{base}_{i:03d}"
            if candidate not in existing:
                return candidate
        return base

    def _apply_import_group_by_unit(self, create_inside: bool, custom_name: str = ""):
        rows = list(self._col_rows.values())
        allocated: set = set()

        def _alloc(base: str) -> str:
            name = self._unique_import_group_name(base, allocated)
            allocated.add(name)
            return name

        if create_inside:
            group_units: Dict[str, set] = {}
            for row in rows:
                g = row.col_group or "__ungrouped__"
                unit = row.edit_unit.text().strip() or "Other"
                group_units.setdefault(g, set()).add(unit)
            target_map: Dict[tuple, str] = {}
            for old_g, units in group_units.items():
                if len(units) <= 1:
                    continue
                for unit in units:
                    suffix = f"{custom_name}_{unit}" if custom_name else unit
                    base = suffix if old_g == "__ungrouped__" else f"{old_g}_{suffix}"
                    target_map[(old_g, unit)] = _alloc(base)
            for row in rows:
                key = (row.col_group or "__ungrouped__",
                       row.edit_unit.text().strip() or "Other")
                if key in target_map:
                    row.col_group = target_map[key]
        else:
            unit_target: Dict[str, str] = {}
            for row in rows:
                unit = row.edit_unit.text().strip() or "Other"
                if unit not in unit_target:
                    base = f"{custom_name}_{unit}" if custom_name else unit
                    unit_target[unit] = _alloc(base)
            for row in rows:
                row.col_group = unit_target[row.edit_unit.text().strip() or "Other"]
        self._rebuild_column_list()

    def _apply_import_group_by_pattern(self, pattern: str, create_inside: bool,
                                        custom_name: str = ""):
        import fnmatch as _fnmatch
        pat_lower = pattern.lower()
        name_repr = pattern.replace("*", "(ALL)")
        rows = list(self._col_rows.values())
        allocated: set = set()

        def _alloc(base: str) -> str:
            name = self._unique_import_group_name(base, allocated)
            allocated.add(name)
            return name

        def _matches(row) -> bool:
            label = (row.edit_label.text().strip() or row.col_name).lower()
            return _fnmatch.fnmatch(label, pat_lower)

        if create_inside:
            group_has_nonmatch: Dict[str, bool] = {}
            for row in rows:
                g = row.col_group or "__ungrouped__"
                if not _matches(row):
                    group_has_nonmatch[g] = True
            group_target: Dict[str, str] = {}
            for g, _ in group_has_nonmatch.items():
                suffix = custom_name or name_repr
                base = suffix if g == "__ungrouped__" else f"{g}_{suffix}"
                group_target[g] = _alloc(base)
            for row in rows:
                if not _matches(row):
                    continue
                old_g = row.col_group or "__ungrouped__"
                if old_g in group_target:
                    row.col_group = group_target[old_g]
        else:
            base = custom_name or f"Group_{name_repr}"
            group_name = _alloc(base)
            for row in rows:
                if _matches(row):
                    row.col_group = group_name
        self._rebuild_column_list()

    def _apply_import_group_enabled(self, custom_name: str = ""):
        base = custom_name or "Enabled"
        group_name = self._unique_import_group_name(base)
        for row in self._col_rows.values():
            if row.chk_enable.isChecked():
                row.col_group = group_name
        self._rebuild_column_list()

    def _do_import(self):
        use_time_col = self.radio_time_col.isChecked()
        time_col_name = self.combo_time_col.currentText() if use_time_col else None

        sps = self.edit_sps.get_value(1000.0)
        dt  = self.edit_dt.get_value(0.001)
        if sps <= 0:
            sps = 1.0 / dt if dt > 0 else 1000.0
        if dt <= 0:
            dt = 1.0 / sps if sps > 0 else 0.001

        time_data = None
        if use_time_col and time_col_name:
            time_data = self.load_result.columns.get(time_col_name)

        # When a time column is present, derive sps/dt from the actual sample
        # spacing in that column.  The spin-box values are meaningless for
        # FFT frequency scaling when real timestamps are available.
        if time_data is not None and len(time_data) >= 2:
            _td = time_data[:min(500, len(time_data))].astype(float)
            _dts = np.diff(_td)
            _pos = _dts[_dts > 0]
            if len(_pos):
                dt  = float(np.median(_pos))
                sps = 1.0 / dt

        # Time zero offset
        t0_sample = int(self.edit_t0_sample.get_value(0))
        t0_time   = self.edit_t0_time.get_value(0.0)

        # Wall-clock anchor override (blank string = no override)
        _wc_override = ""
        if self.chk_t0_override.isChecked():
            _raw_wc = self.edit_t0_wallclock.text().strip()
            if _raw_wc:
                _epoch_local = (self._epoch_tz_widget.isVisible() and
                                self.rb_epoch_local.isChecked())
                try:
                    _wc_override = _parse_wallclock_input(_raw_wc, epoch_local=_epoch_local)
                except ValueError as e:
                    QMessageBox.warning(self, "Invalid Wall-Clock Value", str(e))
                    return

        traces = []
        for col_name, row in self._col_rows.items():
            if not row.chk_enable.isChecked():
                continue
            if col_name == time_col_name:
                continue

            raw = self.load_result.columns[col_name].copy()
            scaling = row.get_scaling()

            # Build time axis with zero offset applied
            td = None
            if time_data is not None:
                td = time_data.copy().astype(float)
                if t0_sample > 0:
                    if 0 < t0_sample < len(td):
                        td = td - td[t0_sample]
                    else:
                        td = td - td[0]
                elif t0_time != 0.0:
                    td = td - t0_time

            # Trim raw_data and time_data to the valid range declared by the
            # parser via #trace_data_range= headers.  This strips the leading/
            # trailing empty-cell padding that the exporter writes when traces
            # have different time extents, restoring the original array length.
            _dr = self.load_result.trace_data_ranges.get(col_name)
            if _dr is not None:
                r0_0 = _dr[0] - 1        # 0-based inclusive start
                r1_0 = _dr[1]             # 0-based exclusive end
                raw = raw[r0_0:r1_0]
                if td is not None:
                    td = td[r0_0:r1_0]

            col_info = self.load_result.column_infos.get(col_name)
            _trace_name = row.edit_label.text().strip() or col_name
            _col_group = row.col_group

            # Per-trace sample rate from #trace_meta= takes precedence over
            # the file-level value derived from the time column / UI spinboxes.
            _ci_sps = col_info.sample_rate if (col_info and col_info.sample_rate) else None
            _trace_sps = _ci_sps if _ci_sps else sps
            _trace_dt  = (1.0 / _trace_sps) if _trace_sps > 0 else dt

            # Per-trace wall-clock anchor; falls back to file-level value.
            _trace_t0wc = (col_info.t0_wall_clock
                           if (col_info and col_info.t0_wall_clock)
                           else self.load_result.t0_wall_clock)

            # Per-trace segment settings (from #trace_settings= headers)
            _ts_seg = (self.load_result.trace_segment_settings.get(col_name)
                       or self.load_result.trace_segment_settings.get(_trace_name)
                       or {})
            trace = TraceModel(
                name=_trace_name,
                raw_data=raw,
                time_data=td,
                sample_rate=_trace_sps,
                dt=_trace_dt,
                label=row.edit_label.text().strip() or col_name,
                unit=scaling.unit,
                scaling=scaling,
                # Instrument channel metadata (preserved from file headers)
                coupling=col_info.coupling if col_info else "",
                impedance=col_info.impedance if col_info else "",
                bwlimit=col_info.bwlimit if col_info else "",
                # Source provenance — available to trace-manipulation plugins
                source_file=self.load_result.filename,
                original_col_name=col_name,
                col_group=_col_group,
                # Wall-clock time anchor: dialog override beats per-trace, beats file-level
                t0_wall_clock=(_wc_override if _wc_override else _trace_t0wc),
                source_time_format=self.load_result.source_time_format,
                # Segment metadata — per-trace if available, file-level fallback
                segments=(self.load_result.trace_segments.get(col_name)
                          or self.load_result.trace_segments.get(_trace_name)
                          or self.load_result.segments),
                primary_segment=(_ts_seg.get("primary_segment",
                                             self.load_result.primary_segment)),
                non_primary_viewmode=_ts_seg.get("non_primary_viewmode", ""),
            )

            # For sample-based time, apply t0 offset via dt-based shift
            if time_data is None and t0_sample > 0:
                trace._t0_sample_offset = t0_sample  # store for time_axis calc

            traces.append(trace)

        if not traces:
            QMessageBox.warning(self, "No Traces",
                "No columns selected for import.")
            return

        self.result_traces = traces
        self.replace_existing    = self.chk_replace.isChecked()
        self.reset_view          = self.chk_reset_view.isChecked()
        self.reset_retrigger     = self.chk_reset_retrigger.isChecked()
        self.remove_cursors      = self.chk_remove_cursors.isChecked()
        self.honor_skip_rows     = self.chk_honor_skip_rows.isChecked()
        self._settings["import_honor_skip_rows"]    = self.honor_skip_rows
        self._settings["import_t0_override_enabled"] = self.chk_t0_override.isChecked()
        self._settings["import_epoch_local"]         = self.rb_epoch_local.isChecked()
        # Persist last-used global scale values
        self._settings["last_gain"]   = self.edit_global_gain.text().strip()
        self._settings["last_offset"] = self.edit_global_offset.text().strip()
        self._settings["last_unit"]   = self.edit_global_unit.text().strip() or "V"
        # Persist sample rate so next file with no time column uses the same default
        if not use_time_col:
            self._settings["default_sample_rate"] = sps
        self.accept()


class _ImportGroupHeader(QWidget):
    """Styled group header bar: fold toggle, ✓ All / ✗ None, and drop target.

    Accepts drops of _COL_DRAG_MIME (column name bytes).  When a column is
    dropped, on_drop(col_name, group_name) is called so the ImportDialog can
    update row.col_group and rebuild the list.

    Colours are derived from the active theme so the header looks correct in
    every theme.  Falls back to neutral values when theme=None.
    """

    def __init__(self, group_name: str, rows_list: list,
                 on_drop=None, theme=None, parent=None):
        super().__init__(parent)
        self._group_name = group_name
        self._rows_list  = rows_list
        self._on_drop    = on_drop
        self._collapsed  = False

        # Derive colours from theme; fall back to mid-neutral values that work
        # reasonably on any background rather than hardcoded dark-only colours.
        _pv = (lambda k, d: theme.pv(k, d)) if theme else (lambda _, d: d)
        bg      = _pv("bg_panel",  "#1e1e2e")
        border  = _pv("border",    "#3a3a5a")
        accent  = _pv("accent",    "#6060c0")
        text_hd = _pv("text_dim",  "#8888bb")

        self._base_style = (
            f"background: {bg}; "
            f"border-top: 1px solid {border}; border-bottom: 1px solid {border};")
        self._drop_style = (
            f"background: {bg}; "
            f"border-top: 2px solid {accent}; border-bottom: 2px solid {accent};")

        self.setFixedHeight(28)
        self.setStyleSheet(self._base_style)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 3, 4, 3)

        self._lbl = QLabel(f"▼  {group_name}")
        self._lbl.setStyleSheet(
            f"color: {accent}; font-weight: bold; font-size: 10px; "
            f"background: transparent; border: none;")
        hl.addWidget(self._lbl)
        hl.addStretch()

        btn_all  = QPushButton("✓ All")
        btn_none = QPushButton("✗ None")
        btn_all.setFixedSize(52, 18)
        btn_none.setFixedSize(52, 18)
        # Keep the All/None buttons subtly coloured but theme-neutral
        btn_all.setStyleSheet(
            f"QPushButton {{ font-size: 9px; color: {text_hd}; background: transparent; "
            f"border: 1px solid {border}; border-radius: 2px; }} "
            f"QPushButton:hover {{ color: #80e080; border-color: #408040; }}")
        btn_none.setStyleSheet(
            f"QPushButton {{ font-size: 9px; color: {text_hd}; background: transparent; "
            f"border: 1px solid {border}; border-radius: 2px; }} "
            f"QPushButton:hover {{ color: #e08080; border-color: #804040; }}")
        btn_all.clicked.connect(
            lambda: [r.chk_enable.setChecked(True)
                     for r in self._rows_list if r.chk_enable.isEnabled()])
        btn_none.clicked.connect(
            lambda: [r.chk_enable.setChecked(False)
                     for r in self._rows_list if r.chk_enable.isEnabled()])
        hl.addWidget(btn_all)
        hl.addWidget(btn_none)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._collapsed = not self._collapsed
        self._lbl.setText(f"{'▶' if self._collapsed else '▼'}  {self._group_name}")
        for r in self._rows_list:
            r.setVisible(not self._collapsed)

    # ── Drag-drop target ──────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_COL_DRAG_MIME):
            event.acceptProposedAction()
            self.setStyleSheet(self._drop_style)

    def dragLeaveEvent(self, event):
        super().dragLeaveEvent(event)
        self.setStyleSheet(self._base_style)

    def dropEvent(self, event):
        self.setStyleSheet(self._base_style)
        col_name = event.mimeData().data(_COL_DRAG_MIME).data().decode()
        if self._on_drop:
            self._on_drop(col_name, self._group_name)
        event.acceptProposedAction()


def _fmt_duration(dur: float) -> str:
    if dur <= 0: return "0 s"
    if dur < 1e-9: return f"{dur*1e12:.3g} ps"
    if dur < 1e-6: return f"{dur*1e9:.3g} ns"
    if dur < 1e-3: return f"{dur*1e6:.3g} µs"
    if dur < 1:    return f"{dur*1e3:.3g} ms"
    return f"{dur:.4g} s"
