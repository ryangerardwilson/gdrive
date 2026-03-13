from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import CliError


@dataclass(slots=True)
class UploadSummary:
    files_uploaded: int = 0
    dirs_created: int = 0


def normalize_upload_paths(values: list[str]) -> list[Path]:
    if not values:
        raise CliError("usage: gdrive <preset> up <file_path> <file_path> ...")
    paths: list[Path] = []
    for raw_value in values:
        path = Path(raw_value).expanduser().resolve()
        if not path.exists():
            raise CliError(f"missing local path: {path}")
        if not path.is_file() and not path.is_dir():
            raise CliError(f"unsupported local path: {path}")
        paths.append(path)
    return paths


def upload_local_paths(client, parent_id: str, local_paths: list[Path]) -> UploadSummary:
    summary = UploadSummary()
    for path in local_paths:
        _upload_local_path(client, parent_id, path, summary)
    return summary


def _upload_local_path(client, parent_id: str, local_path: Path, summary: UploadSummary) -> None:
    remote_name = client.find_available_name(parent_id, local_path.name)
    if local_path.is_dir():
        folder_id = client.create_folder(parent_id, remote_name)
        summary.dirs_created += 1
        for child in sorted(local_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower(), item.name)):
            _upload_local_path(client, folder_id, child, summary)
        return
    client.upload_file(parent_id, remote_name, str(local_path))
    summary.files_uploaded += 1


def download_directory_as_zip(client, entry, target_path: Path) -> Path:
    with TemporaryDirectory(prefix="gdrive-dir-zip-") as tmp:
        tmp_root = Path(tmp)
        local_root = tmp_root / entry.name
        local_root.mkdir(parents=True, exist_ok=True)
        remote_entries = client.list_tree(entry.id)
        for relpath in sorted(remote_entries):
            remote_entry = remote_entries[relpath]
            local_path = local_root / remote_entry.relpath
            if remote_entry.is_dir:
                local_path.mkdir(parents=True, exist_ok=True)
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            client.download_entry(remote_entry, local_path)
        archive_base = target_path.with_suffix("")
        created_archive = shutil.make_archive(str(archive_base), "zip", root_dir=tmp_root, base_dir=entry.name)
        return Path(created_archive)
