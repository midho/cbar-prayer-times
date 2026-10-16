"""Check setup instructions and privacy-safe screenshot exports."""
import json
import os
import re
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ("panel-preview.png", "timetable.png")


class DocumentationTests(unittest.TestCase):
    def test_readme_screenshots_exist_and_have_no_text_metadata(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in SCREENSHOTS:
            with self.subTest(name=name):
                relative = f"docs/screenshots/{name}"
                self.assertIn(f"]({relative})", readme)
                data = (ROOT / relative).read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                width, height = struct.unpack(">II", data[16:24])
                self.assertTrue(20 <= width <= 640)
                self.assertTrue(20 <= height <= 850)
                offset = 8
                chunks = []
                while offset < len(data):
                    length = struct.unpack(">I", data[offset:offset + 4])[0]
                    kind = data[offset + 4:offset + 8]
                    chunks.append(kind)
                    offset += length + 12
                    self.assertLessEqual(offset, len(data))
                self.assertEqual(offset, len(data))
                self.assertEqual(chunks[-1], b"IEND")
                for forbidden in (b"tEXt", b"zTXt", b"iTXt", b"eXIf"):
                    self.assertNotIn(forbidden, chunks)

    def test_setup_copy_block_works_and_keeps_existing_settings(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(r"```sh\n(config_dir=.*?)\n```", readme, re.S)
        self.assertIsNotNone(match)
        snippet = match.group(1)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            home = base / "home"
            home.mkdir()
            config_home = base / "custom settings"
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(config_home))
            target = config_home / "cbar-prayer-times/config.json"
            for existing in (False, True):
                if existing:
                    target.write_text('{"keep": "existing settings"}', encoding="utf-8")
                result = subprocess.run(
                    ["bash", "-eu", "-c", snippet], cwd=ROOT, env=env,
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected = {"keep": "existing settings"} if existing else json.loads(
                    (ROOT / "examples/config.json").read_text(encoding="utf-8")
                )
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
