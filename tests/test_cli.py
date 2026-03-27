import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from gdrive_cli.cli import (
    _build_runtime_command,
    ensure_backup_root_name,
    ensure_client_secret,
    main,
    run_nav,
    run_upload_picker,
    write_timer_units,
)


class CliUsageTests(unittest.TestCase):
    def test_help_is_human_friendly(self):
        with patch("sys.stdout", new=StringIO()) as stdout:
            code = main(["-h"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("gdrive", output)
        self.assertIn("features:", output)
        self.assertIn("authorize a Google account and save or refresh its preset", output)
        self.assertIn("register folders to sync into Drive, then inspect or remove registrations", output)
        self.assertIn("# gdrive auth <client_secret_path>", output)
        self.assertIn("# gdrive <preset> reg <local_dir> <drive_path> | gdrive <preset> ls | gdrive <preset> rm <edit_id>", output)
        self.assertIn("gdrive 1 reg ~/Documents Documents", output)
        self.assertIn("gdrive 1 nav", output)
        self.assertIn("gdrive 1 up ~/Downloads/report.pdf ~/Pictures", output)
        self.assertIn("gdrive run", output)
        self.assertIn("install, disable, or inspect the hourly systemd timer", output)
        self.assertIn("# gdrive ti | gdrive td | gdrive st", output)
        self.assertIn("gdrive ti", output)
        self.assertIn("gdrive td", output)
        self.assertIn("gdrive st", output)
        self.assertIn("gdrive conf", output)
        self.assertNotIn("commands:", output)
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

    def test_nav_dispatches_to_run_nav(self):
        with patch("gdrive_cli.cli.run_nav", return_value=0) as run_nav:
            code = main(["1", "nav"])
        self.assertEqual(code, 0)
        run_nav.assert_called_once_with("1")

    def test_up_dispatches_to_run_upload_picker(self):
        with patch("gdrive_cli.cli.run_upload_picker", return_value=0) as run_upload_picker:
            code = main(["1", "up", "/tmp/a", "/tmp/b"])
        self.assertEqual(code, 0)
        run_upload_picker.assert_called_once_with("1", ["/tmp/a", "/tmp/b"])

    def test_run_nav_uses_current_working_directory_for_downloads(self):
        with TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            browse_drive = MagicMock(return_value=0)
            with patch("gdrive_cli.cli.ensure_client_secret"), patch(
                "gdrive_cli.cli.load_config", return_value=SimpleNamespace(handlers={"pdf_viewer": object()})
            ), patch(
                "gdrive_cli.cli.drive_client", return_value=object()
            ), patch("gdrive_cli.cli.Path.cwd", return_value=cwd), patch.dict(
                "sys.modules", {"gdrive_cli.nav": SimpleNamespace(browse_drive=browse_drive)}
            ):
                code = run_nav("1")
        self.assertEqual(code, 0)
        browse_drive.assert_called_once_with(
            client=unittest.mock.ANY,
            preset="1",
            download_dir=cwd,
            handlers={"pdf_viewer": unittest.mock.ANY},
        )

    def test_run_upload_picker_prints_summary(self):
        with TemporaryDirectory() as tmp:
            upload_file = Path(tmp) / "report.pdf"
            upload_file.write_text("x", encoding="utf-8")
            with patch("gdrive_cli.cli.ensure_client_secret"), patch(
                "gdrive_cli.cli.load_config", return_value=SimpleNamespace(handlers={})
            ), patch("gdrive_cli.cli.drive_client", return_value=object()), patch(
                "gdrive_cli.cli.Path.cwd", return_value=Path(tmp)
            ), patch.dict(
                "sys.modules",
                {
                    "gdrive_cli.nav": SimpleNamespace(
                        upload_with_picker=MagicMock(
                            return_value=SimpleNamespace(
                                upload_summary=SimpleNamespace(files_uploaded=1, dirs_created=0),
                                upload_target_path="/Uploads",
                            )
                        )
                    )
                },
            ), patch("sys.stdout", new=StringIO()) as stdout:
                code = run_upload_picker("1", [str(upload_file)])
        self.assertEqual(code, 0)
        self.assertIn("uploaded\tfiles=1\tdirs=0\ttarget=/Uploads", stdout.getvalue())

    def test_conf_opens_config_in_visual_then_editor_then_vim(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "XDG_CONFIG_HOME": f"{tmp}/config",
                    "XDG_DATA_HOME": f"{tmp}/data",
                    "VISUAL": "nvim",
                },
                clear=False,
            ):
                with patch("subprocess.run") as subprocess_run:
                    subprocess_run.return_value.returncode = 0
                    code = main(["conf"])
            self.assertEqual(code, 0)
            subprocess_run.assert_called_once()
            self.assertEqual(subprocess_run.call_args.args[0][0], "nvim")

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
