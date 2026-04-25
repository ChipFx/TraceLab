---
short_name: tool_panel
long_name: Tool Panel
chapter: tracelab-help
chapter_long: ChipFX TraceLab Help
order: 4
keywords: [tool, tools, cursor, cursors, trigger, retrigger, t=0, rising, falling, both, level, auto-update]
---

Tool Panel
===========

The Tool Panel is to the right of the window and allows the placement of
cursors, inspecting (linearly interpolated) values at the cursor postition, triggering
into the signals in various methods. Some settings in the Tool Panel affect the
retriggering. Retriggering is a group name created for the Acquisition settings.
See `Acquire Menu <rst-doc://menu_bar/acquire_menu>`_. for more information.

There is also the "Jump to t=0" button, which sets t0 on the timeline as the middle
of the X axis in the Trace Panel.

Cursors
-------

There are two cursors in the application. By clicking "Place A" you place the A cursor 
in the middle of the Trace Panel. By clicking "Place B" you place the B cursor just right 
of the middle of the Trace Panel.

You can also set t0 at the A cursor with the "Set t=0 @ A" button.

To remove the cursors from view, click the "Remove" button in the Tool Panel.

When you have placed one cursor, you can see the timepoint at which it is placed.

When you have placed two cursors is also shows the time-distance between them, and
indicates the frequency of the interval through f=1/dt.

For each cursor placed, if it touches data on one or more traces, the "measurements" are
shown in the measurements list below the Cursor tool frame. These measurements are
linear interpolations of the value of the trace, if the cursor is between two values.
The interpolation of the value is always linear, and not influenced by the channel
interpolation settings.

Trigger and Re-trigger
----------------------

In the Trigger panel you can set a trigger level (which accepts p, n, u, m, k, M modifiers)
and set the trigger direction, Rising, Falling or Both. The trigger level is searched 
based on the interpolated level. That means, if you set it to 10V, and there is one sample at
9.99V and the next is at 10.01V, the trigger finder sees this as a valid rising edge, and
sets the trigger to about halfway between the samples.

Finding a trigger can be done forward (Fwd), backward (Bwd) and starting at eithher the
signal edge (Edge) or from t=0 (t=0).

You also have the option of Placing the A cursor at the trigger event (enabled by default).
There is the option of Zooming to the trigger context (enabled by default), and to set t=0 at
the trigger, like a triggered oscilloscope would do.

It bears noting that "Zoom to Trigger Context" does not actually zoom, but leaves your zoom
setting as it, while placing your view centred around the trigger moment, providing there is
enough trace data left and right to fill the screen when the event is centred.

There is one last setting in the trigger menu, which is "Auto-update retrigger". When a 
Retrigger mode is selected from the `Acquire Menu <rst-doc://menu_bar/acquire_menu>`_,
having the Auto-update enabled means that for each zoom or pan action the view updates
the underlying retrigger data and refreshes the view.

When you are triggering, the "Find Trigger" button always finds the first trigger that
qualifies on the data as it is set in the memory. The "Next" (or "Prev" when going 
backward) button jumps to the next up valid trigger.

If you set "Set t=0" and set "From t=0" as the search mode, then the "Find Trigger"
and "Next" (or "Prev") will pretty much do the exact same thing, because every trigger
you find sets t=0 one step onward, and then "Find Trigger" will proceed from the new
t=0 point, just like "Next" (or "Prev") would.
