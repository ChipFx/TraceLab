# TraceLab 🔭

A modular oscilloscope data viewer built with Python, PyQt6, and PyQtGraph.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Generate test data
python generate_test_data.py

# Launch
python main.py
```

---

## Features

### Data Import
- **CSV import** with auto-delimiter detection
- Per-column **scaling** (e.g. ADC 0–4095 → -1.25V to +1.25V)
- Post-scale multiplier (e.g. 0.25 A/V for current shunts)
- **Time column** support or fixed **sample rate / dt** entry
- "Apply to all" scaling for multi-channel imports
- Select/deselect individual columns at import

### Display
- **Split lane mode** — LeCroy MAUI style, each trace in its own lane
- **Overlay mode** — all traces on one plot
- Linked X-axes (pan/zoom all lanes together)
- Up to ~50k display points per trace with min-max decimation envelope
- Pan & zoom with mouse wheel + drag; keyboard shortcuts: `F` fit, `+`/`-` zoom

### Channels
- Toggle visibility per trace (channel on/off)
- Change trace color via color picker or channel panel button
- Remove traces via right-click context menu
- Scope-style default colors (yellow C1, cyan C2, pink C3, green C4…)

### Cursors
- **Cursor A** (yellow dashed) and **Cursor B** (cyan dashed)
- Drag cursors to any position across all lanes
- Readout panel shows: time at each cursor, **ΔT**, **1/ΔT (frequency)**
- Per-trace interpolated values at each cursor
- Export cursor measurements as CSV

### Analysis
- **FFT** dialog: full data or windowed view, selectable window function
  (Rectangular, Hanning, Hamming, Blackman), log-frequency display
- **Filters**: Butterworth lowpass, highpass, bandpass (via scipy)
  — applied with zero-phase `filtfilt`

### Export
- Export **visible/windowed data** as CSV
- **High-res screenshot** (2× pixel scaling) as PNG

### Themes
- Dark (default), Light, Green Phosphor (R&S style)
- Switch via View menu; persists across sessions

---

## Plugin System

Drop `.py` files into the `plugins/` folder — they appear in the **Plugins** menu automatically. Use **Plugins → Reload Plugins** without restarting.

### Included plugins
| Plugin | Type | Description |
|--------|------|-------------|
| `rms_stats.py` | analyzer | RMS, mean, std dev, peak-to-peak for all visible traces |
| `remove_dc.py` | processor | Subtract DC offset (mean) from each trace |

### Writing a processor plugin (30-second example)

```python
# plugins/my_derivative.py
PLUGIN_NAME = "Derivative (dY/dT)"
PLUGIN_DESCRIPTION = "dY/dT of each visible trace"
PLUGIN_VERSION = "1.0"
PLUGIN_TYPE = "processor"

import numpy as np

def run(traces, context):
    for trace in traces:
        if not trace.visible:
            continue
        trace.raw_data = np.gradient(trace.processed_data, trace.dt)
        trace.scaling.enabled = False
        trace._invalidate_cache()
    return traces
```

See `plugins/README.md` for full documentation.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+O` | Open CSV |
| `Ctrl+F` | FFT |
| `Ctrl+P` | Screenshot |
| `Ctrl+Q` | Quit |
| `F` | Zoom to fit |
| `+` | Zoom in (X) |
| `-` | Zoom out (X) |

---

## Project Structure

```
oscilloscope/
├── main.py                  # Entry point
├── settings.json            # User settings (auto-created)
├── requirements.txt
├── generate_test_data.py    # Test data generator
├── core/
│   ├── main_window.py       # Main application window
│   ├── scope_plot_widget.py # Plot area (lanes + overlay)
│   ├── trace_model.py       # Data model per trace
│   ├── data_loader.py       # CSV loading
│   ├── import_dialog.py     # Import configuration dialog
│   ├── channel_panel.py     # Left sidebar
│   ├── cursor_panel.py      # Right sidebar (cursor readouts)
│   ├── fft_dialog.py        # FFT analysis window
│   ├── filter_dialog.py     # Filter dialog
│   ├── theme_manager.py     # Dark/light themes + stylesheets
│   └── plugin_manager.py   # Plugin discovery + execution
└── plugins/
    ├── README.md            # Plugin authoring guide
    ├── rms_stats.py         # Example: analyzer plugin
    └── remove_dc.py         # Example: processor plugin
```

---

## Roadmap / Wishlist

- [ ] Playback mode (scroll through data at configurable speed)
- [ ] Infinite cursors with measurement window
- [ ] Linear and sin(x)/x interpolation
- [ ] Import R&S, LeCroy, Keysight binary formats
- [ ] Per-trace labels anchored to time positions
- [ ] Branding/logo overlay on screenshots
- [ ] Plugin type: `importer` and `exporter`

---

## Settings File (`settings.json`)

```json
{
  "theme": "dark",
  "trace_colors": ["#F0C040", "#40C0F0", ...],
  "color_overrides": {},
  "max_display_points": 50000,
  "default_sample_rate": 1000.0
}
```

Edit this file to change default trace colors, or use the color pickers in-app.
