import unittest
from pathlib import Path

from gdrive_cli.config import HandlerSpec
from gdrive_cli.file_handlers import resolve_download_name, select_handler_spec


class FileHandlerTests(unittest.TestCase):
    def test_resolve_download_name_uses_export_suffix_for_google_sheet(self):
        self.assertEqual(
            resolve_download_name("Budget", "application/vnd.google-apps.spreadsheet"),
            "Budget.xlsx",
        )

    def test_select_handler_spec_prefers_audio_player(self):
        spec, strategy, is_text_like = select_handler_spec(
            {"audio_player": HandlerSpec(commands=[["mpv"]], is_internal=False)},
            Path("/tmp/song.mp3"),
        )
        self.assertEqual(spec.commands, [["mpv"]])
        self.assertEqual(strategy, "external_background")
        self.assertFalse(is_text_like)

    def test_select_handler_spec_uses_terminal_strategy_for_csv(self):
        spec, strategy, is_text_like = select_handler_spec(
            {"csv_viewer": HandlerSpec(commands=[["vixl"]], is_internal=False)},
            Path("/tmp/data.csv"),
        )
        self.assertEqual(spec.commands, [["vixl"]])
        self.assertEqual(strategy, "terminal")
        self.assertFalse(is_text_like)

    def test_select_handler_spec_marks_text_files_for_editor_fallback(self):
        spec, strategy, is_text_like = select_handler_spec({}, Path("/tmp/readme.md"))
        self.assertEqual(spec.commands, [])
        self.assertEqual(strategy, "external_foreground")
        self.assertTrue(is_text_like)
