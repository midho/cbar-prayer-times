"""Keep the public configuration template free of personal settings."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples/config.json"


class PrivacyTests(unittest.TestCase):
    def test_template_has_no_location_or_calculation_choices(self):
        settings = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(settings.pop("label"), "Your location")
        self.assertEqual(set(settings), {
            "latitude", "longitude", "timezone", "method_id", "school_id",
            "latitude_adjustment_method", "midnight_mode",
        })
        for field, value in settings.items():
            with self.subTest(field=field):
                self.assertIsNone(value)

    def test_incomplete_template_cannot_select_a_location(self):
        spec = importlib.util.spec_from_file_location(
            "privacy_test_plugin", ROOT / "plugins/prayer-times.1m.py"
        )
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        with self.assertRaises(plugin.PrayerTimesError):
            plugin.load_settings(TEMPLATE)


if __name__ == "__main__":
    unittest.main()
