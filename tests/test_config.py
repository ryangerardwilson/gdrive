import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gdrive_cli.config import (
    add_registration,
    get_account,
    load_config,
    normalize_drive_path,
    normalize_relative_drive_path,
    set_backup_root_name,
    set_client_secret,
)
from gdrive_cli.errors import CliError


class ConfigTests(unittest.TestCase):
    def test_normalize_drive_path(self):
        self.assertEqual(normalize_drive_path(" Backups / Docs "), "Backups/Docs")

    def test_set_client_secret_missing(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.dict(
                "os.environ",
                {
                    "XDG_CONFIG_HOME": str(tmp_path / "config"),
                    "XDG_DATA_HOME": str(tmp_path / "data"),
                },
                clear=False,
            ):
                with self.assertRaises(CliError):
                    set_client_secret("1", "/tmp/does-not-exist.json")

    def test_add_registration_under_preset(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.dict(
                "os.environ",
                {
                    "XDG_CONFIG_HOME": str(tmp_path / "config"),
                    "XDG_DATA_HOME": str(tmp_path / "data"),
                },
                clear=False,
            ):
                set_backup_root_name("2", "Backups")
                local_dir = tmp_path / "docs"
                local_dir.mkdir()
                reg = add_registration("2", str(local_dir), "Docs")
                config = load_config()
                account = get_account(config, "2")
                self.assertEqual(reg.id, "1")
                self.assertEqual(Path(account.registrations[0].local_dir), local_dir.resolve())

    def test_set_backup_root_name(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.dict(
                "os.environ",
                {
                    "XDG_CONFIG_HOME": str(tmp_path / "config"),
                    "XDG_DATA_HOME": str(tmp_path / "data"),
                },
                clear=False,
            ):
                value = set_backup_root_name("3", " Backups / Laptop ")
                config = load_config()
                account = get_account(config, "3")
                self.assertEqual(value, "Backups/Laptop")
                self.assertEqual(account.backup_root_name, "Backups/Laptop")

    def test_reject_drive_path_with_root_prefix(self):
        with self.assertRaises(CliError):
            normalize_relative_drive_path("Backups/Documents", "Backups")

    def test_load_config_migrates_legacy_root_to_preset_one(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "client_secret_file": "~/client.json",
                        "backup_root_name": "Backups",
                        "registrations": [
                            {
                                "id": "1",
                                "local_dir": str(tmp_path),
                                "drive_path": "Docs",
                                "remote_root_id": "abc",
                                "enabled": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            account = get_account(config, "1")
            self.assertEqual(account.backup_root_name, "Backups")
            self.assertEqual(str(account.client_secret_file), str(Path("~/client.json").expanduser()))
            self.assertEqual(len(account.registrations), 1)
