from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gdrive_cli.config import (
    add_registration,
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
                    set_client_secret("/tmp/does-not-exist.json")

    def test_add_registration(self):
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
                set_backup_root_name("Backups")
                local_dir = tmp_path / "docs"
                local_dir.mkdir()
                reg = add_registration(str(local_dir), "Docs")
                config = load_config()
                self.assertEqual(reg.id, "1")
                self.assertEqual(Path(config.registrations[0].local_dir), local_dir.resolve())

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
                value = set_backup_root_name(" Backups / Laptop ")
                config = load_config()
                self.assertEqual(value, "Backups/Laptop")
                self.assertEqual(config.backup_root_name, "Backups/Laptop")

    def test_reject_drive_path_with_root_prefix(self):
        with self.assertRaises(CliError):
            normalize_relative_drive_path("Backups/Documents", "Backups")
