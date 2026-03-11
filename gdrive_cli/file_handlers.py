from __future__ import annotations

import mimetypes
from pathlib import Path

from .config import HandlerSpec
from .drive_types import resolve_exported_name

AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
TEXT_LIKE_EXTENSIONS = {
    ".c",
    ".cfg",
    ".cpp",
    ".h",
    ".hpp",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def empty_handler_spec() -> HandlerSpec:
    return HandlerSpec(commands=[], is_internal=False)


def select_handler_spec(
    handlers: dict[str, HandlerSpec],
    filepath: Path,
) -> tuple[HandlerSpec, str, bool]:
    mime_type, _ = mimetypes.guess_type(str(filepath))
    ext_lower = filepath.suffix.lower()

    if ext_lower == ".csv":
        return handlers.get("csv_viewer", empty_handler_spec()), "terminal", False
    if ext_lower == ".parquet":
        return handlers.get("parquet_viewer", empty_handler_spec()), "terminal", False
    if ext_lower == ".h5":
        return handlers.get("h5_viewer", empty_handler_spec()), "terminal", False
    if ext_lower == ".xlsx":
        return handlers.get("xlsx_viewer", empty_handler_spec()), "external_background", False
    if mime_type == "application/pdf":
        return handlers.get("pdf_viewer", empty_handler_spec()), "external_background", False
    if mime_type and mime_type.startswith("image/"):
        return handlers.get("image_viewer", empty_handler_spec()), "external_background", False
    if (mime_type and mime_type.startswith("audio/")) or ext_lower in AUDIO_EXTENSIONS:
        return _select_media_handler_spec(handlers, "audio"), "external_background", False
    if (mime_type and mime_type.startswith("video/")) or ext_lower in VIDEO_EXTENSIONS:
        return _select_media_handler_spec(handlers, "video"), "external_background", False
    is_text_like = bool((mime_type and mime_type.startswith("text/")) or ext_lower in TEXT_LIKE_EXTENSIONS)
    return handlers.get("editor", empty_handler_spec()), "external_foreground", is_text_like


def resolve_download_name(filename: str, mime_type: str) -> str:
    return resolve_exported_name(filename, mime_type)


def _select_media_handler_spec(handlers: dict[str, HandlerSpec], kind: str) -> HandlerSpec:
    primary_name = "audio_player" if kind == "audio" else "video_player"
    primary = handlers.get(primary_name)
    if primary and primary.commands:
        return primary
    return handlers.get("media_player", empty_handler_spec())
