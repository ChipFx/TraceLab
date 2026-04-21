## Wishlist:
 - Statusbar block order should follow trace order in trace-panel, just like cursor menu and in-plot tracelabels (in overlaped view)
 - Add new interfaces to the import menu to handle all the new meta data:
	* Add a group select/deselect system, using the groups information (if present) from the parse plugin (ParsedMetadata.groups)
	* Listen to the parse plugin's "skip rows" if setting is enabled in the dialogue (persistent setting through main(settings-cache) <-> settings.json on exit)
 - Add "manually define t=0 date-time" input or option window to the importer, to implement or override the metadata from the parser plugin to set the t=0 to a specific date and time
 - Add settings.json keys for "smart_scale: { max_seconds: 300, max_minutes: 120, max_hours: 24 }" and a persistent smart-scale enable in the settings. If smart-scale is off (default, same as a scope view) it just shows "kilo seconds" for long time intervals. If smart scale is enabled, it will switch to MM:SS[.xxx] view when the labels go beyond "max_seconds", and then keeps using MM:SS[.xxx] until it hits "max_minutes", at which point it will start showing HH:MM:SS (.xxx miliseconds were already questionable at the minute scale, but at the hour scale definitely not there anymore), then when it sees max_hours being crossed, it will add days: DD:HH:MM:SS, possibly dropping the SS. The dropping of detail can also, and probably should, be dictated by the level of zoom-detail. IF you're looking at a window with each div line saying "23:12:18", because you're looking at 23:12:18.004 to 23:12:18.088 then obviously that needs to be handled. Ideally in this case I'd say with smart scale enabled it would show "23:12:18" at the start of the X scale line, and then just show ".004" at the first div line, "0.012" at the next, for example. Because all those hours, minutes and seconds are all fixed across the view scale.
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
 -