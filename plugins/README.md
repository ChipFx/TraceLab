# TraceLab Plugin Guide

Drop `.py` files into this folder — they appear automatically in the **Plugins** menu.
Use **Plugins → Reload Plugins** to pick up changes without restarting.

---

## Required attributes

Every plugin file must define these module-level variables and a `run()` function:

```python
PLUGIN_NAME        = "My Plugin"           # Menu label
PLUGIN_DESCRIPTION = "What it does"        # Tooltip / status bar text
PLUGIN_VERSION     = "1.0"
PLUGIN_TYPE        = "processor"           # See types below
```

---

## Plugin types

### `processor`
Modifies trace data in-place. Receives trace copies; return the modified list.

```python
PLUGIN_TYPE = "processor"

import numpy as np

def run(traces, context):
    for trace in traces:
        if not trace.visible:
            continue
        # Example: invert all visible traces
        trace.raw_data = -trace.processed_data
        trace.scaling.enabled = False
        trace._invalidate_cache()
    return traces
```

### `analyzer`
Shows its own window/dialog. Return `None`.

```python
PLUGIN_TYPE = "analyzer"

def run(traces, context):
    parent = context.get("parent_window")
    # ... open a QDialog, show results, etc.
    return None
```

---

## `context` dictionary keys

| Key | Type | Description |
|-----|------|-------------|
| `view_range` | `(float, float)` | Current X axis range `(t_start, t_end)` |
| `sample_rate` | `float` | Sample rate of first trace |
| `parent_window` | `QMainWindow` | Parent widget for dialogs |

---

## `TraceModel` useful properties

```python
trace.name           # Original column name
trace.label          # Display label (editable)
trace.visible        # bool
trace.color          # HTML color string e.g. "#F0C040"
trace.raw_data       # numpy array — original/unscaled
trace.processed_data # numpy array — after scaling (read-only property)
trace.time_axis      # numpy array — time values in seconds
trace.sample_rate    # float — samples per second
trace.dt             # float — seconds per sample
trace.n_samples      # int

# To replace data:
trace.raw_data = new_numpy_array
trace.scaling.enabled = False   # disable scaling after replacement
trace._invalidate_cache()       # must call to refresh processed_data
```

---

## Example: Derivative plugin

```python
PLUGIN_NAME = "Derivative (dY/dT)"
PLUGIN_DESCRIPTION = "Compute derivative of each visible trace"
PLUGIN_VERSION = "1.0"
PLUGIN_TYPE = "processor"

import numpy as np

def run(traces, context):
    for trace in traces:
        if not trace.visible:
            continue
        y = trace.processed_data
        dt = trace.dt
        dy = np.gradient(y, dt)
        trace.raw_data = dy
        trace.scaling.enabled = False
        trace._invalidate_cache()
    return traces
```

Save this as `plugins/derivative.py` and it appears in the menu immediately after reload.
