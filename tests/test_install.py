"""Temporary-directory integration tests for install and removal."""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
INSTALL=ROOT/"scripts/install.sh"; UNINSTALL=ROOT/"scripts/uninstall.sh"

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name); self.home=self.base/"home with spaces"; self.home.mkdir()
        self.fake=self.base/"fake cbar"; self.fake.write_text("#!/bin/sh\nexit 0\n"); self.fake.chmod(0o755)
    def env(self, **extra):
        env=dict(os.environ,HOME=str(self.home),PATH=os.environ.get("PATH",""),CBAR_BIN=str(self.fake))
        for key in ("XDG_DATA_HOME","XDG_CONFIG_HOME","XDG_CACHE_HOME"): env.pop(key,None)
        env.update(extra); return env
    def run_script(self, script, env, check=True, cwd=None):
        result=subprocess.run(["bash",str(script)],env=env,text=True,capture_output=True,cwd=cwd)
        if check and result.returncode: self.fail(f"{script.name}: {result.stderr}\n{result.stdout}")
        return result
    def test_install_reinstall_uninstall_preserves_data(self):
        env=self.env(); data=self.home/".local/share"
        cfg=self.home/".config/cbar-prayer-times/config.json"; cfg.parent.mkdir(parents=True); cfg.write_text("settings")
        calendar=self.home/".cache/cbar-prayer-times/year.json"; calendar.parent.mkdir(parents=True); calendar.write_text("calendar")
        unrelated=data/"cbar-prayer-times/keep"; unrelated.parent.mkdir(parents=True); unrelated.write_text("unrelated")
        self.assertIn("Existing settings kept",self.run_script(INSTALL,env).stdout)
        desktop=data/"applications/cbar-prayer-times.desktop"; plugin=data/"cbar-prayer-times/plugins/prayer-times.1m.py"
        self.assertTrue(desktop.exists() and plugin.exists()); self.run_script(INSTALL,env)
        self.assertEqual(cfg.read_text(),"settings"); self.assertEqual(unrelated.read_text(),"unrelated")
        self.run_script(UNINSTALL,env); self.assertFalse(desktop.exists() or plugin.exists())
        self.assertEqual(cfg.read_text(),"settings"); self.assertEqual(calendar.read_text(),"calendar")
        self.assertEqual(unrelated.read_text(),"unrelated"); self.assertTrue(self.fake.exists())
    def test_custom_and_empty_xdg_values(self):
        data=self.base/"custom data"; cfg=self.base/"custom config"
        env=self.env(XDG_DATA_HOME=str(data),XDG_CONFIG_HOME=str(cfg),XDG_CACHE_HOME="")
        self.run_script(INSTALL,env); self.assertTrue((data/"applications/cbar-prayer-times.desktop").exists())
        self.run_script(UNINSTALL,env)
        output=self.run_script(INSTALL,self.env(XDG_DATA_HOME="",XDG_CONFIG_HOME="",XDG_CACHE_HOME="")).stdout
        self.assertIn(str(self.home/".config/cbar-prayer-times/config.json"),output)
    def test_missing_cbar_fails_before_any_writes(self):
        result=self.run_script(INSTALL,self.env(CBAR_BIN=str(self.base/"missing")),check=False)
        self.assertNotEqual(result.returncode,0); self.assertIn("CBar is not an executable file",result.stderr)
        self.assertFalse((self.home/".local/share").exists())
    def test_desktop_entry_quoting_and_validation(self):
        home=self.base/"space % apostrophe ' home"; home.mkdir()
        self.run_script(INSTALL,self.env(HOME=str(home)))
        desktop=home/".local/share/applications/cbar-prayer-times.desktop"; contents=desktop.read_text()
        self.assertIn("Name=Prayer Time",contents); self.assertIn("%%",contents); self.assertIn("CBAR_PLUGIN_DIR=",contents)
        validator=shutil.which("desktop-file-validate")
        if validator:
            check=subprocess.run([validator,str(desktop)],text=True,capture_output=True)
            self.assertEqual(check.returncode,0,check.stdout+check.stderr)
    def test_relative_cbar_bin_is_saved_as_absolute_path(self):
        relative=self.env(CBAR_BIN="./fake cbar")
        self.run_script(INSTALL,relative,cwd=self.base)
        desktop=self.home/".local/share/applications/cbar-prayer-times.desktop"
        self.assertIn(str(self.fake),desktop.read_text())
        self.assertTrue(desktop.is_file())

if __name__=="__main__": unittest.main()
