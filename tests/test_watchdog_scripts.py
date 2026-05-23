import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "smart-proxy-watchdog.ps1"
INSTALLER = ROOT / "install-smart-proxy-watchdog.ps1"


def parse_powershell(path):
    script = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{path}', [ref]$tokens, [ref]$errors) > $null; "
        "if ($errors) { "
        "$errors | ForEach-Object { "
        "\"$($_.Extent.StartLineNumber):$($_.Extent.StartColumnNumber) $($_.Message)\" "
        "}; exit 1 }"
    )
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class WatchdogScriptTests(unittest.TestCase):
    def test_watchdog_script_parses_and_has_safe_process_filter(self):
        self.assertTrue(WATCHDOG.exists(), "watchdog script should exist")

        result = parse_powershell(WATCHDOG)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = WATCHDOG.read_text(encoding="utf-8")
        self.assertIn("Test-SmartProxyHealth", text)
        self.assertIn("Restart-SmartProxy", text)
        self.assertIn("smart-proxy-watchdog.log", text)
        self.assertIn("/api/runtime-status", text)
        self.assertIn("Get-CimInstance Win32_Process", text)
        self.assertIn("$_.Name -eq 'python.exe'", text)
        self.assertIn("$_.Name -eq 'pythonw.exe'", text)

    def test_installer_script_parses_and_registers_logon_task(self):
        self.assertTrue(INSTALLER.exists(), "installer script should exist")

        result = parse_powershell(INSTALLER)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("Register-ScheduledTask", text)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", text)
        self.assertIn("smart-proxy-watchdog.ps1", text)
        self.assertIn("Start-ScheduledTask", text)
        self.assertIn("Unregister-ScheduledTask", text)
        self.assertIn("Install-StartupFallback", text)
        self.assertIn("[Environment+SpecialFolder]::Startup", text)
        self.assertIn("Start-WatchdogNow", text)
        self.assertIn("Get-RunningWatchdogProcesses", text)
        self.assertIn("Resolve-Path -LiteralPath $WatchdogScript", text)
        self.assertIn("-notlike '*-Status*'", text)
        self.assertIn("-notlike '*-Once*'", text)
        self.assertIn("-notlike '*-Command*'", text)


if __name__ == "__main__":
    unittest.main()
