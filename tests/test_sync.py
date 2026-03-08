import unittest
from tempfile import TemporaryDirectory

from gdrive_cli.sync import LocalEntry, StateEntry, build_rename_map, parent_relpath, scan_local_tree


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
