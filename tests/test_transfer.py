import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from gdrive_cli.errors import CliError
from gdrive_cli.transfer import download_directory_as_folder, download_directory_as_zip, normalize_upload_paths, upload_local_paths


class FakeDriveClient:
    def __init__(self):
        self.created_folders: list[tuple[str, str, str]] = []
        self.uploaded_files: list[tuple[str, str, str]] = []
        self._next_folder = 0

    def find_available_name(self, parent_id: str, name: str) -> str:
        return name

    def create_folder(self, parent_id: str, name: str) -> str:
        self._next_folder += 1
        folder_id = f"folder-{self._next_folder}"
        self.created_folders.append((parent_id, name, folder_id))
        return folder_id

    def upload_file(self, parent_id: str, name: str, file_path: str) -> str:
        self.uploaded_files.append((parent_id, name, file_path))
        return f"file-{len(self.uploaded_files)}"

    def list_tree(self, _root_id: str):
        return {
            "alpha.txt": SimpleNamespace(
                id="1",
                relpath="alpha.txt",
                name="alpha.txt",
                parent_id="root",
                mime_type="text/plain",
                is_dir=False,
            ),
            "docs": SimpleNamespace(
                id="2",
                relpath="docs",
                name="docs",
                parent_id="root",
                mime_type="application/vnd.google-apps.folder",
                is_dir=True,
            ),
            "docs/beta.txt": SimpleNamespace(
                id="3",
                relpath="docs/beta.txt",
                name="beta.txt",
                parent_id="2",
                mime_type="text/plain",
                is_dir=False,
            ),
        }

    def download_entry(self, entry, target_path: Path) -> Path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(f"downloaded:{entry.name}", encoding="utf-8")
        return target_path


class TransferTests(unittest.TestCase):
    def test_normalize_upload_paths_rejects_missing_paths(self):
        with self.assertRaises(CliError):
            normalize_upload_paths(["/tmp/definitely-missing-gdrive-upload-path"])

    def test_upload_local_paths_preserves_directory_structure(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root_dir = tmp_path / "Photos"
            nested_dir = root_dir / "Trips"
            nested_dir.mkdir(parents=True)
            (root_dir / "cover.jpg").write_text("cover", encoding="utf-8")
            (nested_dir / "day1.jpg").write_text("day1", encoding="utf-8")
            loose_file = tmp_path / "notes.txt"
            loose_file.write_text("notes", encoding="utf-8")

            client = FakeDriveClient()
            summary = upload_local_paths(client, "root", [root_dir, loose_file])

        self.assertEqual(summary.dirs_created, 2)
        self.assertEqual(summary.files_uploaded, 3)
        self.assertEqual(client.created_folders[0][:2], ("root", "Photos"))
        self.assertEqual(client.created_folders[1][:2], ("folder-1", "Trips"))
        uploaded_parents = sorted(parent_id for parent_id, _, _ in client.uploaded_files)
        self.assertEqual(uploaded_parents, ["folder-1", "folder-2", "root"])

    def test_download_directory_as_zip_preserves_tree(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client = FakeDriveClient()
            archive_path = download_directory_as_zip(
                client,
                SimpleNamespace(id="root", name="Exports"),
                tmp_path / "Exports.zip",
            )
            with zipfile.ZipFile(archive_path) as archive:
                names = sorted(archive.namelist())
                self.assertIn("Exports/alpha.txt", names)
                self.assertIn("Exports/docs/beta.txt", names)
                self.assertEqual(archive.read("Exports/alpha.txt").decode("utf-8"), "downloaded:alpha.txt")

    def test_download_directory_as_folder_extracts_tree_and_leaves_no_zip(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client = FakeDriveClient()
            folder_path = download_directory_as_folder(
                client,
                SimpleNamespace(id="root", name="Exports"),
                tmp_path / "Exports",
            )

            self.assertEqual(folder_path, tmp_path / "Exports")
            self.assertTrue(folder_path.is_dir())
            self.assertFalse((tmp_path / "Exports.zip").exists())
            self.assertEqual((folder_path / "alpha.txt").read_text(encoding="utf-8"), "downloaded:alpha.txt")
            self.assertEqual((folder_path / "docs" / "beta.txt").read_text(encoding="utf-8"), "downloaded:beta.txt")
