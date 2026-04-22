## Wishlist:
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
   plugins->Spikes->{plugin_one.name, plugin_two.name, plugin_three.name, plugin_four.name, plugin_fixe.name}
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