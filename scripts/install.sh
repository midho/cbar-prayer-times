#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
python_bin=$(command -v python3 || true)
if [[ -z "$python_bin" ]] || ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    printf '%s\n' 'Install requires Python 3.10 or newer (python3 was not found or is too old).' >&2; exit 1
fi
if [[ -n "${CBAR_BIN:-}" ]]; then cbar_bin=$CBAR_BIN
elif cbar_bin=$(command -v cbar 2>/dev/null); then :
elif [[ -n "${HOME:-}" && -x "$HOME/.local/bin/cbar" ]]; then cbar_bin="$HOME/.local/bin/cbar"
else printf '%s\n' 'Install requires an existing executable CBar (PATH, ~/.local/bin/cbar, or CBAR_BIN).' >&2; exit 1
fi
if [[ ! -x "$cbar_bin" || -d "$cbar_bin" ]]; then printf 'CBar is not an executable file: %s\n' "$cbar_bin" >&2; exit 1; fi
if [[ "$cbar_bin" != /* ]]; then
    cbar_bin=$("$python_bin" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$cbar_bin")
fi
data_home=${XDG_DATA_HOME:-${HOME:?HOME must be set}/.local/share}
apps_dir="$data_home/applications"; plugin_root="$data_home/cbar-prayer-times"
plugin_dir="$plugin_root/plugins"; plugin_target="$plugin_dir/prayer-times.1m.py"
desktop_target="$apps_dir/cbar-prayer-times.desktop"
for path in "$data_home" "$apps_dir" "$plugin_root" "$plugin_dir" "$plugin_target" "$desktop_target" "$cbar_bin"; do
    if [[ "$path" == *$'\n'* || "$path" == *$'\r'* ]]; then printf '%s\n' 'Paths containing newline characters cannot be installed safely.' >&2; exit 1; fi
done
mkdir -p -- "$plugin_dir" "$apps_dir"
plugin_tmp=$(mktemp "$plugin_dir/.prayer-times.XXXXXX")
desktop_tmp=$(mktemp "$apps_dir/.cbar-prayer-times.XXXXXX")
cleanup() { rm -f -- "$plugin_tmp" "$desktop_tmp"; }; trap cleanup EXIT
cp -- "$project_dir/plugins/prayer-times.1m.py" "$plugin_tmp"; chmod 0755 "$plugin_tmp"
"$python_bin" - "$cbar_bin" "$plugin_dir" "$desktop_tmp" <<'PY'
import sys
cbar, plugins, output = sys.argv[1:]
def quote_arg(value):
    value = value.replace("%", "%%").replace("\\", "\\\\")
    value = value.replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return '"' + value + '"'
def desktop_escape(value): return value.replace("\\", "\\\\")
exec_line = " ".join(quote_arg(arg) for arg in ("/usr/bin/env", f"CBAR_PLUGIN_DIR={plugins}", cbar))
fields = ["[Desktop Entry]", "Type=Application", "Name=Prayer Time", "Icon=io.github.alexprates.CBar-symbolic", "X-CosmicApplet=true", "X-CosmicHoverPopup=Auto", "NoDisplay=true", "StartupNotify=true", "Exec=" + desktop_escape(exec_line), ""]
with open(output, "w", encoding="utf-8", newline="\n") as handle: handle.write("\n".join(fields))
PY
mv -f -- "$plugin_tmp" "$plugin_target"; mv -f -- "$desktop_tmp" "$desktop_target"; trap - EXIT
config_home=${XDG_CONFIG_HOME:-${HOME:?HOME must be set}/.config}; config_path="$config_home/cbar-prayer-times/config.json"
if [[ -f "$config_path" ]]; then printf 'Prayer Time installed. Existing settings kept: %s\n' "$config_path"
else printf 'Prayer Time installed. Next, copy %s/examples/config.json to %s and edit it for your location.\n' "$project_dir" "$config_path"; printf '%s\n' 'Then add Prayer Time from COSMIC panel settings. The launcher is hidden from the app menu.'; fi
printf 'Launcher: %s\n' "$desktop_target"
