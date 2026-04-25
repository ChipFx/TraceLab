---
short_name: channel_panel
long_name: Channel Panel
chapter: tracelab-help
chapter_long: ChipFX TraceLab Help
order: 2
keywords: [channel, color, colour, lin, cub, sinc, group, trace, delete, enable, disable]
---

Channel Panel
===========

The channel panel, on the left, lets you enable and disable any of the loaded
channels, change their colouring and re-order them. You may also delete traces
as well as rename them and group them.

At the bottom of the Channel Panel you can enable and disable all channels and
set their interpolation.
As opposed to the **View → Interpolation** setting, the buttons on the bottom
of the Channel Panel apply only on already loaded channels and will not be
remembered at application close.

Enabling/Disabling
------------------

To enable or disbale the channel, simply click its name.

When a channel is enabled you will see a tick colour block between the
channel colour block and the channel name. This tick is normally pure blue
on the Dark and Light theme and a darker blue on the Print theme.

You can also use the "All" and "None" button to enable or disable all channels
at once.

Changing colour
---------------

To change the channel colour of a channel you can click the coloured box at the
left of the channel, you will get a pop-up window with standard colour selection
menu elements.

If you want to re-use certain colour sets a lot during various analysis tasks
you may want to create or edit a theme.
For more about themes see `Settings: Themes <rst-doc://settings_menu/edit_theme>`_.

Changing order
--------------

You can simply change the channel order in the Channel Panel by dragging a
channel to a new spot in the list (click-and-hold and move the pointer).

When you change the order of the channels in the Channel Panel, all the major
elements in the main window will follow suit. The Status Bar at the bottom will 
also update the channel block order, and the measurement panel will also
update the channel order in its list view if you have a cursor active.

Interpolation Mode
------------------

At the bottom of the Channel Panel you can also click the three buttons
"All Lin", "All Cub", "All Sinc". These buttons set the interpolation for all
traces at once.

If you want to change the interpolation of a single channel you can do that:
 * In the Channel Panel by right-clicking and selecting "Interpolation"
 * In the **Analysis → Interpolation Per Channel** Menu.
 * By clicking the channel's status block in the Status Bar.

To learn more about what Interpolation does and how, please see `View: Interpolation <rst-doc://view_menu/interpolation>`_.

Deleting a Trace
----------------

To delete a single trace, you can right-click the Channel in the Channel Panel
and select "Remove Trace". If you want to remove them all, you can click "Clear"
next to "Open" on the top of the main window, or go to **File → Clear All Traces**

Renaming a Trace
----------------

To rename a Trace you can right-click on the Channel in the Channel Panel and select
"Rename". You will then get a text-input field to update the trace's name.

Note: The new name is the Display Name. The internal engine also remembers the
original column name for data management when exporting the data. However, when
exporting, the Display Name, if different, is also stored and recovered when
back-importing TraceLab exported CSVs.

Creating a Group
----------------

To Be Determined

Showing/Hiding a Group
----------------------

To Be Determined

Deleting a Group
----------------

To Be Determined
