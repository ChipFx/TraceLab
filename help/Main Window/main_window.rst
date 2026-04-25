---
short_name: main_window
long_name: Main Window
chapter: tracelab-help
chapter_long: ChipFX TraceLab Help
order: 3
keywords: [help, tracelab, main, trace, traceview, view, panel, quick, action, split, overlay, screenshot, fit, auto-scale, Y-Auto, Y auto, fft, filter, clear]
---

Main Window
===========

The Main Window houses all the elements and panels, but that's not the purpose
of this help entry.

This help-entry is about the trace-view middle panel and the quick-action bar along
the top of the viewport, just under the Menu Bar.

Trace View
----------

The Trace View panel has two inherent modes:
 * Split-lane view (default for TraceLab)
 * Overlaid view (Average Oscilloscope Behaviour)

Split-lane view is default for TraceLab, because this is the most useful view for
most of the uses for this application. It doesn't require traces to be on a 
comparable scale to be able to compare their movements in time.

But, there are various realistic scenarios where you would want to overlay the traces,
so this view mode is supported.

To change between the two use **View → Split Lanes** and **View → Overlay all Traces**.

In the Split-lane view mode, there are some additional things to make life easier:
 * You can configure the normal scroll behaviour when floating on the trace list:
    - Zoom/unzoom (default)
    - Scroll Lanes up and down (if they overflow)
 * You can press **Ctrl** or **Shift** while scrolling to perform the alternate action
    - e.g.: When you have it set to zoom/unzoom, when you press either **Ctrl** or
    **Shift** while scrolling, it will scroll the list.
 * You can change these settings in **Settings → Advanced UI → Mouse**

When zooming in the Trace View, you may notice that you zoom the time-scale (X) and not
the Y axis. This is because "Y Auto" (also found in **View → Lock Y to Auto-Scale (L)**)
is enabled. This setting forces the view to auto-scale to the min/max of the Y range within
that sub-panel. In Split-lane mode that is an auto-scale on each lane element, in
Overlay mode it scales the view considering all traces as a whole.

If you disable this mode, zooming will zoom in/out equally on X and Y at the same time.

Quick Actions
-------------

At the top of the Main Window there is the Quick Action bar.

It allows you to perform:
 * Open CSV File, see `File: Open CSV File <rst-doc://file_menu/open-csv>`_.
 * Clear all Traces, see `Channel Pannel <rst-doc://tracelab-help/channel_panel>`_.
 * Go to Split View, see above.
 * Go to Overlay View, see above.
 * Place Cursor A, see `Tool Panel <rst-doc://tracelab-help/tool_panel>`_.
 * Place Cursor B, see `Tool Panel <rst-doc://tracelab-help/tool_panel>`_.
 * Fit (F) all traces into the View Port
 * Fit X (T) all traces on the time-scale only
 * Fit Y (A) all traces' amplitude to within the view.
 * Toggle Y-Auto-Scale lock, see above.
 * Open the FFT window, see `Analysis: FFT <rst-doc://fft_window>`_.
 * Open the Filter window, see `Analysis: Filter <rst-doc://filter_window>`_.
 * Take a screenshot of the Trace Panel and Status Bar.

 Screenshots taken with the Screenshot button should create a double-size screenshot
 through interpolation, and open a save-as window to save it as PNG.

Manually set the viewport
-------------------------

At the bottom of the Main Window, under the Status Bar, there is a set of entry fields
that you can use to force the viewport to a range on the X and Y axes.
Simply enter a minimum and a maximum in the X and Y sections and click Apply.
