#!/usr/bin/env python3
"""Standalone CBar prayer-times plugin.

The plugin downloads one complete Gregorian year from AlAdhan, validates it,
and stores a compact local cache. Normal one-minute refreshes only read that
cache. The current and following year are kept so New Year's Eve also works
without a last-minute lookup.
"""

from __future__ import annotations

import argparse
import calendar
import fcntl
import html
import json
import math
import os
import queue
import re
import sys
import tempfile
import subprocess
import time as clock
import hashlib
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


API_BASE = "https://api.aladhan.com/v1/calendar"
API_DOCS = "https://aladhan.com/prayer-times-api"
CACHE_SCHEMA = 2
RETRY_SECONDS = 15 * 60
LOCK_WAIT_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 3.0
USER_AGENT = "cbar-prayer-times/1.0 (personal desktop plugin)"

PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
DISPLAY_TIMES = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
TIME_RE = re.compile(r"^(\d{2}):(\d{2})(?:\s*\((?:[A-Z]{2,5}(?:[+-]\d{2}:?\d{2})?|[+-]\d{2}(?::?\d{2})?)\))?$")
PLUGIN_PATH = Path(__file__).resolve()
SUPPORTED_METHODS = set(range(0, 6)) | set(range(7, 24))
DEFAULT_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")) / "cbar-prayer-times" / "config.json"
CACHE_BASE = Path(os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")) / "cbar-prayer-times"


class PrayerTimesError(RuntimeError):
    """A user-facing cache or download error."""


def load_settings(path: Path) -> dict[str, object]:
    try:
        with path.open(encoding="utf-8") as handle:
            settings = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PrayerTimesError("Set up a valid config.json to show prayer times") from exc
    if not isinstance(settings, dict):
        raise PrayerTimesError("Configuration must be a JSON object")
    label = settings.get("label")
    lat, lon = settings.get("latitude"), settings.get("longitude")
    zone = settings.get("timezone")
    method, school = settings.get("method_id"), settings.get("school_id")
    adjustment, midnight = settings.get("latitude_adjustment_method"), settings.get("midnight_mode")
    if not isinstance(label, str) or not label.strip() or any(c in label for c in "\r\n|;="):
        raise PrayerTimesError("Configuration label is invalid")
    if isinstance(lat, bool) or not isinstance(lat, (int, float)) or not math.isfinite(lat) or not -90 <= lat <= 90:
        raise PrayerTimesError("Configuration latitude is invalid")
    if isinstance(lon, bool) or not isinstance(lon, (int, float)) or not math.isfinite(lon) or not -180 <= lon <= 180:
        raise PrayerTimesError("Configuration longitude is invalid")
    if not isinstance(zone, str) or not zone:
        raise PrayerTimesError("Configuration timezone is invalid")
    try:
        ZoneInfo(zone)
    except (KeyError, ValueError) as exc:
        raise PrayerTimesError("Configuration timezone is invalid") from exc
    if isinstance(method, bool) or not isinstance(method, int) or method not in SUPPORTED_METHODS:
        raise PrayerTimesError("Configuration method_id is unsupported")
    if isinstance(school, bool) or not isinstance(school, int) or school not in (0, 1):
        raise PrayerTimesError("Configuration school_id must be 0 or 1")
    if isinstance(adjustment, bool) or not isinstance(adjustment, int) or adjustment not in (1, 2, 3):
        raise PrayerTimesError("Configuration latitude_adjustment_method must be 1, 2 or 3")
    if isinstance(midnight, bool) or not isinstance(midnight, int) or midnight not in (0, 1):
        raise PrayerTimesError("Configuration midnight_mode must be 0 or 1")
    return {"label": label.strip(), "latitude": float(lat), "longitude": float(lon), "timezone": zone,
            "method_id": method, "school_id": school, "latitude_adjustment_method": adjustment,
            "midnight_mode": midnight}


def fingerprint(settings: dict[str, object]) -> str:
    encoded = json.dumps({"schema": CACHE_SCHEMA, **settings}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:24]


def storage(settings: dict[str, object]) -> Path:
    return CACHE_BASE / fingerprint(settings)


def cache_path(year: int, settings: dict[str, object]) -> Path:
    return storage(settings) / f"{year}.json"


def retry_path(year: int, settings: dict[str, object]) -> Path:
    return storage(settings) / f"{year}.retry.json"


def api_url(year: int, settings: dict[str, object]) -> str:
    query = urllib.parse.urlencode(
        {
            "latitude": settings["latitude"], "longitude": settings["longitude"],
            "method": settings["method_id"], "school": settings["school_id"],
            "timezonestring": settings["timezone"],
            "latitudeAdjustmentMethod": settings["latitude_adjustment_method"],
            "midnightMode": settings["midnight_mode"],
        }
    )
    return f"{API_BASE}/{year}?{query}"


def clean_time(value: object) -> str:
    match = TIME_RE.match(str(value))
    if not match:
        raise PrayerTimesError(f"Unexpected prayer time: {value!r}")
    hour, minute = (int(part) for part in match.groups()[:2])
    if hour > 23 or minute > 59:
        raise PrayerTimesError(f"Invalid prayer time: {value!r}")
    return f"{hour:02d}:{minute:02d}"


def bounded_json_request(request: urllib.request.Request, timeout: float = REQUEST_TIMEOUT_SECONDS) -> object:
    """Bound connection, body reads, DNS, and JSON decoding with one deadline."""
    result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def read_response() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result.put((True, json.load(response)))
        except BaseException as exc:
            result.put((False, exc))

    threading.Thread(target=read_response, name="prayer-calendar-read", daemon=True).start()
    try:
        succeeded, value = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise PrayerTimesError("The timetable request exceeded its time limit") from exc
    if not succeeded:
        if isinstance(value, BaseException):
            raise value
        raise PrayerTimesError("The timetable response could not be read")
    return value


def compact_annual_response(payload: object, year: int, settings: dict[str, object]) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise PrayerTimesError("The prayer-times service returned an error")

    months = payload.get("data")
    if not isinstance(months, dict):
        raise PrayerTimesError("The annual timetable has an unexpected format")

    days: dict[str, dict[str, object]] = {}
    metadata_seen = False

    for month_number in range(1, 13):
        records = months.get(str(month_number))
        if not isinstance(records, list):
            raise PrayerTimesError(f"Month {month_number} is missing from the timetable")

        for record in records:
            try:
                gregorian = record["date"]["gregorian"]
                parsed_date = datetime.strptime(
                    gregorian["date"], "%d-%m-%Y"
                ).date()
                raw_timings = record["timings"]
                hijri = record["date"]["hijri"]
                hijri_day = str(hijri["day"])
                hijri_month = str(hijri["month"]["en"])
                hijri_year = str(hijri["year"])
                meta = record["meta"]
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise PrayerTimesError("A timetable entry is incomplete") from exc

            if parsed_date.year != year or parsed_date.month != month_number:
                raise PrayerTimesError("The service returned a date in the wrong year or month")

            if not isinstance(raw_timings, dict):
                raise PrayerTimesError("A timetable entry has no valid prayer times")
            try:
                timings = {name: clean_time(raw_timings[name]) for name in DISPLAY_TIMES}
            except (KeyError, TypeError) as exc:
                raise PrayerTimesError("A timetable entry is missing a prayer time") from exc
            if parsed_date.isoformat() in days:
                raise PrayerTimesError("The annual timetable contains duplicate dates")
            days[parsed_date.isoformat()] = {
                "timings": timings,
                "hijri": {
                    "day": safe_menu_text(hijri_day),
                    "month": safe_menu_text(hijri_month),
                    "year": safe_menu_text(hijri_year),
                },
            }

            if not isinstance(meta, dict):
                raise PrayerTimesError("Timetable metadata is invalid")
            expected_school = "HANAFI" if settings["school_id"] == 1 else "STANDARD"
            checks = ((meta.get("method", {}).get("id") if isinstance(meta.get("method"), dict) else None, settings["method_id"]),
                      (meta.get("school"), expected_school),
                      (meta.get("timezone"), settings["timezone"]),
                      (meta.get("latitude"), settings["latitude"]),
                      (meta.get("longitude"), settings["longitude"]),
                      (meta.get("latitudeAdjustmentMethod"), {1:"MIDDLE_OF_THE_NIGHT",2:"ONE_SEVENTH",3:"ANGLE_BASED"}[settings["latitude_adjustment_method"]]),
                      (meta.get("midnightMode"), {0:"STANDARD",1:"JAFARI"}[settings["midnight_mode"]]))
            for index, (actual, expected) in enumerate(checks):
                if actual is None:
                    continue
                if index in (3, 4):
                    if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual) or not math.isclose(actual, expected, abs_tol=1e-5):
                        raise PrayerTimesError("The service returned conflicting calculation metadata")
                elif str(actual) != str(expected):
                    raise PrayerTimesError("The service returned conflicting calculation metadata")
            metadata_seen = True

    expected_days = 366 if calendar.isleap(year) else 365
    if set(days) != {date.fromordinal(date(year, 1, 1).toordinal()+i).isoformat() for i in range(expected_days)}:
        raise PrayerTimesError(
            f"Expected {expected_days} days but received {len(days)}"
        )
    if not metadata_seen:
        raise PrayerTimesError("The service omitted calculation metadata")

    return {
        "schema": CACHE_SCHEMA,
        "year": year,
        "generated_at": datetime.now(ZoneInfo(str(settings["timezone"]))).isoformat(timespec="seconds"),
        "source": API_DOCS,
        "settings": settings,
        "days": days,
    }


def write_json(path: Path, data: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = path
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def fetch_year(year: int, settings: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        api_url(year, settings),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        payload = bounded_json_request(request)
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrayerTimesError(f"Could not download the {year} timetable") from exc

    compact = compact_annual_response(payload, year, settings)
    try:
        write_json(cache_path(year, settings), compact)
    except OSError as exc:
        raise PrayerTimesError(f"Could not store the {year} timetable") from exc
    return compact


def notify_desktop(summary: str, body: str = "", replace_id: int = 0, expire_ms: int = 0) -> int | None:
    """Send a best-effort native notification through the user's session bus."""
    argv = [
        "busctl", "--user", "--timeout=2", "call",
        "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications", "Notify", "susssasa{sv}i",
        "Prayer Time", str(replace_id), "", html.escape(summary, quote=True),
        html.escape(body, quote=True), "0", "0", str(expire_ms),
    ]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=2.5, check=False)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if result.returncode != 0:
        return None
    if not isinstance(result.stdout, str):
        return None
    match = re.fullmatch(r"\s*u\s+([0-9]{1,10})\s*", result.stdout)
    if not match:
        return None
    notification_id = int(match.group(1))
    return notification_id if 0 < notification_id <= 0xFFFFFFFF else None


def last_updated_text(cache: dict[str, object], zone: ZoneInfo) -> str:
    try:
        timestamp = datetime.fromisoformat(str(cache["generated_at"]))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=zone)
        return timestamp.astimezone(zone).strftime("Last updated: %Y-%m-%d %H:%M")
    except (KeyError, TypeError, ValueError, OverflowError):
        return "Last updated: unavailable"


def read_cache(year: int, settings: dict[str, object]) -> dict[str, object] | None:
    path = cache_path(year, settings)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    if (
        not isinstance(data, dict)
        or data.get("schema") != CACHE_SCHEMA
        or data.get("year") != year
        or not isinstance(data.get("days"), dict)
        or data.get("settings") != settings
    ):
        return None
    try:
        dates = {date(year, 1, 1) + timedelta(days=i) for i in range(366 if calendar.isleap(year) else 365)}
        days = data["days"]
        if set(days) != {d.isoformat() for d in dates}:
            return None
        for key, entry in days.items():
            if not isinstance(entry, dict) or set(entry) != {"timings", "hijri"}:
                return None
            if not isinstance(entry["timings"], dict) or set(entry["timings"]) != set(DISPLAY_TIMES):
                return None
            for value in entry["timings"].values():
                if not isinstance(value, str) or clean_time(value) != value:
                    return None
            hijri = entry["hijri"]
            if not isinstance(hijri, dict) or not all(isinstance(hijri.get(k), str) and hijri[k] for k in ("day", "month", "year")):
                return None
    except (TypeError, ValueError, KeyError, PrayerTimesError):
        return None
    return data


def ensure_year(year: int, settings: dict[str, object], *, force: bool = False) -> dict[str, object]:
    if not force:
        cached = read_cache(year, settings)
        if cached is not None:
            return cached

    # COSMIC starts one CBar process per monitor. Lock the annual download so
    # both panels cannot request and replace the same cache at the same time.
    lock_path = storage(settings) / f".{year}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+", encoding="utf-8") as lock:
        lock_path.chmod(0o600)
        deadline = clock.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if clock.monotonic() >= deadline:
                    raise PrayerTimesError("Another update is still running; try again shortly")
                clock.sleep(.05)
        if not force:
            cached = read_cache(year, settings)
            if cached is not None:
                return cached
        rp = retry_path(year, settings)
        if not force:
            try:
                state = json.loads(rp.read_text(encoding="utf-8"))
                if clock.time() < float(state["retry_after"]):
                    raise PrayerTimesError(f"The {year} timetable is temporarily unavailable")
            except FileNotFoundError:
                pass
            except (ValueError, KeyError, TypeError, json.JSONDecodeError, OSError):
                pass
        try:
            result = fetch_year(year, settings)
            rp.unlink(missing_ok=True)
            return result
        except PrayerTimesError:
            try:
                write_json(rp, {"retry_after": clock.time() + RETRY_SECONDS})
            except OSError:
                # Keep the original fetch/store failure. If the cache directory
                # is unwritable, a persistent retry deadline cannot be saved.
                pass
            raise


def entry_for(day: date, caches: dict[int, dict[str, object]], settings: dict[str, object]) -> dict[str, object]:
    if day.year not in caches:
        caches[day.year] = ensure_year(day.year, settings)
    try:
        return caches[day.year]["days"][day.isoformat()]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise PrayerTimesError(f"No cached prayer times for {day.isoformat()}") from exc


def prayer_datetime(day: date, value: str, zone: ZoneInfo) -> datetime:
    hour, minute = (int(part) for part in value.split(":", 1))
    return datetime.combine(day, time(hour, minute), zone)


def find_next_prayer(
    now: datetime, caches: dict[int, dict[str, object]], settings: dict[str, object]
) -> tuple[str, datetime, bool]:
    today = entry_for(now.date(), caches, settings)
    zone = ZoneInfo(str(settings["timezone"]))
    timings = today["timings"]

    for prayer in PRAYERS:
        moment = prayer_datetime(now.date(), timings[prayer], zone)  # type: ignore[index]
        elapsed = (now.astimezone(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds()
        if 0 <= elapsed < 60:
            return prayer, moment, True
        if moment.astimezone(timezone.utc) > now.astimezone(timezone.utc):
            return prayer, moment, False

    tomorrow = now.date() + timedelta(days=1)
    tomorrow_entry = entry_for(tomorrow, caches, settings)
    moment = prayer_datetime(tomorrow, tomorrow_entry["timings"]["Fajr"], zone)  # type: ignore[index]
    return "Fajr", moment, False


def countdown(now: datetime, moment: datetime, is_now: bool) -> str:
    if is_now:
        return "now"
    seconds = max(0, math.ceil((moment.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()))
    minutes = max(1, math.ceil(seconds / 60))
    hours, remaining = divmod(minutes, 60)
    if hours:
        return f"{hours}h {remaining:02d}m"
    return f"{minutes}m"


def render(now: datetime, settings: dict[str, object], config_path: Path) -> None:
    config_token = str(config_path)
    if any(c in config_token for c in "\r\n") or ('"' in config_token and "'" in config_token):
        raise PrayerTimesError("Config path cannot be used safely in the panel action")
    quote = "'" if '"' in config_token else '"'
    caches: dict[int, dict[str, object]] = {}
    prefetch_warning: str | None = None

    caches[now.year] = ensure_year(now.year, settings)
    try:
        caches[now.year + 1] = ensure_year(now.year + 1, settings)
    except (PrayerTimesError, OSError) as exc:
        # The current year remains fully usable. Retry the prefetch next minute.
        prefetch_warning = safe_menu_text(exc)

    next_name, next_moment, is_now = find_next_prayer(now, caches, settings)
    remaining = countdown(now, next_moment, is_now)
    print(f"🕌 {next_name} {next_moment:%H:%M} · {remaining}")
    print("---")

    today = entry_for(now.date(), caches, settings)
    friendly_date = now.strftime("%A, %d %B %Y")
    hijri = today["hijri"]
    print(f"{settings['label']} · {friendly_date} | disabled=true")
    print(
        f"{safe_menu_text(hijri['day'])} {safe_menu_text(hijri['month'])} {safe_menu_text(hijri['year'])} AH | disabled=true"
    )
    print("---")

    for name in DISPLAY_TIMES:
        marker = "→ " if next_moment.date() == now.date() and name == next_name else "  "
        print(f"{marker}{name} — {today['timings'][name]} | disabled=true")

    if next_moment.date() != now.date():
        print("---")
        print(
            f"→ Next: {next_name} tomorrow at {next_moment:%H:%M} · {remaining} | disabled=true"
        )

    print("---")
    print(f"Method {settings['method_id']} · {'Hanafi' if settings['school_id'] == 1 else 'Standard'} Asr · {settings['timezone']} | disabled=true")
    available = [str(y) for y in (now.year, now.year + 1) if read_cache(y, settings) is not None]
    print(f"Stored years: {', '.join(available) if available else 'none'} | disabled=true")
    print(f"{last_updated_text(caches[now.year], ZoneInfo(str(settings['timezone'])))} | disabled=true")
    if prefetch_warning:
        print(f"Next-year prefetch pending: {prefetch_warning} | disabled=true")
    print(
        f"Refresh annual cache | param1=--refresh-cache param2=--config param3={quote}{config_token}{quote} param4=--notify refresh=true"
    )
    print(f"Prayer-time source | href={API_DOCS}")


def parse_now(value: str | None, zone: ZoneInfo) -> datetime:
    if value is None:
        return datetime.now(zone)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PrayerTimesError("--at must be an ISO date and time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def render_error(error: Exception) -> None:
    message = safe_menu_text(str(error)) or error.__class__.__name__
    print("Prayer times unavailable")
    print("---")
    print(f"{message} | disabled=true")
    print("Try again | refresh=true")
    print(f"Prayer-time source | href={API_DOCS}")


def safe_menu_text(value: object) -> str:
    """Make external or user supplied text harmless in CBar's line format."""
    text = " ".join(str(value).replace("|", " ").replace(";", " ").split())
    return text[:180]


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--at")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--help", action="help")
    args = parser.parse_args()
    if args.notify and not args.refresh_cache:
        parser.error("--notify can only be used with --refresh-cache")
    notification_id = None
    if args.notify:
        notification_id = notify_desktop("Refreshing prayer times…", replace_id=0, expire_ms=0)

    try:
        config_path = args.config.expanduser().resolve()
        settings = load_settings(config_path)
        zone = ZoneInfo(str(settings["timezone"]))
        now = parse_now(args.at, zone)
        if args.refresh_cache:
            for year in (now.year, now.year + 1):
                path = cache_path(year, settings)
                ensure_year(year, settings, force=True)
                print(f"Cached {year}: {path}")
            if args.notify:
                notify_desktop("Prayer times updated", "Both calendar years were refreshed.", notification_id or 0, 6000)
            return 0
        render(now, settings, config_path)
        return 0
    except Exception as exc:
        if args.refresh_cache:
            if args.notify:
                notify_desktop("Prayer times refresh failed", safe_menu_text(exc), notification_id or 0, 10000)
            print(str(exc), file=sys.stderr)
            return 1
        render_error(exc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
