from pathlib import Path
import unittest


class OpenWebUiStartupTests(unittest.TestCase):
    def test_launcher_uses_portable_python_311_and_local_state(self) -> None:
        script = Path(__file__).with_name("start.ps1").read_text(encoding="utf-8")
        self.assertIn("cpython-3.11", script)
        self.assertIn("pip install --python $Python --link-mode copy open-webui", script)
        self.assertIn("$env:DATA_DIR = $Data", script)
        self.assertIn("$env:USERPROFILE = $PortableProfile", script)
        self.assertIn("$env:HF_HOME", script)
        self.assertIn("$env:PYTHONNOUSERSITE = '1'", script)

    def test_launcher_serves_only_on_loopback_and_checks_health(self) -> None:
        script = Path(__file__).with_name("start.ps1").read_text(encoding="utf-8")
        self.assertIn("@('serve','--host','127.0.0.1','--port',$Port)", script)
        self.assertIn('Invoke-WebRequest -UseBasicParsing -Uri "$Url/health"', script)
        self.assertIn("-WorkingDirectory $Data", script)
        self.assertNotIn("Start-Process $Url", script)


if __name__ == "__main__":
    unittest.main()
