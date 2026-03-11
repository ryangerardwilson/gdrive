from __future__ import annotations

from pathlib import Path

FOLDER_MIME = "application/vnd.google-apps.folder"
EXPORT_MIME_TYPES: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
    "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json", ".json"),
}


def resolve_exported_name(filename: str, mime_type: str) -> str:
    export = EXPORT_MIME_TYPES.get(mime_type)
    if export is None:
        return filename
    _, suffix = export
    path = Path(filename)
    if path.suffix.lower() == suffix.lower():
        return path.name
    if path.suffix:
        return f"{path.stem}{suffix}"
    return f"{path.name}{suffix}"
