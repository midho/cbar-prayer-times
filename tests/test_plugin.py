"""Offline regression tests for the standalone panel plugin."""
import contextlib
import importlib.util
import io
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prayer_plugin", ROOT / "plugins/prayer-times.1m.py")
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)
SETTINGS = {
    "label": "Synthetic test location", "latitude": 0.0, "longitude": 0.0,
    "timezone": "Europe/London", "method_id": 2, "school_id": 0,
    "latitude_adjustment_method": 3, "midnight_mode": 0,
}


def annual_fixture(year):
    months = {str(month): [] for month in range(1, 13)}
    count = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    for offset in range(count):
        day = date.fromordinal(date(year, 1, 1).toordinal() + offset)
        metadata = {
            "method": {"id": 2}, "school": "STANDARD", "timezone": "Europe/London",
            "latitude": 0.0, "longitude": 0.0,
            "latitudeAdjustmentMethod": "ANGLE_BASED", "midnightMode": "STANDARD",
        }
        months[str(day.month)].append({
            "date": {"gregorian": {"date": day.strftime("%d-%m-%Y")},
                     "hijri": {"day": "1", "month": {"en": "Month"}, "year": "1448"}},
            "timings": {"Fajr": "05:00 (CET)", "Sunrise": "06:00 (+04)",
                        "Dhuhr": "12:00 (+0545)", "Asr": "15:00 (-03)",
                        "Maghrib": "18:00 (CEST)", "Isha": "20:00"},
            "meta": metadata,
        })
    return {"code": 200, "data": months}


def child_ensure(count, year, settings):
    """Multiprocessing target; fetch_year is replaced before fork by the test."""
    PLUGIN.ensure_year(year, settings)


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        PLUGIN.CACHE_BASE = Path(self.temp.name) / "cache"

    def test_cli_help_missing_and_invalid_configuration(self):
        script = str(ROOT / "plugins/prayer-times.1m.py")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XDG_CACHE_HOME=str(Path(self.temp.name) / "cli-cache"))
        help_result = subprocess.run([sys.executable, script, "--help"], text=True, capture_output=True, env=env)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--config", help_result.stdout)
        for value in (None, "{bad json"):
            config = Path(self.temp.name) / ("missing.json" if value is None else "invalid.json")
            if value is not None:
                config.write_text(value, encoding="utf-8")
            result = subprocess.run([sys.executable, script, "--config", str(config)], text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            self.assertIn("Prayer times unavailable", result.stdout)

    def test_relative_config_resolves_from_current_working_directory(self):
        config = Path(self.temp.name) / "relative.json"
        config.write_text(json.dumps(SETTINGS), encoding="utf-8")
        previous = Path.cwd()
        try:
            os.chdir(self.temp.name)
            with patch.object(sys, "argv", ["plugin", "--config", "relative.json"]), patch.object(PLUGIN, "render") as render:
                self.assertEqual(PLUGIN.main(), 0)
            self.assertEqual(render.call_args.args[2], config.resolve())
        finally:
            os.chdir(previous)

    def test_settings_validation_and_all_fingerprint_inputs(self):
        config = Path(self.temp.name) / "config.json"
        config.write_text(json.dumps(SETTINGS), encoding="utf-8")
        self.assertEqual(PLUGIN.load_settings(config), SETTINGS)
        invalid = (("label", "x|refresh=true"), ("latitude", True), ("longitude", 181),
                   ("timezone", "No/Such_Zone"), ("method_id", 99), ("school_id", 2),
                   ("latitude_adjustment_method", 0), ("midnight_mode", 2))
        for key, value in invalid:
            config.write_text(json.dumps(dict(SETTINGS, **{key: value})), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(PLUGIN.PrayerTimesError):
                PLUGIN.load_settings(config)
        for key, value in (("label", "Other"), ("latitude", 10.0), ("longitude", 20.0),
                           ("timezone", "UTC"), ("method_id", 3), ("school_id", 1),
                           ("latitude_adjustment_method", 2), ("midnight_mode", 1)):
            self.assertNotEqual(PLUGIN.fingerprint(SETTINGS), PLUGIN.fingerprint(dict(SETTINGS, **{key: value})), key)

    def test_full_365_and_366_day_years_and_date_record_rejections(self):
        for year, expected in ((2025, 365), (2024, 366)):
            compact = PLUGIN.compact_annual_response(annual_fixture(year), year, SETTINGS)
            self.assertEqual(len(compact["days"]), expected)
            self.assertIn(f"{year}-02-29", compact["days"]) if expected == 366 else None
        makers = (
            lambda p: p["data"]["2"].pop(),
            lambda p: p["data"]["1"].append(p["data"]["1"][0]),
            lambda p: p["data"]["1"][0]["date"]["gregorian"].update(date="01-02-2024"),
            lambda p: p["data"]["1"][0]["timings"].update(Fajr="25:90"),
            lambda p: p["data"]["1"][0]["timings"].pop("Fajr"),
            lambda p: p["data"]["1"][0]["date"]["hijri"].pop("month"),
        )
        for mutate in makers:
            payload = annual_fixture(2024)
            mutate(payload)
            with self.assertRaises(PLUGIN.PrayerTimesError):
                PLUGIN.compact_annual_response(payload, 2024, SETTINGS)

    def test_corrupt_cache_shapes_and_types_are_invalid_not_exceptions(self):
        valid = PLUGIN.compact_annual_response(annual_fixture(2024), 2024, SETTINGS)
        bad_values = ([], {"days": []}, {"days": {"2024-01-01": None}},
                      {"days": {"2024-01-01": {"timings": [], "hijri": {}}}})
        for bad in bad_values:
            path = PLUGIN.cache_path(2024, SETTINGS)
            payload = bad if isinstance(bad, list) else {**valid, **bad}
            PLUGIN.write_json(path, payload)
            self.assertIsNone(PLUGIN.read_cache(2024, SETTINGS))
        PLUGIN.cache_path(2024, SETTINGS).write_bytes(b"\xff\xfe")
        self.assertIsNone(PLUGIN.read_cache(2024, SETTINGS))
        complete = PLUGIN.compact_annual_response(annual_fixture(2024), 2024, SETTINGS)
        complete["days"]["2024-08-12"]["timings"]["Fajr"] = "05:00 (CET)"
        PLUGIN.write_json(PLUGIN.cache_path(2024, SETTINGS), complete)
        self.assertIsNone(PLUGIN.read_cache(2024, SETTINGS))

    def test_zone_suffixes_and_bad_suffix(self):
        for value in ("05:57 (+04)", "05:57 (+0545)", "05:57 (-03)", "05:57 (CET)", "05:57 (CEST)"):
            self.assertEqual(PLUGIN.clean_time(value), "05:57")
        for value in ("05:57 (GMT extra)", "05:57 (04)", "05:57 garbage"):
            with self.assertRaises(PLUGIN.PrayerTimesError):
                PLUGIN.clean_time(value)

    def test_utc_countdowns_and_prayer_now_rollovers(self):
        examples = (("2026-03-28T21:00:00+00:00", "2026-03-29T05:29:00+01:00", "7h 29m"),
                    ("2026-10-24T21:00:00+01:00", "2026-10-25T05:19:00+00:00", "9h 19m"))
        zone = ZoneInfo("Europe/London")
        for start, moment, expected in examples:
            local_start = datetime.fromisoformat(start).astimezone(zone)
            local_moment = datetime.fromisoformat(moment).astimezone(zone)
            self.assertEqual(PLUGIN.countdown(local_start, local_moment, False), expected)
        utc = ZoneInfo("UTC")
        self.assertEqual(PLUGIN.countdown(datetime(2026, 1, 1, tzinfo=utc), datetime(2026, 1, 1, 0, 0, 1, tzinfo=utc), False), "1m")
        self.assertEqual(PLUGIN.countdown(datetime(2026, 1, 1, tzinfo=utc), datetime(2026, 1, 1, 0, 1, 1, tzinfo=utc), False), "2m")
        base = {"timings": {"Fajr": "05:00", "Dhuhr": "12:00", "Asr": "15:00", "Maghrib": "18:00", "Isha": "20:00"}}
        by_day = {"2026-05-01": base, "2026-05-02": base, "2027-01-01": base}
        caches = {2026: {"days": by_day}, 2027: {"days": {"2027-01-01": base}}}
        def lookup(day, _caches, _settings):
            return by_day.get(day.isoformat(), caches.get(day.year, {}).get("days", {}).get(day.isoformat(), base))
        with patch.object(PLUGIN, "entry_for", side_effect=lookup):
            at = datetime(2026, 5, 1, 12, 0, tzinfo=zone)
            self.assertEqual(PLUGIN.find_next_prayer(at, caches, SETTINGS), ("Dhuhr", at, True))
            self.assertEqual(PLUGIN.find_next_prayer(at.replace(second=59), caches, SETTINGS)[2], True)
            self.assertEqual(PLUGIN.find_next_prayer(at.replace(minute=1), caches, SETTINGS)[0:3:2], ("Asr", False))
            self.assertEqual(PLUGIN.find_next_prayer(datetime(2026, 5, 1, 20, 1, tzinfo=zone), caches, SETTINGS)[0], "Fajr")
            self.assertEqual(PLUGIN.find_next_prayer(datetime(2026, 12, 31, 20, 1, tzinfo=zone), caches, SETTINGS)[1].date(), date(2027, 1, 1))

    def test_existing_year_cache_causes_zero_requests(self):
        for year in (2026, 2027):
            PLUGIN.write_json(PLUGIN.cache_path(year, SETTINGS), PLUGIN.compact_annual_response(annual_fixture(year), year, SETTINGS))
        with patch.object(PLUGIN.urllib.request, "urlopen", side_effect=AssertionError("network used")) as request:
            self.assertEqual(PLUGIN.ensure_year(2026, SETTINGS)["year"], 2026)
            self.assertEqual(PLUGIN.ensure_year(2027, SETTINGS)["year"], 2027)
            request.assert_not_called()

    def test_invalid_response_bytes_use_retry_cooldown_and_keep_current_display(self):
        current = PLUGIN.compact_annual_response(annual_fixture(2026), 2026, SETTINGS)
        PLUGIN.write_json(PLUGIN.cache_path(2026, SETTINGS), current)
        now = datetime(2026, 10, 12, 13, 0, tzinfo=ZoneInfo("Europe/London"))
        output = io.StringIO()
        with patch.object(PLUGIN.urllib.request, "urlopen", return_value=io.BytesIO(b"\xff")) as request, contextlib.redirect_stdout(output):
            PLUGIN.render(now, SETTINGS, Path("config.json"))
            PLUGIN.render(now, SETTINGS, Path("config.json"))
        self.assertEqual(request.call_count, 1)
        self.assertIn("Asr", output.getvalue())
        self.assertIn("Fajr — 05:00", output.getvalue())
        self.assertIn("Next-year prefetch pending", output.getvalue())
        self.assertNotIn("Prayer times unavailable", output.getvalue())

    def test_next_year_lock_cache_and_retry_state_failures_keep_current_display(self):
        current = PLUGIN.compact_annual_response(annual_fixture(2026), 2026, SETTINGS)
        following = json.dumps(annual_fixture(2027)).encode("utf-8")
        PLUGIN.write_json(PLUGIN.cache_path(2026, SETTINGS), current)
        now = datetime(2026, 10, 12, 13, 0, tzinfo=ZoneInfo("Europe/London"))
        real_open = Path.open

        def render_text():
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                PLUGIN.render(now, SETTINGS, Path("config.json"))
            text = capture.getvalue()
            self.assertIn("Fajr — 05:00", text)
            warning = next(line for line in text.splitlines() if line.startswith("Next-year prefetch pending:"))
            self.assertNotIn("| refresh=true", warning)
            return text

        with patch.object(Path, "open", autospec=True, side_effect=lambda path, *a, **kw: (_ for _ in ()).throw(PermissionError("blocked| refresh=true\nmenu")) if path.name == ".2027.lock" else real_open(path, *a, **kw)):
            self.assertIn("Next-year prefetch pending", render_text())

        real_write = PLUGIN.write_json
        def deny_next_calendar(path, data):
            if path.name == "2027.json":
                raise PermissionError("next year cache is read-only")
            return real_write(path, data)
        with patch.object(PLUGIN.urllib.request, "urlopen", return_value=io.BytesIO(following)), patch.object(PLUGIN, "write_json", side_effect=deny_next_calendar):
            self.assertIn("Could not store the 2027 timetable", render_text())
        PLUGIN.retry_path(2027, SETTINGS).unlink(missing_ok=True)

        def deny_retry(path, data):
            if path.name == "2027.retry.json":
                raise PermissionError("retry state is read-only")
            return real_write(path, data)
        with patch.object(PLUGIN.urllib.request, "urlopen", side_effect=OSError("offline")), patch.object(PLUGIN, "write_json", side_effect=deny_retry):
            text = render_text()
        self.assertIn("Could not download the 2027 timetable", text)
        self.assertNotIn("retry state is read-only", text)

    def test_offline_retry_then_eligible_retry_and_force_preserves_good_cache(self):
        with patch.object(PLUGIN.urllib.request, "urlopen", side_effect=OSError("offline")) as request, patch.object(PLUGIN.clock, "time", return_value=1000):
            for _ in range(2):
                with self.assertRaises(PLUGIN.PrayerTimesError): PLUGIN.ensure_year(2028, SETTINGS)
            self.assertEqual(request.call_count, 1)
        good = PLUGIN.compact_annual_response(annual_fixture(2028), 2028, SETTINGS)
        with patch.object(PLUGIN, "fetch_year", return_value=good) as fetch, patch.object(PLUGIN.clock, "time", return_value=1900):
            self.assertEqual(PLUGIN.ensure_year(2028, SETTINGS)["year"], 2028)
            fetch.assert_called_once()
        self.assertFalse(PLUGIN.retry_path(2028, SETTINGS).exists())
        PLUGIN.write_json(PLUGIN.cache_path(2029, SETTINGS), PLUGIN.compact_annual_response(annual_fixture(2029), 2029, SETTINGS))
        with patch.object(PLUGIN, "fetch_year", side_effect=PLUGIN.PrayerTimesError("offline")):
            with self.assertRaises(PLUGIN.PrayerTimesError): PLUGIN.ensure_year(2029, SETTINGS, force=True)
        self.assertIsNotNone(PLUGIN.read_cache(2029, SETTINGS))

    def test_multiprocess_lock_recheck_fetches_once(self):
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("OS file-lock test requires fork")
        context = multiprocessing.get_context("fork")
        counter = context.Value("i", 0)
        def fake_fetch(year, settings):
            with counter.get_lock(): counter.value += 1
            time.sleep(0.2)
            data = PLUGIN.compact_annual_response(annual_fixture(year), year, settings)
            PLUGIN.write_json(PLUGIN.cache_path(year, settings), data)
            return data
        processes = []
        with patch.object(PLUGIN, "fetch_year", side_effect=fake_fetch):
            for _ in range(2):
                process = context.Process(target=child_ensure, args=(counter, 2030, SETTINGS))
                process.start(); processes.append(process)
            for process in processes: process.join(10)
        self.assertTrue(all(not process.is_alive() and process.exitcode == 0 for process in processes))
        self.assertEqual(counter.value, 1)

    def test_action_path_spaces_and_text_control_safety(self):
        self.assertEqual(PLUGIN.safe_menu_text("evil|refresh=true\nnext"), "evil refresh=true next")
        self.assertEqual(PLUGIN.safe_menu_text("x; refresh=true"), "x refresh=true")
        zone = ZoneInfo("UTC")
        now = datetime(2026, 1, 1, 0, 0, tzinfo=zone)
        annual = PLUGIN.compact_annual_response(annual_fixture(2026), 2026, SETTINGS)
        with patch.object(PLUGIN, "ensure_year", return_value=annual), patch.object(PLUGIN, "read_cache", return_value=annual):
            output = io.StringIO()
            with contextlib.redirect_stdout(output): PLUGIN.render(now, SETTINGS, Path("/tmp/config path/config.json"))
        self.assertIn('param3="/tmp/config path/config.json"', output.getvalue())
        for path, expected in ((Path("has'apostrophe/config.json"), 'param3="has\'apostrophe/config.json"'),
                               (Path('has"double/config.json'), "param3='has\"double/config.json'")):
            output = io.StringIO()
            with patch.object(PLUGIN, "ensure_year", return_value=annual), patch.object(PLUGIN, "read_cache", return_value=annual), contextlib.redirect_stdout(output):
                PLUGIN.render(now, SETTINGS, path)
            self.assertIn(expected, output.getvalue())
        with self.assertRaises(PLUGIN.PrayerTimesError): PLUGIN.render(now, SETTINGS, Path("bad'\"path"))

    def test_slow_response_body_obeys_total_deadline(self):
        class SlowResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): time.sleep(0.15); return b'{"code":200}'
        request = PLUGIN.urllib.request.Request("https://example.invalid/")
        with patch.object(PLUGIN.urllib.request, "urlopen", return_value=SlowResponse()):
            started = time.monotonic()
            with self.assertRaises(PLUGIN.PrayerTimesError):
                PLUGIN.bounded_json_request(request, timeout=0.02)
            self.assertLess(time.monotonic() - started, 0.1)

    def test_empty_xdg_values_fall_back_to_home_defaults(self):
        import importlib.util
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "", "XDG_CACHE_HOME": ""}):
            spec = importlib.util.spec_from_file_location("prayer_empty_xdg", ROOT / "plugins/prayer-times.1m.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertEqual(module.DEFAULT_CONFIG, Path.home() / ".config/cbar-prayer-times/config.json")
        self.assertEqual(module.CACHE_BASE, Path.home() / ".cache/cbar-prayer-times")

    def test_notify_refresh_progress_order_id_reuse_and_failure(self):
        config = Path(self.temp.name) / "notify.json"
        config.write_text(json.dumps(SETTINGS), encoding="utf-8")
        events = []
        def notify(summary, body="", replace_id=0, expire_ms=0):
            events.append(("notify", summary, body, replace_id, expire_ms))
            return 41 if summary == "Refreshing prayer times…" else None
        def ensure(year, _settings, *, force=False):
            events.append(("ensure", year, force))
        argv = ["plugin", "--refresh-cache", "--notify", "--config", str(config), "--at", "2026-10-12T13:00:00+02:00"]
        with patch.object(sys, "argv", argv), patch.object(PLUGIN, "notify_desktop", side_effect=notify), patch.object(PLUGIN, "ensure_year", side_effect=ensure):
            self.assertEqual(PLUGIN.main(), 0)
        self.assertEqual([event[0] for event in events], ["notify", "ensure", "ensure", "notify"])
        self.assertEqual(events[0][1], "Refreshing prayer times…")
        self.assertEqual(events[-1][1], "Prayer times updated")
        self.assertEqual(events[-1][3:], (41, 6000))

        events.clear()
        def fail_second(year, _settings, *, force=False):
            events.append(("ensure", year))
            if year == 2027:
                raise PLUGIN.PrayerTimesError("offline | refresh=true")
        with patch.object(sys, "argv", argv), patch.object(PLUGIN, "notify_desktop", side_effect=notify), patch.object(PLUGIN, "ensure_year", side_effect=fail_second):
            self.assertEqual(PLUGIN.main(), 1)
        self.assertEqual(events[-1][1], "Prayer times refresh failed")
        self.assertEqual(events[-1][3:], (41, 10000))
        self.assertNotIn("|", events[-1][2])

    def test_notification_opt_in_only_and_best_effort(self):
        config = Path(self.temp.name) / "notify.json"
        config.write_text(json.dumps(SETTINGS), encoding="utf-8")
        refresh_argv = ["plugin", "--refresh-cache", "--config", str(config), "--at", "2026-10-12T13:00:00+02:00"]
        with patch.object(sys, "argv", refresh_argv), patch.object(PLUGIN, "notify_desktop") as notify, patch.object(PLUGIN, "ensure_year"):
            self.assertEqual(PLUGIN.main(), 0)
            notify.assert_not_called()
        ordinary_argv = ["plugin", "--config", str(config), "--at", "2026-10-12T13:00:00+02:00"]
        with patch.object(sys, "argv", ordinary_argv), patch.object(PLUGIN, "notify_desktop") as notify, patch.object(PLUGIN, "render"):
            self.assertEqual(PLUGIN.main(), 0)
            notify.assert_not_called()
        flagged = ["plugin", "--refresh-cache", "--notify", "--config", str(config), "--at", "2026-10-12T13:00:00+02:00"]
        with patch.object(sys, "argv", flagged), patch.object(PLUGIN, "notify_desktop", return_value=None), patch.object(PLUGIN, "ensure_year"):
            self.assertEqual(PLUGIN.main(), 0)

    def test_busctl_helper_handles_failures_and_escapes_markup_without_shell(self):
        result = subprocess.CompletedProcess([], 0, "u 73\n", "")
        with patch.object(PLUGIN.subprocess, "run", return_value=result) as run:
            self.assertEqual(PLUGIN.notify_desktop("<start>", "<img src=x onerror=alert(1)>", 12, 6000), 73)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "busctl")
        self.assertIn("&lt;start&gt;", argv)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", argv)
        self.assertEqual(run.call_args.kwargs["timeout"], 2.5)
        self.assertNotIn("shell", run.call_args.kwargs)
        for failure in (FileNotFoundError("busctl"), subprocess.TimeoutExpired("busctl", 2.5), ValueError("embedded null byte")):
            with patch.object(PLUGIN.subprocess, "run", side_effect=failure):
                self.assertIsNone(PLUGIN.notify_desktop("summary"))
        for bad_result in (subprocess.CompletedProcess([], 1, "", "no service"),
                           subprocess.CompletedProcess([], 0, "bad reply", ""),
                           subprocess.CompletedProcess([], 0, "u 0", ""),
                           subprocess.CompletedProcess([], 0, "u " + "9" * 5000, ""),
                           subprocess.CompletedProcess([], 0, "u 4294967296", ""),
                           subprocess.CompletedProcess([], 0, None, "")):
            with patch.object(PLUGIN.subprocess, "run", return_value=bad_result):
                self.assertIsNone(PLUGIN.notify_desktop("summary"))

    def test_last_updated_timestamp_is_local_and_invalid_value_is_safe(self):
        zone = ZoneInfo("Europe/London")
        self.assertEqual(PLUGIN.last_updated_text({"generated_at": "2026-10-12T12:00:00+00:00"}, zone), "Last updated: 2026-10-12 13:00")
        self.assertEqual(PLUGIN.last_updated_text({}, zone), "Last updated: unavailable")
        self.assertEqual(PLUGIN.last_updated_text({"generated_at": "not-a-date"}, zone), "Last updated: unavailable")

if __name__ == "__main__":
    unittest.main()
