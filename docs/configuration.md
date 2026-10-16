# Configuration reference

For the short setup instructions, start with the [main guide](../README.md#setup).

## Requirements

Use Linux with COSMIC, CBar, Python 3.10 or newer, and system timezone data for
your selected timezone. The plugin uses only Python's standard library.
An internet connection is needed to download a calendar, not to read one that
has already been saved.

## Settings

Your configuration is a JSON object. The public [example](../examples/config.json)
uses London demonstration data; replace it with your own choices.

| Field | Meaning |
| --- | --- |
| `label` | Name shown in the popup; it does not choose a location |
| `latitude`, `longitude` | Coordinates in decimal degrees, written as numbers |
| `timezone` | IANA timezone name, written as a string |
| `method_id` | Supported AlAdhan calculation method ID; custom method `99` is not supported |
| `school_id` | `0` for standard Asr, `1` for Hanafi Asr |
| `latitude_adjustment_method` | High-latitude rule: `1` middle of the night, `2` one seventh, `3` angle based |
| `midnight_mode` | `0` for standard midnight, `1` for Jafari midnight |

See the [AlAdhan API documentation](https://aladhan.com/prayer-times-api) for
calculation methods and rules. A missing or invalid configuration produces a
short error in CBar instead of selecting a location automatically.

## Where files are stored

| File type | Default location |
| --- | --- |
| Configuration | `~/.config/cbar-prayer-times/config.json` |
| Plugin | `~/.local/share/cbar-prayer-times/plugins/prayer-times.1m.py` |
| Panel entry | `~/.local/share/applications/cbar-prayer-times.desktop` |
| Calendars | `~/.cache/cbar-prayer-times/` |

The standard `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_CACHE_HOME` variables
override these locations. Empty values use the normal home-folder defaults.

Calendar folders are keyed by the selected settings. Changing a setting creates
separate storage rather than silently reusing another location's timetable. A
download is validated before it replaces a saved calendar. The current and
following year are prepared, including when next year's Fajr is needed after
Isha on New Year's Eve.

## Manual refresh from a terminal

```sh
plugin="${XDG_DATA_HOME:-$HOME/.local/share}/cbar-prayer-times/plugins/prayer-times.1m.py"
config="${XDG_CONFIG_HOME:-$HOME/.config}/cbar-prayer-times/config.json"
python3 "$plugin" --refresh-cache --config "$config"
```

Add `--notify` to request the same start and completion banners as the panel
action. Refresh without `--notify` prints its result in the terminal without
requesting a banner. Ordinary one-minute panel updates stay silent.

| Option | Purpose |
| --- | --- |
| `--config PATH` | Use a specific configuration file |
| `--refresh-cache` | Download the current and following year again |
| `--notify` | Request native banners; valid only with `--refresh-cache` |
| `--at ISO_DATETIME` | Use a fixed date/time for checks, such as `2026-01-01T12:00:00+00:00` |

A fixed date still requires that year's saved calendar or access to the service.
All dates and countdowns use the configured timezone, including clock changes.

## Notification and path limits

Native banners use `busctl` and are best effort. A missing notification service,
disabled banners or Do Not Disturb does not stop a refresh. Success is requested
only after both years are saved; a failure keeps any previously valid calendar.

The popup's refresh action passes your selected configuration to the plugin.
CBar's action parser cannot safely represent a configuration path containing
both a single quote and a double quote, or a newline. The plugin reports an
error instead of creating an unsafe action for such a path.

## Installing again

Running `bash scripts/install.sh` again updates this plugin and keeps your
configuration and saved calendars. The installer checks for Python 3.10 or newer
and an executable CBar. If CBar is in another location, set `CBAR_BIN` to its
executable path. It does not download CBar or other dependencies.
