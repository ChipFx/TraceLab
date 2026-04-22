## Wishlist:
 - Primary note: ALL Settings mentioned in this whishlist should be cached in the main settings cache and written to settings.json on application close, like just about every other setting so far. All settings should also recover any tick-mark locations from the settings cache after it is loaded from disk.
 - The native TraceLab CSV import/export system becomes segment aware as well. No information should be lost by importing a LeCroy set of data and re-exporting it at it's full "unzoomed" setting.
	* The trace model has the segments() element and the primary_segment element already known to the importer, they just need to be populated by the TraceLab parser based on the final decision on how to handle segmentation in the TraceLab data format.
	* The Trace model also has/gets "non_primary_viewmode", which is described int her GUI todo as follows:
		A channel will get an addition to its right click context menu to set the primary_segment and how to deal with non-primaries, including at least:
		~ Show only primary (hide non-primary)
		~ Show non-primaries dimmed (setting in settings.json; View->Segments->Dim Opacity=[10% to 90%]) (default setting when segemnts present and primary is not none)
		~ Show non-primaries dashed (setting in settigns,json; "View->Segments->Dash Settings->Dash Size" and "View->Segments->Dash Settings->Gap Size")
		~ Show non-primaries as regular (line at 100% opaque, as it would do now "by accident")
		~ Trace model gets a "non_primary_viewmode" element which contains this selection per trace.
	* This non_primary_viewmode should also be stored in export and parsed in import.
	* proposal for storing segments, to be defined/refined possibly:
		~ There is a "Settings->Export->Segments->" with a MutEx toggle between:
			^ Export All Always
			^ Export Primary Only
		~ Segments get exported as individual traces with names "${TRACE_NAME}.SEG[0 .. n-1]"
		~ A header element #segments= exists, which works similar to "addgroup", as follows:
			^ #segments={ "${TRACE_NAME}", 2, 3, 4, 5, 6, 7, 8, 9 }   # lists 8 segments on columns 2 through 9 for the trace with name TRACE_NAME
			^ #segments=( "${TRACE_NAME}", "${TRACE_NAME}.SEG0", "${TRACE_NAME}.SEG1", "ADD_THIS_SEGMENT_TOO", "AND Pile This On" )   # defines a trace with apparently two original trace segments, and a manually added set of two other segments using exact column string names.
		~ A header element #segment_meta={ ${TRACE_NAME}, index, start, stop, t0_abs, t0_rel } will be added, which links the already existing trace-model's segments list data to a trace name, e.g. (using t0_0 and dt0_0 as tokens to avoid having to think up sensible time stamps):
			^ #segment_meta={ "Ampl", 0, 1, 1000, t0_0, dt0_0 }
			^ #segment_meta={ "Ampl", 1, 1, 1000, t0_0, dt0_0 }
			^ #segment_meta={ "Ampl", 2, 1, 1000, t0_0, dt0_0 }
			^ #segment_meta={ "Ampl", 3, 1, 1000, t0_0, dt0_0 }
			^ #segment_meta={ "Ampl", 4, 1, 1000, t0_0, dt0_0 }
			^ #segment_meta={ "Ampl", 5, 1, 1000, t0_0, dt0_0 }
			The main deviation here is: The segment data starts at 1 and ends at 1000 for each. Why? Because in this case it tells the importer/parser how many lines to import of these columns. This is forward compatibility to Keysight Bust/Glitch combined trace segments, which are not identical length, not might manually matched data be. Do note: the start number isn't always 1. One segment might start at t=-4us, while the other starts at t=-8us, which means the second one would have a lower start number, because the samples are always stored correctly, with each sample on the correct time-stamp line.
			The index number indicates the index of the segment element defined in segments={}, i.e. in the example #segments={ "${TRACE_NAME}", 2, 3, 4, 5, 6, 7, 8, 9 }, segment_meta's index number 0 would reference column 2. The segments in the trace model will also follow this convention, so they are stored from 0 to n-1 in the segments list in the order that they appear in the segment_meta list.
	* Export CSV will check if there are segments on any traces, check the settings where applicable, and then handle the creation of TraceLab native segmentation header data, and placing the data correctly in columns.
	* Export/Import can handle columns not having data, or at least can handle it when the importer finds segmented data, as in the example of two segments that might not exactly align, the fields that contain no segment data should ideally be empty, and be correctly handled when being empty (ideal situation either way to support the most possible data sources, segments or no), or otherwise contain a low-cost ("single char") element that can never be interpreted as a plottable character, to ensure no confusion can ever exist. Ideally it is also a character that gets ignored as non-data by Excel and LibreOffice/OpenOffice for the purpose of plotting data, maybe a single space, or a back tick? To be researched what would be best.
	* A new header element #trace_settings={ "primary_segment": None, "non_primary_viewmode": DictionaryWord } which remembers the trace's settings with respect to segmentation in the header data.