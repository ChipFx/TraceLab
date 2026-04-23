## Wishlist:
 - Primary note: ALL Settings mentioned in this whishlist should be cached in the main settings cache and written to settings.json on application close, like just about every other setting so far. All settings should also recover any tick-mark locations from the settings cache after it is loaded from disk.
 - Statusbar block order should follow trace order in trace-panel, just like cursor menu and in-plot tracelabels (in overlaped view)
 - Add new interfaces to the import menu to handle all the new meta data:
	* Add a group select/deselect system, using the groups information (if present) from the parse plugin (ParsedMetadata.groups)
	* Listen to the parse plugin's "skip rows" if setting is enabled in the dialogue (persistent setting through main(settings-cache) <-> settings.json on exit)
 - Add "manually define t=0 date-time" input or option window to the importer, to implement or override the metadata from the parser plugin to set the t=0 to a specific date and time
 - Add settings.json keys for "smart_scale: { max_seconds: 300, max_minutes: 120, max_hours: 24 }" and a persistent smart-scale enable in the settings. If smart-scale is off (default, same as a scope view) it just shows "kilo seconds" for long time intervals. If smart scale is enabled, it will switch to MM:SS[.xxx] view when the labels go beyond "max_seconds", and then keeps using MM:SS[.xxx] until it hits "max_minutes", at which point it will start showing HH:MM:SS (.xxx miliseconds were already questionable at the minute scale, but at the hour scale definitely not there anymore), then when it sees max_hours being crossed, it will add days: DD:HH:MM:SS, possibly dropping the SS. The dropping of detail can also, and probably should, be dictated by the level of zoom-detail. IF you're looking at a window with each div line saying "23:12:18", because you're looking at 23:12:18.004 to 23:12:18.088 then obviously that needs to be handled. Ideally in this case I'd say with smart scale enabled it would show "23:12:18" at the start of the X scale line, and then just show ".004" at the first div line, "0.012" at the next, for example. Because all those hours, minutes and seconds are all fixed across the view scale. Maybe if "smart-scale" is enabled in shorter spans also always show "182.004" at the start of the X axis and then just ".008", ".012", ".016" at each div, but leaving the current view as is for "classic behaviour".
 - Create plugin groups: A plugin file can declare a group, or be placed in the group folder name (for future cleanup if needed) and will then be loaded into that group. Groups are possibly multi-level ("measure/rms" as group name makes plugins->measure->rms->{plugin.name}, same as when it would have been placed in /plugins/measure/rms/) and the group name in the plugin file beats folder name. e.g.:
	* plugins/plugin_one.py defines group="Spikes"; name="Spike reject"
	* plugins/plugin_two.py defines group="Spikes"; name="Spike extract"
	* plugins/plugin_three.py defines group="spIkes" name="Spike split"
	* plugins/coconut/plugin_four.py defines group="Spikes" name="Spike flip"
	* plugins/spikes/plugin_five.py defines name="Max spike = t0"   # no group defined
	* plugins/plugin_six.py defines name="Awesome Plugin"    # no group defined
  Will end up:
   plugins->Spikes->{plugin_one.name, plugin_two.name, plugin_three.name, plugin_four.name, plugin_five.name}
   plugins->Ungrouped->{plugin_six.name}
  i.e.: group-name folders and name strings are case-insensitive (on all OS'es!) and the GUI draws them as Camel Case: "my gROUP name" -> "My Group Name". ideally stupid-chars ('-', '_', '\t', '    ' etc) get replaced by a single space, to avoid ending up with group entries in the GUI for "My_group_name", "My Group Name", "My   Group Name", etc. And there is one "catch all" group "Ungrouped" for plugins that forgot to declare a group and aren't in a named folder.
 - When ManyLines(tm) are imported, the view in split mode very neatly builds the full list and then resizes and applies a scroll function to the list so everything is somewhat legible. This asks for some upgrades:
	* In default operation, when pressing modifier key(s) the scroll should no longer zoom the plots, but scroll the list.
	* By default the modifier list will be any one of crtl, alt or shift will work: ctrl+scroll = scroll list, alt+scroll=scroll list, etc. ctrl+alt(+shift) does not have to scroll the list, just pressing any one key.
	* Ideally this list is configurable at least from 1 to 3 keys, 5 seems a real-world upper limit for someone wanting to always have any finger near a key.
	* The behaviour should be invert-able and disable-able in the following senses:
		~ zoom with mouse wheel = on/off (on by default)
		~ scroll list with mouse wheel = on/off (on by default)
		~ scroll mode toggle keys = { ctrl, alt, shift }
		~ scroll mode default action = zoom/scroll list   <-- The one set here will need no modifier IF both are turned on, the other will require any one of the set modifiers.
		Propose: Settings->Advanced UI->Mouse->Scroll->
	* Arrow keys should scroll and pan time if enabled, with settings:
		~ Allow left/right panning of time = true/false (true default)
		~ Allow up/down trace scrolling = true/false (true default)
		Proposed: Setting->Advanced UI->Keyboard->
	* Multi-trace minimum height setting in Settings->Advanced UI->Split View->Minimum Trace Height=
 - When importing data is done, at least on many-column but sparse data in some instances (hp34970a_exmaple.csv) the initial div-scaling in the status bar isn't sensible.
 - When the mouse is over the status bar, using the scroll wheel should scroll the status bar, if enabled:
	* Settings->Advanced UI->Mouse->Scroll->Enable Status Bar Scrolling
	* ideally left/right click of mouse wheel if present would also move the status bar by one block exactly (snap to block edge)
	* In stead of left/right button moves time enable/disable, maybe rework that menu option into:
		~ Settings->Advanced UI->Keyboard->Arrows->
			^ MutEx Option 1: Off / do not use
			^ MutEx Option 2: Use to pan time axis
			^ MutEx Option 3: Use to pan status bar by 1 block
 - Group channels in channel panel through the import menu's group function as well, e.g. in HP 34970A / Agilent Benchlink 3 import grouping by unit type in a group would be helpful for overlapped view, because who cares about comparing volts with degrees celcius (usually), so you can quickly enable/disable from view an entire signal-unit group.
 - Make enabling/disabling a channel depend on a click anywhere on its name (same for groups) in stead of very sensitively the blue enable box, but make sure that dragging still works.
 - Support creating a group and renaming it based on currently viewed channels, could be a plugin, but a GUI function in the channel menu would be better
 - Support renaming a signal in the channel panel
 - When exporting to CSV use the TraceLab header function (added in feat_CSV_Robustness) to also export group information in the export, so that manipulating channel settings, naming and grouping in the app can be saved to a native CSV format.
	* A new "addgroup" directive is defined in the CSV parser system for custom TraceLab as follows:
		~ #addgroup={ "some group name", 2, 4, 6, 8, 10, 12 }    # defines "some group name" with columns 2, 4, 6, 8 , 10  ,12
		~ #addgroup={ "The best group", "101", "banana", "fish tacos" }   # defines "The best group" with "101" "banana" "fish tacos", referring to exact column name strings after parsing, however weird a main course that is. 
 - Fix this rare bug on n'th import of data (folder origins anonymised):
	Traceback (most recent call last):
	File "\core\main_window.py", line 1289, in _zoom_full_safe
		pi.setYRange(y0 - pad_y, y1 + pad_y, padding=0)
		~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
	File "\Python314\site-packages\pyqtgraph\graphicsItems\PlotItem\PlotItem.py", line 319, in method
		return getattr(self.vb, name)(*args, **kwargs)
			~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
	File "\Python314\site-packages\pyqtgraph\graphicsItems\ViewBox\ViewBox.py", line 696, in setYRange
		self.setRange(yRange=[min, max], update=update, padding=padding)
		~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
	File "\Python314\site-packages\pyqtgraph\graphicsItems\ViewBox\ViewBox.py", line 619, in setRange
		raise Exception("Cannot set range [%s, %s]" % (str(mn), str(mx)))
	Exception: Cannot set range [nan, nan]
 - Trace Colours in the import window would ideally be assigned in order to enabled channels, not in order to channels, and then only loaded into enabled channels. Say I have 8 colours and 32 channels, of which I want to show only 4, suddenly I end up with 4 yellow traces.
 - The Import Window could use a pass for user friendliness and clarity and alignment of stuff as well.
 - Add the ChipFX Icon to the program
 - Add a button to the trace panel to delete a trace.
 - Trigger menu and options on the smallest possible window now takes up 2/3rd of the right hand action bar.
	* Trigger Settings can be optimised(, after which the layout of the area can also do with reduced blank space):
		~ Channel: [DropDown]   # Leave as is, sometimes a channel name set is long
		~ Edge: [Smaller Dropdown] | Level: [Smaller box]  # Level accepts and shows n, u, m, k, M, so doesn't need loads of space
		~ Search: [tick] Start  [tick] End  # Right now it's a weird info block, by making it two ticks and adding the search from end option it actually has a use.
	* On trigger can be optimised (and hopefully de-blanked as well):
		~ [tick] Place Cur. A | [tick] Set t=0
		~ [tick] Zoom to trig | [tick] Auto-update  # add "retrigger" only if it fits
	* The "Find Trigger" and "Next" buttons can be placed next to each other. 50/50 or 60/40
 - The cursor data view can also be improved:
	* A: ----         | B: ----
	* dt: ----        | 1/dt: ----
 - These should allow a small window to still show a decent amount of trace data at cursor points, rather than showing a line and a half with a scroll bar.
 - Traces now have a segments element, as well as a primary_segment element. Both can be set to none, in which case they should be ignored as "don't exist", otherwise;
	* segments is a list[(start, end, t0_abs, t0_rel)]; 
		~ [int, int, float, float] with 1 or more entries, each entry represent a segmentation on the existing data import. If there are 2 or more segment elements in the list, that means that the time_data and raw_data (and possibly processed_data) contain multiple segments captured around a similar trigger on the same channel. The time_data will thus (likely) jump from positive index back to negative index on the edge between two segments. This allows non-segment aware plotters, analysers and modifier plugins still run on the full data, editing all segments the same.
		~ start and end define the sample index on time_data and raw_data (and processed_data) on which that segment exists.
		~ t0_abs is the absolute time, or t0_wall_clock for that segment. To stay compatible to simpler data models, the trace's t0_wall_clock is always also set, but to the first segment's clock, not some average or middle value.
		~ t0_relative is the offset in seconds from this segment to the first segment, useful to quickly calculate segment trigger jitter (i.e. often signal jitter on good scopes)
	* primary_segment is an integer either "None" or a number 0 through n-1, where n is the number of segments in the segment list, thus primary_segment is the show-index in the segments list. A channel will get an addition to its right click context menu to set the primary_segment and how to deal with non-primaries, including at least:
		~ Show only primary (hide non-primary)
		~ Show non-primaries dimmed (setting in settings.json; View->Segments->Dim Opacity=[10% to 90%]) (default setting when segemnts present and primary is not none)
		~ Show non-primaries dashed (setting in settigns,json; "View->Segments->Dash Settings->Dash Size" and "View->Segments->Dash Settings->Gap Size")
		~ Show non-primaries as regular (line at 100% opaque, as it would do now "by accident")
		~ Trace model gets a "non_primary_viewmode" element which contains this selection per trace.
		~ primary_segment=None --> Same as Show non-primaries as regular, because there isn't a primary to choose from.
	* There is a "View->Segemnts->Process Segments" setting, default checked, if set, the segments get processed by the view system as dictated by the settings. Showing multi-segment may invalidate persistence and interpolate/averaging for that trace at a point in the future, but for now, we leave those as-is and see what happens when both are enabled, maybe it's super useful.
	* View->Interpolation auto-re-zooms the traces to a point where interpolation starts. That's weird. Probably should avoid doing that.
	* 'Analysis->Interpolation per Channel->[channel name]->Cubic Spline' does not set the badge on the channel in the status bar. There is probably some good work to be done on the badging system: Have any device that sets a new interpolation trigger the status bar to update that channel's block. If at all possible it should be investigated if we can make trace data work such that at least elements that would be interesting for the status bar trigger a channel-block update in the status bar when that data is modified, so that elements "up wind" only need to know the trace model and never have to call an update on the status bar. E.g. the active segment of a trace may well be a status indication at some point. The wall_clock time for t=0 on a trace might be interesting as well, when you consider multiple sources can be loaded at once.
