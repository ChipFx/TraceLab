---
short_name: file_menu
long_name: File Menu
chapter: menu_bar
chapter_long: Menu Bar
order: 1
keywords: [ba, na, na]
---

File Menu
===========

The File Menu allows you to Open a CSV file (Ctrl + O), Export the visible data,
Save a Screenshot (Ctrl + P), Clear all Traces and of course Quit (Ctrl + Q).

Open a CSV
----------

Using this entry you can open any CSV file.

The Open dialog will remember when you close the application where you were
opening files from, so that next time it's easy to continue your work.

Upon first loading the CSV file the application runs a scan of the available CSV
plugin configurations in the csv-parsers/ folder. When a special parser is found
for the type of CSV, that parser detects channel information and settings.
After all the settings have been retrieved from the CSV file, the import dialog
is opened, showing the channels that were found.

For more information about the CSV Parser plugin see `Plugins: CSV Parsers <rst-doc://plugins/csv-parsers>`_.

For more information about the import dialog see `Import Dialog <rst-doc://importing/csv-files>`_.

Export Visible Data as CSV...
-----------------------------

This option allows you to export all the data points in the current view to a 
new CSV file. It uses the TraceLab Native format, which is a name given to a 
generic CSV file with a set of special meta-data headers before the main data.

The export will only export true data points. If you're looking at an
interpolation method, this will not be exported. A future option for that
may well be implemented, but this is not it.

Saving a Screenshot
-------------------

This allows you to simply save a PNG view of the Traces and Status Bar.
The resulting screenshot is created with twice as many pixels as your screen
shows at the time of creation, to allow extra high resolution pictures for
documentation purposes.

Clear all Traces
----------------

Simple deletes all the trace data from the application, reverting back to its
initial state.
