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
    run_restore_all,
    run_nav,
    run_upload_picker,
    upgrade_app,
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
        self.assertIn(
            "# gdrive <preset> register <local_dir> as <drive_path> | gdrive <preset> list registrations | gdrive <preset> remove registration <id>",
            output,
        )
        self.assertIn("gdrive 1 register ~/Documents as Documents", output)
        self.assertIn("gdrive 1 browse", output)
        self.assertIn("gdrive 1 upload ~/Downloads/report.pdf ~/Pictures", output)
        self.assertIn("gdrive sync restore", output)
        self.assertIn("gdrive sync run", output)
        self.assertIn("install, disable, or inspect the hourly systemd timer", output)
        self.assertIn("# gdrive timer install | gdrive timer disable | gdrive timer status", output)
        self.assertIn("gdrive timer install", output)
        self.assertIn("gdrive timer disable", output)
        self.assertIn("gdrive timer status", output)
        self.assertIn("gdrive config", output)
        self.assertNotIn("commands:", output)
        self.assertNotIn("usage:", output)

    def test_no_args_prints_same_help_as_dash_h(self):
        with patch("sys.stdout", new=StringIO()) as help_stdout:
            help_code = main(["-h"])
        with patch("sys.stdout", new=StringIO()) as no_args_stdout:
            no_args_code = main([])
        self.assertEqual(help_code, 0)
        self.assertEqual(no_args_code, 0)
        self.assertEqual(no_args_stdout.getvalue(), help_stdout.getvalue())

    def test_version_prints_runtime_version_only(self):
        with patch("sys.stdout", new=StringIO()) as stdout:
            code = main(["-v"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), "0.0.0")

    def test_upgrade_downloads_installer_and_runs_upgrade_mode(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"#!/usr/bin/env bash\n"

        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen, patch(
            "subprocess.run"
        ) as subprocess_run:
            subprocess_run.return_value.returncode = 0
            code = upgrade_app()
        self.assertEqual(code, 0)
        urlopen.assert_called_once()
        self.assertEqual(subprocess_run.call_args.args[0][:2], ["/usr/bin/env", "bash"])
        self.assertEqual(subprocess_run.call_args.args[0][-1], "-u")

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

    def test_browse_dispatches_to_run_nav(self):
        with patch("gdrive_cli.cli.run_nav", return_value=0) as run_nav:
            code = main(["1", "browse"])
        self.assertEqual(code, 0)
        run_nav.assert_called_once_with("1")

    def test_upload_dispatches_to_run_upload_picker(self):
        with patch("gdrive_cli.cli.run_upload_picker", return_value=0) as run_upload_picker:
            code = main(["1", "upload", "/tmp/a", "/tmp/b"])
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

    def test_config_opens_config_in_visual_then_editor_then_vim(self):
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
                    code = main(["config"])
            self.assertEqual(code, 0)
            subprocess_run.assert_called_once()
            self.assertEqual(subprocess_run.call_args.args[0][0], "nvim")

    def test_build_runtime_command_uses_launcher_only_when_frozen(self):
        with patch("sys.executable", "/tmp/gdrive"), patch("sys.frozen", True, create=True):
            self.assertEqual(_build_runtime_command("sync", "run"), "/tmp/gdrive sync run")

    def test_write_timer_units_uses_launcher_only_when_frozen(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("gdrive_cli.cli.ensure_dirs"), patch(
                "gdrive_cli.cli.Path.home", return_value=home
            ), patch("sys.executable", "/tmp/gdrive"), patch("sys.frozen", True, create=True):
                write_timer_units()
            service_path = home / ".config" / "systemd" / "user" / "gdrive.service"
            timer_path = home / ".config" / "systemd" / "user" / "gdrive.timer"
            service_body = service_path.read_text(encoding="utf-8")
            timer_body = timer_path.read_text(encoding="utf-8")
            self.assertIn("ExecStart=/usr/bin/env bash -lc", service_body)
            self.assertIn("if /tmp/gdrive sync run; then", service_body)
            self.assertNotIn("main.py sync run", service_body)
            self.assertIn("OnActiveSec=5m", timer_body)
            self.assertIn("quickshell ipc -p \"$qs\" call bar notify", service_body)
            self.assertIn("notify-send", service_body)
            self.assertIn("Hourly backup started", service_body)
            self.assertIn("Hourly backup finished successfully", service_body)
            self.assertIn("Hourly backup failed", service_body)

    def test_install_timer_restarts_existing_timer(self):
        with patch("gdrive_cli.cli.write_timer_units") as write_units, patch(
            "gdrive_cli.cli.systemctl_user"
        ) as systemctl_user:
            code = main(["timer", "install"])
        self.assertEqual(code, 0)
        write_units.assert_called_once()
        systemctl_user.assert_any_call("daemon-reload")
        systemctl_user.assert_any_call("enable", "gdrive.timer")
        systemctl_user.assert_any_call("restart", "gdrive.timer")

    def test_run_restore_all_restores_each_registered_folder(self):
        account = SimpleNamespace(
            registrations=[
                SimpleNamespace(id="1", enabled=True),
                SimpleNamespace(id="2", enabled=True),
            ]
        )
        config = SimpleNamespace(accounts={"1": account})
        summaries = [
            SimpleNamespace(downloaded=2, dirs_created=1, skipped_existing=0, state_entries=3),
            SimpleNamespace(downloaded=1, dirs_created=0, skipped_existing=1, state_entries=1),
        ]
        with patch("gdrive_cli.cli.load_config", return_value=config), patch(
            "gdrive_cli.cli.get_account", return_value=account
        ), patch("gdrive_cli.cli.require_client_secret"), patch(
            "gdrive_cli.cli.require_backup_root_name", return_value="Backups"
        ), patch("gdrive_cli.cli.drive_client", return_value=object()), patch(
            "gdrive_cli.cli.restore_registration_from_remote", side_effect=summaries
        ) as restore, patch("gdrive_cli.cli.update_registration") as update_registration, patch(
            "sys.stdout", new=StringIO()
        ) as stdout:
            code = run_restore_all()
        self.assertEqual(code, 0)
        self.assertEqual(restore.call_count, 2)
        self.assertEqual(update_registration.call_count, 2)
        self.assertIn("downloaded=2", stdout.getvalue())

    def test_preset_restore_dispatches_single_registration(self):
        with patch("gdrive_cli.cli._restore_account_registrations", return_value=True) as restore:
            code = main(["1", "restore", "registration", "2"])
        self.assertEqual(code, 0)
        restore.assert_called_once_with("1", "2")
