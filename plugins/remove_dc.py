"""
TraceLab Plugin: Remove DC Offset
Type: processor

Subtracts the mean value from each selected trace, removing DC offset.
"""

PLUGIN_NAME = "Remove DC Offset"
PLUGIN_DESCRIPTION = "Subtract mean value (DC offset) from all visible traces"
PLUGIN_VERSION = "1.0"
PLUGIN_TYPE = "processor"

import numpy as np


def run(traces, context):
    """
    traces: list of TraceModel (will be modified in-place)
    Returns the modified list.
    """
    for trace in traces:
        if not trace.visible:
            continue
        mean = np.mean(trace.processed_data)
        trace.raw_data = trace.processed_data - mean
        trace.scaling.enabled = False
        trace._invalidate_cache()

    return traces
