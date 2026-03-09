import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gdrive_cli.cli import (
    _build_runtime_command,
    ensure_backup_root_name,
    ensure_client_secret,
    main,
    write_timer_units,
)


class CliUsageTests(unittest.TestCase):
    def test_help_is_human_friendly(self):
        with patch("sys.stdout", new=StringIO()) as stdout:
            code = main(["-h"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("Google Drive backup CLI", output)
        self.assertIn("register a local folder to sync into a Drive path", output)
        self.assertIn("gdrive 1 reg ~/Documents Documents", output)
        self.assertNotIn("usage:", output)

    def test_ensure_backup_root_name_prompts_and_saves_for_preset(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "XDG_CONFIG_HOME": f"{tmp}/config",
                    "XDG_DATA_HOME": f"{tmp}/data",
                },
                clear=False,
            ):
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", return_value="Backups"):
                        self.assertEqual(ensure_backup_root_name("2", interactive=True), "Backups")

    def test_ensure_client_secret_prompts_and_saves_for_preset(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "XDG_CONFIG_HOME": f"{tmp}/config",
                    "XDG_DATA_HOME": f"{tmp}/data",
                },
                clear=False,
            ):
                secret_path = f"{tmp}/client.json"
                with open(secret_path, "w", encoding="utf-8") as handle:
                    handle.write("{}")
                with patch("sys.stdin.isatty", return_value=True):
                    with patch("builtins.input", return_value=secret_path):
                        self.assertEqual(str(ensure_client_secret("2", interactive=True)), secret_path)

    def test_build_runtime_command_uses_launcher_only_when_frozen(self):
        with patch("sys.executable", "/tmp/gdrive"), patch("sys.frozen", True, create=True):
            self.assertEqual(_build_runtime_command("run"), "/tmp/gdrive run")

    def test_write_timer_units_uses_launcher_only_when_frozen(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("gdrive_cli.cli.ensure_dirs"), patch(
                "gdrive_cli.cli.Path.home", return_value=home
            ), patch("sys.executable", "/tmp/gdrive"), patch("sys.frozen", True, create=True):
                write_timer_units()
            service_path = home / ".config" / "systemd" / "user" / "gdrive.service"
            service_body = service_path.read_text(encoding="utf-8")
            self.assertIn("ExecStart=/usr/bin/env bash -lc '/tmp/gdrive run &&", service_body)
            self.assertNotIn("main.py run", service_body)
