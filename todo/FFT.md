# FFT

The FFT Window needs work.

## Wishlist:
 1. The bars with controls are much too wide, they should be grouped by function into more bars then there are now:
    * [ Trace Dropdown ] [ Window Dropdown ] [ () All Data | () From Main Window Zoom ] [ Fit Full Span ] [ Fit Amplitude ] [ []Auto-Y ]
	* Span: Min: [ input Hz ] Max: [ input Hz ] [ Suggest Button ] [ Accented Compute FFT button, with bold text ]
	* Min peak Prominence: [ Input spin ] dB | Cursors: A: [ ... ] [Snap Next ->] B: [ .... ] [ Snap Next -> ] [ Remove Cursors ]
	* [ Mark top 10 peaks ] [ Add Marker ] [ Clear All Markers ]
	The last two lines should probably live in a context outline, since they share the peak prominance.
 2. The trace selection box needs to be wider by default, so the layout of the layout of the bars has a good balance while developing between trying to get to minimum width and average trace name legibility
 3. The FFT window should also get a settings.json entry for its size
 4. The FFT window should get a mild bit of branding as well. NOt too outspoken, don't want to waste a lot of space. Maybe to the left of all the control bars, since you click on the right in the main window for the FFT quick action, then having the logo left of the controls pushes them closer to where your mouse already is on screen.
 5. The FFT window does not have a complex geometry encoding system, it just opens centre on the main app with a width and height that defaults to a reasonable number (TBD after UI reworks) initially and then just saves that in settings.json
 6. The channel selection box should have an [all channels] entry to show them all at once. To think about: Maybe a channel panel similar to the main view, inheriting or even linking the view and grouping settings, so you can enable what sets you want however you like.
 7. If it can be figured out it'd be cool when viewing all traces, or multiple traces, if the mark top 10 peaks can apply to just one trace, or to the peaks of all traces, as follows (or better if it presents itself on reflection):
    * Each FFT plot evaluates its 10 highest peaks and those get collected into a big bucket of peaks (each peak of course still knowing what colour trace it belongs to)
	* The big bucket of peaks sorts for top 10.
	* The marker function marks the top 10, and applies the trace colour to the label of the trace the peak points to / belongs to.
	* The labels will keep the theme_background+alpha background setting to avoid text-to-trace blending.
	* Label placement probably needs to be refined for multi-trace view, but we'll run into that bridge when the cows come home.
 8. Add tooltips to things like the min prominance label and spin box saying "Top 10 MArkers and cursors will only apply to peaks that are at least this much above the surrounding levels to suppress noise snaps" and such "Noob introduction tooltipping"
