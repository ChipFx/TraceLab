"""
TraceLab Plugin: Remove DC Offset
Type: processor

Subtracts the mean value from each selected trace's segments independently,
removing DC offset.  Operating per-segment is correct because each segment
may have been captured under different conditions (recalibration, temperature
drift, etc.) and should be demeaned on its own baseline.
"""

PLUGIN_NAME = "Remove DC Offset"
PLUGIN_DESCRIPTION = "Subtract mean value (DC offset) from all visible traces"
PLUGIN_VERSION = "1.0"
PLUGIN_TYPE = "processor"

import numpy as np


def run(traces, context):
    """
    traces: list of TraceModel (modified in-place)
    Returns the modified list.
    """
    for trace in traces:
        if not trace.visible:
            continue
        for seg in trace.segments:
            # Work in scaled (physical-unit) space so the mean is meaningful
            # regardless of ADC scaling.  The demeaned result becomes the new
            # raw data; scaling is then disabled (baked in).
            y = trace.segment_processed(seg)
            seg.data = y - float(np.mean(y))
            seg.filtered_data = None   # stale after raw data change
        trace.scaling.enabled = False

    return traces
