import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gdrive_cli.cli import compact_usage, ensure_backup_root_name, ensure_client_secret


class CliUsageTests(unittest.TestCase):
    def test_compact_usage_contains_core_commands(self):
        usage = compact_usage()
        self.assertIn("gdrive reg <local_dir> <drive_path>", usage)
        self.assertIn("gdrive run [id]", usage)
        self.assertNotIn("gdrive a <client_secret_file>", usage)

    def test_ensure_backup_root_name_prompts_and_saves(self):
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
                        self.assertEqual(ensure_backup_root_name(interactive=True), "Backups")

    def test_ensure_client_secret_prompts_and_saves(self):
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
                        self.assertEqual(str(ensure_client_secret(interactive=True)), secret_path)

    def test_compact_usage_has_ls(self):
        usage = compact_usage()
        self.assertIn("gdrive ls", usage)
