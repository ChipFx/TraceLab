---
short_name: status_bar
long_name: Status Bar
chapter: tracelab-help
chapter_long: ChipFX TraceLab Help
order: 5
keywords: [status, channel, panel, block, aperiodic, periodicity, lin, cub, sinc, div, time base, extrap]
---

Status Bar
===========

The status bar shows the current status for all channels currently being shown. To show or hide
Channel see `Channel Panel <rst-doc://tracelab-help/channel_panel>`_.

Each block takes on the colour of the trace as set in the `Channel Panel <rst-doc://tracelab-help/channel_panel>`_.

The channel block shows the following information:
 * The Channel name in contracting bold
 * The Channel Y-scale per div
 * A badge in the upper-right corner for the interpolation setting:
    - "LIN" in normal shading for linear interpolation
    - "CUB" in purple accent for cubic spline interpolation
    - "SINC" in red accent for sinc() (sin(x)/x) interpolation
 * A badge, if applicable, in the lower right corner in orange accent showing "Aperiodic" for signals
   that were not detected to be periodic when loaded.
 * A badge, if applicable, in the lower left corner in blue accent showing "Extrap" for signals which
   have extrapolated data resulting from the retrigger system at the edges of their regular data.

For more information about interpolation see `View: Interpolation <rst-doc://view_menu/interpolation>`_.

For more information about retriggering see `Acquire Menu <rst-doc://menu_bar/acquire_menu>`_.

To change a channel's interpolation setting, you can click the channel's block and it will cycle
through the settings: **Linear → Cubic → Sinc → Linear**.

You can also change the settings in the `Channel Panel <rst-doc://tracelab-help/channel_panel>`_. and
through various actions on the `Menu Bar <rst-doc://menu_bar>`_.

Hovering over the Channel block will show you the channel related information more clearly
in a standard tooltip, and if the signal was detected as periodic, it will indicate the period interval.

For more information about the periodicity estimation see `Acquire Menu <rst-doc://menu_bar/acquire_menu>`_.

To move the Status Bar when there are more Channels active than fit in a single view, you can:
 * Hover the mouse pointer over the bar and scroll.
 * Use the mouse's left/right scroll wheel click, if it has them, to jump by 1 block.
 * Use the left/right arrow keys to move the bar by 1 block, depending on the settings in
   **Settings → Advanced UI → Keyboard**
