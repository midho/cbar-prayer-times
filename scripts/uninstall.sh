#!/usr/bin/env bash
set -euo pipefail
data_home=${XDG_DATA_HOME:-${HOME:?HOME must be set}/.local/share}
plugin_root="$data_home/cbar-prayer-times"; plugin_dir="$plugin_root/plugins"
plugin_target="$plugin_dir/prayer-times.1m.py"; desktop_target="$data_home/applications/cbar-prayer-times.desktop"
for path in "$data_home" "$plugin_root" "$plugin_dir" "$plugin_target" "$desktop_target"; do
    if [[ "$path" == *$'\n'* || "$path" == *$'\r'* ]]; then printf '%s\n' 'Paths containing newline characters cannot be removed safely.' >&2; exit 1; fi
done
rm -f -- "$plugin_target" "$desktop_target"
rmdir -- "$plugin_dir" 2>/dev/null || true
rmdir -- "$plugin_root" 2>/dev/null || true
printf '%s\n' 'Prayer Time launcher and plugin removed if present. Settings and calendars were kept.'
