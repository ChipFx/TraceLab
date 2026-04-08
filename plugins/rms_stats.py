"""
PyScope Plugin: RMS Calculator
Type: analyzer

Shows RMS, peak-to-peak, mean, and std dev for all visible traces
in the current view window.
"""

PLUGIN_NAME = "RMS & Statistics"
PLUGIN_DESCRIPTION = "Calculate RMS, peak-to-peak, mean, std dev for visible traces"
PLUGIN_VERSION = "1.0"
PLUGIN_TYPE = "analyzer"

import numpy as np


def run(traces, context):
    """
    traces: list of TraceModel objects (copies)
    context: dict with 'view_range', 'sample_rate', 'parent_window'
    """
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QTableWidget,
                                   QTableWidgetItem, QHeaderView, QLabel,
                                   QPushButton, QHBoxLayout)
    from PyQt6.QtCore import Qt

    view_range = context.get("view_range")
    parent = context.get("parent_window")

    results = []
    for trace in traces:
        if not trace.visible:
            continue

        if view_range:
            t, y = trace.windowed_data(*view_range)
        else:
            y = trace.processed_data

        if len(y) == 0:
            continue

        rms = float(np.sqrt(np.mean(y**2)))
        mean = float(np.mean(y))
        std = float(np.std(y))
        p2p = float(np.max(y) - np.min(y))
        pk = float(np.max(np.abs(y)))

        results.append({
            "name": trace.label,
            "color": trace.color,
            "rms": rms,
            "mean": mean,
            "std": std,
            "p2p": p2p,
            "peak": pk,
        })

    # Show results dialog
    dlg = QDialog(parent)
    dlg.setWindowTitle("Trace Statistics")
    dlg.resize(600, 300)
    layout = QVBoxLayout(dlg)

    scope = "current view" if view_range else "all data"
    layout.addWidget(QLabel(f"Statistics for {scope}:"))

    table = QTableWidget(len(results), 6)
    table.setHorizontalHeaderLabels(
        ["Trace", "RMS", "Mean", "Std Dev", "Peak-Peak", "Peak"])
    table.horizontalHeader().setSectionResizeMode(
        0, QHeaderView.ResizeMode.Stretch)

    for i, r in enumerate(results):
        table.setItem(i, 0, QTableWidgetItem(r["name"]))
        table.setItem(i, 1, QTableWidgetItem(f"{r['rms']:.6g}"))
        table.setItem(i, 2, QTableWidgetItem(f"{r['mean']:.6g}"))
        table.setItem(i, 3, QTableWidgetItem(f"{r['std']:.6g}"))
        table.setItem(i, 4, QTableWidgetItem(f"{r['p2p']:.6g}"))
        table.setItem(i, 5, QTableWidgetItem(f"{r['peak']:.6g}"))
        # Color the trace name cell
        item = table.item(i, 0)
        from PyQt6.QtGui import QColor
        item.setForeground(QColor(r["color"]))

    layout.addWidget(table)

    btn = QPushButton("Close")
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    dlg.exec()
    return None
