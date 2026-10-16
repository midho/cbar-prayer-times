# CBar Prayer Times

A small CBar plugin that shows the next prayer, its time, and a countdown in the COSMIC panel. Its popup contains today's timetable and the selected calculation settings. It uses the AlAdhan annual calendar service; it does not calculate prayer times itself.

## Requirements

- Linux with COSMIC and CBar already installed.
- Python 3.10 or newer, using only Python's standard library.
- System time-zone data for the timezone in your configuration.

The installer checks for Python and an executable CBar. It does not download or install CBar. If CBar is not found on `PATH` or at `$HOME/.local/bin/cbar`, set `CBAR_BIN` to its executable path.

## Install and configure

Download this repository using **Code → Download ZIP** on GitHub and extract it,
or clone it using the URL shown by GitHub's **Code** button.

From the project folder, run:

```sh
bash scripts/install.sh
```

The plugin and separate launcher are installed beneath `${XDG_DATA_HOME:-$HOME/.local/share}`. The installer does not create or replace your configuration. If the configuration file is missing, it prints the example-file path and the expected destination. Create the directory and copy the example only when no configuration already exists:

```sh
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/cbar-prayer-times"
mkdir -p "$config_dir"
if [ ! -e "$config_dir/config.json" ]; then
  cp "/path/to/cbar-prayer-times/examples/config.json" "$config_dir/config.json"
fi
```

Replace `/path/to/cbar-prayer-times` with this project's folder. Edit `config.json` for your location. Then add **Prayer Time** to the COSMIC panel using COSMIC panel settings. The launcher is hidden from the app menu. No panel restart is performed by the installer.

The example file is a blank template, not a real location. Replace `Your location`
with a label and every `null` with your own coordinates, timezone and calculation
choices from the table below. Use numbers for coordinates and calculation IDs,
and a quoted string for the timezone. The plugin rejects the template until it
has been completed; it never guesses a location.

Configuration fields:

| Field | Meaning |
| --- | --- |
| `label` | Name shown in the popup |
| `latitude`, `longitude` | Location coordinates in decimal degrees |
| `timezone` | IANA timezone name for your location |
| `method_id` | Supported AlAdhan calculation method ID |
| `school_id` | `0` for standard Asr or `1` for Hanafi Asr |
| `latitude_adjustment_method` | High-latitude adjustment: `1`, `2`, or `3` |
| `midnight_mode` | `0` for standard or `1` for Jafari |

The plugin validates the configuration. A missing or invalid file produces a short message in CBar instead of a guessed timetable.

## Calendars and refresh

Calendars are kept separately under `${XDG_CACHE_HOME:-$HOME/.cache}/cbar-prayer-times`, in a folder keyed by all selected settings. The plugin prepares the current and following calendar years. Once a valid year is saved, normal panel updates use it without making another request for that year. The saved calendar includes the calculation settings so changing a setting does not reuse an old timetable.

Downloaded calendars remain available offline. If a needed year is not saved and the service cannot be reached, that year's times cannot be shown. A failed request sets a 15-minute retry delay; manual refresh bypasses that delay. A failed refresh keeps any previously valid calendar. The popup reports which years are actually stored.

To refresh both years manually, use the installed plugin and your existing configuration:

```sh
plugin="${XDG_DATA_HOME:-$HOME/.local/share}/cbar-prayer-times/plugins/prayer-times.1m.py"
config="${XDG_CONFIG_HOME:-$HOME/.config}/cbar-prayer-times/config.json"
python3 "$plugin" --refresh-cache --config "$config"
```

`--config PATH` selects another configuration. `--at ISO_DATETIME` selects a date and time for repeatable checks, for example `--at 2026-01-01T12:00:00+00:00`. `--notify` enables the same start and completion notices for `--refresh-cache`, for example `python3 "$plugin" --refresh-cache --config "$config" --notify`; regular panel updates and command-line refreshes without `--notify` stay silent. These options do not bypass the need for a saved calendar or service access when a year is missing.

## Privacy

No personal location or user settings are bundled. The example is intentionally
blank, and tests use made-up coordinates and timetables. Keep your completed
configuration, downloaded calendars, screenshots and logs outside this repository.
The ignore rules exclude local configuration and calendar files, but always review
files before uploading them.

The refresh menu action passes the selected configuration to the plugin and requests desktop feedback. It immediately shows “Refreshing prayer times…”, then replaces that notice with success only after both years are saved, or failure if the update stops. Native notices use `busctl` and are best effort: a missing service or disabled desktop banners will not stop the refresh. Do Not Disturb settings may hide the banner. The popup also shows when the current saved calendar was last generated; a bad timestamp is shown as unavailable. CBar's action parser cannot safely represent a config path containing both a single quote and a double quote, or a newline; the plugin reports an error for such a path.

## Remove

Run:

```sh
bash scripts/uninstall.sh
```

Removal deletes only this project's installed plugin file and launcher. It keeps configuration, downloaded calendars, CBar, and any other files in the same directories.

## Accuracy and credits

Prayer times can differ from local mosque timetables because methods, adjustments, and local practices differ. Check the selected method and compare results with a trusted local source.

- [CBar](https://github.com/alexandreprates/cbar), the COSMIC panel applet host.
- [AlAdhan Prayer Times API](https://aladhan.com/prayer-times-api) and its [credits and terms](https://aladhan.com/credits-and-terms).

This project is licensed under **GPL-3.0-only**.

## Checks and remaining verification

Run the local checks from the project folder:

```sh
bash -n scripts/install.sh scripts/uninstall.sh
python3 -B -X dev -W error::ResourceWarning -m unittest discover -s tests -v
```

The GitHub Actions workflow runs the test suite on Ubuntu 24.04 with Python 3.10 and 3.12. The automated tests use synthetic calendars and temporary user directories. They do not call the live service. Desktop-entry parsing has been checked by launching it through Gio into a fake app, including paths with spaces and special characters.

A side-by-side trial on Pop!_OS/COSMIC confirmed the visible panel text, timetable popup, and start/completion messages from the manual refresh action. These checks do not prove behaviour on every COSMIC/CBar version.
