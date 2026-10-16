"""Keep the public example limited to documented demonstration settings."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/config.json"
DEMO_SETTINGS = {
    "label": "London (example)",
    "latitude": 51.5,
    "longitude": -0.1,
    "timezone": "Europe/London",
    "method_id": 3,
    "school_id": 0,
    "latitude_adjustment_method": 3,
    "midnight_mode": 0,
}


class PrivacyTests(unittest.TestCase):
    def test_example_contains_only_the_documented_demo_settings(self):
        settings = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(settings, DEMO_SETTINGS)

    def test_example_loads_as_the_documented_demo(self):
        spec = importlib.util.spec_from_file_location(
            "privacy_test_plugin", ROOT / "plugins/prayer-times.1m.py"
        )
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        self.assertEqual(plugin.load_settings(EXAMPLE), DEMO_SETTINGS)


if __name__ == "__main__":
    unittest.main()
