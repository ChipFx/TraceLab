## Wishlist:
 - Primary note: ALL Settings mentioned in this whishlist should be cached in the main settings cache and written to settings.json on application close, like just about every other setting so far. All settings should also recover any tick-mark locations from the settings cache after it is loaded from disk.
 - When importing data is done, at least on many-column but sparse data in some instances (hp34970a_exmaple.csv) the initial div-scaling in the status bar isn't sensible.
	* UPDATE: I think it is that the channel blocks for channels that haven't scrolled into view yet haven't been updated to a div scale yet. On an import of 101 through 116 + 301 through 316, the bottom channels all had their div scaling set to something like "100V/div" in the status bar, but once scrolled down in the view, or after scrolling and taking one zoom step, suddenly they updated to the correct x mV/div to x uV/div setting that was correct with the loaded data.


## Wishlist NEXT:
 1. Cursor measurements panel not alphabetical, but same order as trace panel, hard linked. ADC1, ADC2, ADC3, ADC4, ADC1_001, ADC2_001, ADC3_001, ADC4_001 in that order in the trace panel become ADC1, ADC1_001, ADC2, ADC2_001, ... etc in the cursor panel. Thought this was fixed before, but apparently not.
 2. Segments are now handled correctly in the main view system, but persistence, averaging and interpolation still have no idea what to do with them. I think they should operate on the primary segment if chosen, if none is chosen it seems probably best to average all segments before applying the tool. This was the tool doesn't need major rework itself, and the unused segments can exist next to persistence or averaging, etc.
 3. In the Acquire menu, when you have previously set a persistence (either future or normal), and then select averaging, even though they are mutually explusivem the tick at persistence remains, while a tick is added to averaging. the group (off, persistence normal, persistence future, averaging, interpolation) needs to be mutually exclusive in both the menu and the operation.
 4. The import menu understands grouping to some extent, but the group banners don't seem to allow folding/unfolding. It's nitpicky, but with 60 channels it does help to be able to fold a group you're done fiddling with.
 5. Done.
 6. When you delete a trace, and select "Apply Default Colour" on a trace you haven't edited yet that used to be below the deleted trace, it updates the trace's colour to its new colour as dictated by the new order. Programmatically that seems fine, but of course, if you have a list of 8 traces, you delete the 5th, and then click only on the 6th or 7th for default colour, it suddenly becomes the same colour as a trace next to it, because the others are not updated. It makes no sense for the "restore default" on a single trace to force the other traces to also change colour, but it'd be cool if default colouring would always be as unique as possible. The easiest solution seems to change the action in the menu to "Restore Default Colours" and just make it trigger a re-index of all displayed channels. That would mean after re-ordering the panel, and then restoring, the channels would also change colour, but that may be more desirable than various traces next to each other having the same colour. Maybe with some more thinking time this point will get an update for a better solution to the base issue.
 7. Ideally the Trace View list, in Split-View get the exact same right click menu as the trace names in the Channel Panel. Preferably with a direct redirection to an entity or entity template, so that an update to the one also updates the other. In the ideal-ideal situaltion the Channel Panel and Overlay Traces view would also have the same right click menu when you click in the Channel Panel or on the Channel Label in the Overlay View, where that right click menu becomes the same as Split-View, but with the addition of "bring to front" being added. Or, if not being added being not-greyed-out in Overlay and greyed out in Split View. In fact, that last bit is probably the best, where the right-click menu on a channel_info element (as I'm calling them for convenience) is always the exact same, but actions that don't make sense for this view mode just get greyed out. That leaves on single channel_right_click menu to maintain between the signal view panel and the Channel Panel. If we're being really utopic, right clicking on a channel block in the Status Bar opens the same menu, because now a left click and right click both change interpolation, and that's wasted context. So in the UI utopia:
	- There is one channel_right_click menu element, which contains everything that split and overlay need. This element enables/disables elements directly based on the View->[Overlay / Split] setting (as buffered in the application, checked on right-click event / instantiation, or through call-back when changing, as long as it's a robust pathway).
	- The menu's entries get tied to actions, anything that connects to Overlay-View-Behaviour greys out in Split View and vice-versa, anythgin that is universal is always enabled.
	- A right click on the Status Bar channel block, Trace Element in split view, Trace Label in Overlay view, or Channel Name in the Channel Panel all open that right click menu.
	--> Future additions, changes, updates, all neatly in one bit of code.
	- The right-click menu anywhere on the overlay panel, other than a channel name label opens the same menu as now with the BRing to Front-> being a sub-menu with a full list of the active traces to pick from, however, the following error is handled (have not read yet, just copy-pasted) when using the bring to front system:
		File "\Python\Python314\site-packages\pyqtgraph\graphicsItems\GraphicsObject.py", line 20, in itemChange
		self.changeParent()
		~~~~~~~~~~~~~~~~~^^
	File "\Python\Python314\site-packages\pyqtgraph\graphicsItems\GraphicsItem.py", line 394, in changeParent
		self._updateView()
		~~~~~~~~~~~~~~~~^^
	File "\Python\Python314\site-packages\pyqtgraph\graphicsItems\GraphicsItem.py", line 448, in _updateView
		self.viewRangeChanged()
		~~~~~~~~~~~~~~~~~~~~~^^
	File "\Python\Python314\site-packages\pyqtgraph\graphicsItems\PlotDataItem.py", line 1827, in viewRangeChanged
		self.updateItems(styleUpdate=False)
		~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
	File "\Python\Python314\site-packages\pyqtgraph\graphicsItems\PlotDataItem.py", line 1378, in updateItems
		dataset = self._getDisplayDataset()
	File "\Python\Python314\site-packages\pyqtgraph\graphicsItems\PlotDataItem.py", line 1556, in _getDisplayDataset
		if view is None or view.autoRangeEnabled()[0]:
						^^^^^^^^^^^^^^^^^^^^^
	File "\Python\Python314\site-packages\pyqtgraph\widgets\PlotWidget.py", line 85, in __getattr__
		raise AttributeError(attr)
	AttributeError: autoRangeEnabled
 8. Next to smart-scale and non-smart-scale a real-world-time toggle would help {View->Time Scale->[Standard / Smart Scale / Real Time]-mutually-exclusive}, else we're storing the t0_wall_clock parameter for nothing. This may not be a very useful thing along the time-axis, but I'd like to try it out, where the long-range time element is posted once up front, and the smallest useful sub-index per div, e.g.:
	* "2026-04-18 15:18:00.0" ["+1:00.0"   "+2:00.0"   "+3:00.0"   "+4:00.0"   "+5:00.0"] if you're looking at a timescale of the major lines being minutes, then it makes no sense for the pre-print to have anything filled in for seconds or ms, but it should acknowledge them with 0's to make it easier to comprehend.
	* "2026-04-18 15:18:24.0" ["+2.0"      "+4.0"      "+6.0"      "+8.0"      "+10.0"  ] if the major ticks are at 2s intervals because you just zoomed in on a part of the same data as the previous point, the time-date stamp at the start snaps to a major line, thus the seconds are even to aling with the 2 second steps, and then every next major has a +2.0 marker. With a bit of training and a good manual a decent engineer will see the minute and hour marker are missing and quickly comprehend them to be seconds.
	* "2026-04-18 15:18:24.008" ["+1ms"    "+2ms"    "+3ms"    "+4ms"    "+5ms"] The initial stamp shows now it's miliseconds by the longer tail on the seconds, and then each major increments with sub-second accuracy according to normal s/ms/us/ns label rules.
	* Zoomed in further, the date-time stamp stays the same ms accurate, because, really, when you're looking at day and hours, a mili second is already insanely accurate. The only reason you're zooming in further is to see the profile of a high frequency noise spike or burst, and whether that happened at +306us from the milisecond or -100us, at this scale of timestamping should not be relevant, and if it is, people can use cursors to get deeper offset accuracy.
 9. When Smart Scale or Real Time are set for the plot timeline, the Cursor A and B position should show the same number type as on the time-axis. Ideally each number would calculate its own "smart time" in the case of smartscale, based on the same settings as the axis, to avoid the numbers from jumping between various "smart" indices based on zoomstate, which they would do if they truly copy directly from the timeline. So their input is still seconds ("81138.2s" for example), but then just for viewing in the menu they filter it through the "Smart Scale" filter. dt should still also be calculated as seconds, but then once again filter itself through the Smart Scale for display only. 1/dt can stay in Hz, but while we're futzing with the numbers in that window, maybe make it "smart by default" in the sense of letting it use the range of n/u/m/./k/M/G/T automatically to avoid "1.232e-05 Hz", rather putting it as 12.32 uHz (as weird as that looks, it does trigger neatly as a "huh, weird! Ignore.") u of course replace by the small letter mju.



## Completed So Far:
	* 'Analysis->Interpolation per Channel->[channel name]->Cubic Spline' does not set the badge on the channel in the status bar. There is probably some good work to be done on the badging system: Have any device that sets a new interpolation trigger the status bar to update that channel's block. If at all possible it should be investigated if we can make trace data work such that at least elements that would be interesting for the status bar trigger a channel-block update in the status bar when that data is modified, so that elements "up wind" only need to know the trace model and never have to call an update on the status bar. E.g. the active segment of a trace may well be a status indication at some point. The wall_clock time for t=0 on a trace might be interesting as well, when you consider multiple sources can be loaded at once.
	* When a cursor is past the data on a trace, it still shows the last value it was at, while it is parked at a NaN point on the trace. When a cursor hits NaN it should revert to "---" as value.
	* View->Interpolation auto-re-zooms the traces to a point where interpolation starts. That's weird. Probably should avoid doing that.
 - Removing the cursors doesn't clear the A/B data and trace measurements, but it should.
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
 - The Import Window could use a pass for user friendliness and clarity and alignment of stuff as well.
 - Add the ChipFX Icon to the program
 - When exporting to CSV use the TraceLab header function (added in feat_CSV_Robustness) to also export group information in the export, so that manipulating channel settings, naming and grouping in the app can be saved to a native CSV format.
	* A new "addgroup" directive is defined in the CSV parser system for custom TraceLab as follows:
		~ #addgroup={ "some group name", 2, 4, 6, 8, 10, 12 }    # defines "some group name" with columns 2, 4, 6, 8 , 10  ,12
		~ #addgroup={ "The best group", "101", "banana", "fish tacos" }   # defines "The best group" with "101" "banana" "fish tacos", referring to exact column name strings after parsing, however weird a main course that is. 
 - Make enabling/disabling a channel depend on a click anywhere on its name (same for groups) in stead of very sensitively the blue enable box, but make sure that dragging still works.
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
 - When the mouse is over the status bar, using the scroll wheel should scroll the status bar, if enabled:
	* Settings->Advanced UI->Mouse->Scroll->Enable Status Bar Scrolling
	* ideally left/right click of mouse wheel if present would also move the status bar by one block exactly (snap to block edge)
	* In stead of left/right button moves time enable/disable, maybe rework that menu option into:
		~ Settings->Advanced UI->Keyboard->Arrows->
			^ MutEx Option 1: Off / do not use
			^ MutEx Option 2: Use to pan time axis
			^ MutEx Option 3: Use to pan status bar by 1 block
- Statusbar block order should follow trace order in trace-panel, just like cursor menu and in-plot tracelabels (in overlaped view)
- Support renaming a signal in the channel panel
- Add a button to the trace panel to delete a trace.
 - Traces now have a segments element, as well as a primary_segment element. Both can be set to none, in which case they should be ignored as "don't exist", otherwise;
	* segments is a list[(start, end, t0_abs, t0_rel)]; 
		~ [int, int, float, float] with 1 or more entries, each entry represent a segmentation on the existing data import. If there are 2 or more segment elements in the list, that means that the time_data and raw_data (and possibly processed_data) contain multiple segments captured around a similar trigger on the same channel. The time_data will thus (likely) jump from positive index back to negative index on the edge between two segments. This allows non-segment aware plotters, analysers and modifier plugins still run on the full data, editing all segments the same.
		~ start and end define the sample index on time_data and raw_data (and processed_data) on which that segment exists.
		~ t0_abs is the absolute time, or t0_wall_clock for that segment. To stay compatible to simpler data models, the trace's t0_wall_clock is always also set, but to the first segment's clock, not some average or middle value.
		~ t0_relative is the offset in seconds from this segment to the first segment, useful to quickly calculate segment trigger jitter (i.e. often signal jitter on good scopes)
	* primary_segment is an integer either "None" or a number 0 through n-1, where n is the number of segments in the segment list, thus primary_segment is the show-index in the segments list. A channel will get an addition to its right click context menu to set the primary_segment and how to deal with non-primaries, stored in the pre-made "non_primary_viewmode" element on the trace model including at least:
		~ Show only primary (hide non-primary)
		~ Show non-primaries dimmed (setting in settings.json; View->Segments->Dim Opacity=[10% to 90%]) (default setting when segemnts present and primary is not none)
		~ Show non-primaries dashed (setting in settigns,json; "View->Segments->Dash Settings->Dash Size" and "View->Segments->Dash Settings->Gap Size")
		~ Show non-primaries as regular (line at 100% opaque, as it would do now "by accident")
		~ primary_segment=None --> Same as Show non-primaries as regular, because there isn't a primary to choose from.
	* There is a "View->Segemnts->Process Segments" setting, default checked, if set, the segments get processed by the view system as dictated by the settings. Showing multi-segment may invalidate persistence and interpolate/averaging for that trace at a point in the future, but for now, we leave those as-is and see what happens when both are enabled, maybe it's super useful.
 - Add "manually define t=0 date-time" input or option window to the importer, to implement or override the metadata from the parser plugin to set the t=0 to a specific date and time
 - Add new interfaces to the import menu to handle all the new meta data:
	* Add a group select/deselect system, using the groups information (if present) from the parse plugin (ParsedMetadata.groups)
	* Listen to the parse plugin's "skip rows" if setting is enabled in the dialogue (persistent setting through main(settings-cache) <-> settings.json on exit)
 - Add settings.json keys for "smart_scale: { max_seconds: 300, max_minutes: 120, max_hours: 24 }" and a persistent smart-scale enable in the settings. If smart-scale is off (default, same as a scope view) it just shows "kilo seconds" for long time intervals. If smart scale is enabled, it will switch to MM:SS[.xxx] view when the labels go beyond "max_seconds", and then keeps using MM:SS[.xxx] until it hits "max_minutes", at which point it will start showing HH:MM:SS (.xxx miliseconds were already questionable at the minute scale, but at the hour scale definitely not there anymore), then when it sees max_hours being crossed, it will add days: DD:HH:MM:SS, possibly dropping the SS. The dropping of detail can also, and probably should, be dictated by the level of zoom-detail. IF you're looking at a window with each div line saying "23:12:18", because you're looking at 23:12:18.004 to 23:12:18.088 then obviously that needs to be handled. Ideally in this case I'd say with smart scale enabled it would show "23:12:18" at the start of the X scale line, and then just show ".004" at the first div line, "0.012" at the next, for example. Because all those hours, minutes and seconds are all fixed across the view scale. Maybe if "smart-scale" is enabled in shorter spans also always show "182.004" at the start of the X axis and then just ".008", ".012", ".016" at each div, but leaving the current view as is for "classic behaviour".
 - Trace Colours in the import window would ideally be assigned in order to enabled channels, not in order to channels, and then only loaded into enabled channels. Say I have 8 colours and 32 channels, of which I want to show only 4, suddenly I end up with 4 yellow traces set in the import window.
    * UPDATE: Fairly sure there is a disconnect here anyway. If you change the colour in the import window, it doesn't carry over to the GUI, this is likely to do with the fact the plot window applies the standard colour progression from the theme, so I think colours should probably just not be added to the import window at all. Seeing as when you change a theme it changes the trace colours.... What's the point of the relatively large effort of going through manual edits in the import menu? Let's leave that space for more useful advanced stuff to do with segments, or whatever.
 - (UPDATE: It looked like you touched code, but the menu has not grouped the two existing plugins into "Ungrouped", so the menu creation is not working!) Create plugin groups: A plugin file can declare a group, or be placed in the group folder name (for future cleanup if needed) and will then be loaded into that group. Groups are possibly multi-level ("measure/rms" as group name makes plugins->measure->rms->{plugin.name}, same as when it would have been placed in /plugins/measure/rms/) and the group name in the plugin file beats folder name. e.g.:
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
 - Group channels in channel panel through the import menu's group function as well, e.g. in HP 34970A / Agilent Benchlink 3 import grouping by unit type in a group would be helpful for overlapped view, because who cares about comparing volts with degrees celcius (usually), so you can quickly enable/disable from view an entire signal-unit group. (Update: see Wishlist Next section point 5. for a new idea to better solve this with grouping parsers at import and while in the channel view UI both)
 - Support creating a group and renaming it based on currently viewed channels, could be a plugin, but a GUI function in the channel menu would be better


## Completed from Wishlist Next:
 5. Benhlink import groups by channels and alarms very well, now it'd be helpful if it also groups, or can group by channel's set unit, and if at all possible by some form of wildcard name-match (e.g. column_name.make_group("3??") call that matches all import data columns that match 301 to 399 to identify the cartridge, or "internal*" to filter all signal names for internal temp measurements to separate them from "extermal*"). Although I'd prefer these options at least to also be available from the channel panel. If possible there should be a tickable option to either create groups within existing groups (nested parsing, but still flat view) or create new groups according to the chosen rule, to explain:
	* You have 3 groups, Channels("101", "102", "103", "202", "203", "204", "306"); Alarms("101_Alarm", "102_Alarm", "103_Alarm", "202_Alarm", "203_Alarm", "204_Alarm", "306_Alarm"); Other("Time", "Scan"):
		~ With "create inside groups" and the match "1*" you get: Channels_1*("101", "102", "103"); Channels("202", "203", "204", "306"); Alarms_1*("101_Alarm", "102_Alarm", "103_Alarm"); Alarms("202_Alarm", "203_Alarm", "204_Alarm", "306_Alarm"); Other("Time", "Scan")
		~ With "create new" and the match "1*" you get: _1*("101", "102", "103", "101_Alarm", "102_Alarm", "103_Alarm"); Channels("202", "203", "204", "306"); Alarms("202_Alarm", "203_Alarm", "204_Alarm", "306_Alarm"); Other("Time", "Scan")
	* And on and on to create and/or recreate groups as you like.
	* New groups will get a default name based on:
		~ "Create inside groups": {OLD_GROUP_NAME}_{REPRESENTATION_OF_MATCH_CRITERIA}
		~ "Create new": Group_{REPRESENTATION_OF_MATCH_CRITERIA}
		Where "REPRESENTATION_OF_MATCH_CRITERIA" doesn't have to be a 1:1 copy of the match string, especially with wildcards involved, but some not-too-long form of machine-parsable, but human-understandable conversion of it. e.g.:
			^ "internal*" could become "Group_internal*", but "Group_internal(ALL)" would also work
			^ "?larm???" could become "Group_?larm???", but "Group_(ONE)larm(THREE)" or if absolutely needed "Group_(ONE)larm(ONE)(ONE)(ONE)" would also work. 
			^ Or any similar indicative way of saying "there was an uncomfortable wildcard here, which we replaced for naming"

