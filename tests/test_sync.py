import unittest
from tempfile import TemporaryDirectory
from unittest import mock

from gdrive_cli.drive_api import RemoteEntry
from gdrive_cli.sync import (
    LocalEntry,
    StateEntry,
    build_rename_map,
    load_state,
    parent_relpath,
    restore_registration_from_remote,
    scan_local_tree,
    sync_registration,
)


class FakeDrive:
    def __init__(self):
        self.deleted = []
        self.uploaded = []
        self.updated = []

    def ensure_drive_path(self, drive_path):
        self.drive_path = drive_path
        return "root-id"

    def list_tree(self, root_id):
        self.root_id = root_id
        return {
            "Album": RemoteEntry("dir-1", "Album", "Album", "root-id", "application/vnd.google-apps.folder"),
            "Album/song.mp3": RemoteEntry("file-1", "Album/song.mp3", "song.mp3", "dir-1", "audio/mpeg"),
            "book.pdf": RemoteEntry("file-2", "book.pdf", "book.pdf", "root-id", "application/pdf"),
        }

    def download_entry(self, entry, target_path):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(f"downloaded {entry.name}", encoding="utf-8")
        return target_path

    def create_folder(self, parent_id, name):
        self.uploaded.append(("dir", parent_id, name))
        return f"created-{name}"

    def upload_file(self, parent_id, name, file_path):
        self.uploaded.append(("file", parent_id, name, file_path))
        return f"uploaded-{name}"

    def update_file(self, drive_id, file_path):
        self.updated.append((drive_id, file_path))

    def delete_entry(self, drive_id):
        self.deleted.append(drive_id)

    def move_entry(self, drive_id, new_parent_id, new_name, old_parent_id):
        self.updated.append(("move", drive_id, new_parent_id, new_name, old_parent_id))

    def rename_entry(self, drive_id, new_name):
        self.updated.append(("rename", drive_id, new_name))


class SyncTests(unittest.TestCase):
    def test_parent_relpath(self):
        self.assertEqual(parent_relpath("a/b/c.txt"), "a/b")
        self.assertEqual(parent_relpath("file.txt"), "")

    def test_build_rename_map(self):
        old = {
            "old.txt": StateEntry("old.txt", "file", "id-1", "", 5, 10, "abc"),
        }
        new = {
            "new.txt": LocalEntry("new.txt", "file", 5, 12, "abc"),
        }
        self.assertEqual(build_rename_map(old, new), {"old.txt": "new.txt"})

    def test_scan_local_tree_reuses_hash(self):
        with TemporaryDirectory() as tmp:
            from pathlib import Path

            root = Path(tmp) / "root"
            root.mkdir()
            file_path = root / "a.txt"
            file_path.write_text("hello")
            first = scan_local_tree(root, {})
            second = scan_local_tree(
                root,
                {
                    "a.txt": StateEntry(
                        "a.txt",
                        "file",
                        "id-1",
                        "",
                        first["a.txt"].size,
                        first["a.txt"].mtime_ns,
                        first["a.txt"].sha1,
                    )
                },
            )
            self.assertEqual(second["a.txt"].sha1, first["a.txt"].sha1)

    def test_restore_registration_downloads_remote_and_seeds_state_without_deleting(self):
        with TemporaryDirectory() as tmp:
            from pathlib import Path

            root = Path(tmp) / "Music"
            registration = type(
                "Registration",
                (),
                {
                    "id": "1",
                    "local_dir": str(root),
                    "drive_path": "Music",
                    "remote_root_id": None,
                    "enabled": True,
                },
            )()
            drive = FakeDrive()

            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": str(Path(tmp) / "data")}, clear=False):
                summary = restore_registration_from_remote("1", registration, drive, "Backups")
                state = load_state("1", "1")

            self.assertEqual(summary.downloaded, 2)
            self.assertEqual(summary.dirs_created, 1)
            self.assertEqual(summary.skipped_existing, 0)
            self.assertEqual(drive.deleted, [])
            self.assertEqual(drive.uploaded, [])
            self.assertEqual(drive.updated, [])
            self.assertEqual(registration.remote_root_id, "root-id")
            self.assertTrue((root / "Album" / "song.mp3").exists())
            self.assertTrue((root / "book.pdf").exists())
            self.assertIn("Album", state)
            self.assertIn("Album/song.mp3", state)
            self.assertIn("book.pdf", state)

    def test_restore_registration_skips_existing_files_without_state_for_that_file(self):
        with TemporaryDirectory() as tmp:
            from pathlib import Path

            root = Path(tmp) / "Music"
            (root / "Album").mkdir(parents=True)
            (root / "Album" / "song.mp3").write_text("local", encoding="utf-8")
            registration = type(
                "Registration",
                (),
                {
                    "id": "2",
                    "local_dir": str(root),
                    "drive_path": "Music",
                    "remote_root_id": "root-id",
                    "enabled": True,
                },
            )()

            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": str(Path(tmp) / "data")}, clear=False):
                summary = restore_registration_from_remote("1", registration, FakeDrive(), "Backups")
                state = load_state("1", "2")

            self.assertEqual(summary.downloaded, 1)
            self.assertEqual(summary.skipped_existing, 1)
            self.assertNotIn("Album/song.mp3", state)
            self.assertIn("book.pdf", state)

    def test_restore_state_prevents_next_sync_from_deleting_remote_files(self):
        with TemporaryDirectory() as tmp:
            from pathlib import Path

            root = Path(tmp) / "Music"
            registration = type(
                "Registration",
                (),
                {
                    "id": "3",
                    "local_dir": str(root),
                    "drive_path": "Music",
                    "remote_root_id": "root-id",
                    "enabled": True,
                },
            )()
            drive = FakeDrive()

            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": str(Path(tmp) / "data")}, clear=False):
                restore_registration_from_remote("1", registration, drive, "Backups")
                summary = sync_registration("1", registration, drive, "Backups")

            self.assertEqual(summary.deleted, 0)
            self.assertEqual(summary.created, 0)
            self.assertEqual(summary.updated, 0)
            self.assertEqual(drive.deleted, [])
            self.assertEqual(drive.uploaded, [])
            self.assertEqual(drive.updated, [])
