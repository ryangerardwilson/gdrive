from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import CliError
from .paths import ensure_dirs, state_dir

if TYPE_CHECKING:
    from .drive_api import DriveClient


@dataclass(slots=True)
class LocalEntry:
    relpath: str
    kind: str
    size: int
    mtime_ns: int
    sha1: str | None


@dataclass(slots=True)
class StateEntry:
    relpath: str
    kind: str
    drive_id: str
    parent_relpath: str
    size: int
    mtime_ns: int
    sha1: str | None


@dataclass(slots=True)
class SyncSummary:
    created: int = 0
    updated: int = 0
    moved: int = 0
    deleted: int = 0


def state_file(reg_id: str) -> Path:
    ensure_dirs()
    return state_dir() / f"{reg_id}.json"


def load_state(reg_id: str) -> dict[str, StateEntry]:
    path = state_file(reg_id)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    return {item["relpath"]: StateEntry(**item) for item in payload.get("entries", [])}


def save_state(reg_id: str, entries: dict[str, StateEntry]) -> None:
    path = state_file(reg_id)
    payload = {"entries": [asdict(entries[key]) for key in sorted(entries)]}
    path.write_text(json.dumps(payload, indent=2) + "\n")


def delete_state(reg_id: str) -> None:
    path = state_file(reg_id)
    if path.exists():
        path.unlink()


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parent_relpath(relpath: str) -> str:
    if "/" not in relpath:
        return ""
    return relpath.rsplit("/", 1)[0]


def scan_local_tree(root_dir: Path, previous: dict[str, StateEntry]) -> dict[str, LocalEntry]:
    result: dict[str, LocalEntry] = {}
    for path in sorted(root_dir.rglob("*")):
        if path.is_symlink():
            continue
        relpath = path.relative_to(root_dir).as_posix()
        stat = path.stat()
        if path.is_dir():
            result[relpath] = LocalEntry(
                relpath=relpath,
                kind="dir",
                size=0,
                mtime_ns=stat.st_mtime_ns,
                sha1=None,
            )
            continue
        old = previous.get(relpath)
        sha1 = None
        if old and old.kind == "file" and old.size == stat.st_size and old.mtime_ns == stat.st_mtime_ns:
            sha1 = old.sha1
        if not sha1:
            sha1 = sha1_file(path)
        result[relpath] = LocalEntry(
            relpath=relpath,
            kind="file",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha1=sha1,
        )
    return result


def build_rename_map(
    old_entries: dict[str, StateEntry],
    new_entries: dict[str, LocalEntry],
) -> dict[str, str]:
    removed: list[StateEntry] = [entry for path, entry in old_entries.items() if path not in new_entries and entry.kind == "file"]
    added: list[LocalEntry] = [entry for path, entry in new_entries.items() if path not in old_entries and entry.kind == "file"]
    by_sig: dict[tuple[int, str | None], list[LocalEntry]] = defaultdict(list)
    for item in added:
        by_sig[(item.size, item.sha1)].append(item)
    rename_map: dict[str, str] = {}
    for item in removed:
        key = (item.size, item.sha1)
        bucket = by_sig.get(key)
        if bucket:
            target = bucket.pop(0)
            rename_map[item.relpath] = target.relpath
    return rename_map


def sort_paths_deep(paths: list[str], reverse: bool = False) -> list[str]:
    return sorted(paths, key=lambda item: (item.count("/"), item), reverse=reverse)


def sync_registration(registration, drive: DriveClient, backup_root_name: str) -> SyncSummary:
    local_root = Path(registration.local_dir)
    if not local_root.exists() or not local_root.is_dir():
        raise CliError(f"missing local dir: {local_root}")
    previous = load_state(registration.id)
    local_entries = scan_local_tree(local_root, previous)
    remote_root_id = registration.remote_root_id or drive.ensure_drive_path(
        f"{backup_root_name}/{registration.drive_path}"
    )
    registration.remote_root_id = remote_root_id
    remote_entries = drive.list_tree(remote_root_id)
    summary = SyncSummary()

    dir_ids = {"": remote_root_id}
    for relpath, entry in remote_entries.items():
        if entry.is_dir:
            dir_ids[relpath] = entry.id

    for relpath in sort_paths_deep([path for path, item in local_entries.items() if item.kind == "dir"]):
        existing = remote_entries.get(relpath)
        if existing and existing.is_dir:
            dir_ids[relpath] = existing.id
            continue
        parent_id = dir_ids[parent_relpath(relpath)]
        drive_id = drive.create_folder(parent_id, Path(relpath).name)
        dir_ids[relpath] = drive_id
        summary.created += 1

    rename_map = build_rename_map(previous, local_entries)
    current_state: dict[str, StateEntry] = {}

    removed_file_paths = {
        path for path, entry in previous.items()
        if entry.kind == "file" and path not in local_entries and path not in rename_map
    }

    for old_path, new_path in rename_map.items():
        old_state = previous[old_path]
        local_entry = local_entries[new_path]
        parent_id = dir_ids[parent_relpath(new_path)]
        old_parent = previous[old_path].parent_relpath
        old_parent_id = remote_root_id if not old_parent else dir_ids.get(old_parent) or previous[old_parent].drive_id
        drive.move_entry(old_state.drive_id, parent_id, Path(new_path).name, old_parent_id)
        summary.moved += 1
        current_state[new_path] = StateEntry(
            relpath=new_path,
            kind="file",
            drive_id=old_state.drive_id,
            parent_relpath=parent_relpath(new_path),
            size=local_entry.size,
            mtime_ns=local_entry.mtime_ns,
            sha1=local_entry.sha1,
        )

    for relpath in sort_paths_deep([path for path, item in local_entries.items() if item.kind == "file"]):
        if relpath in current_state:
            continue
        local_entry = local_entries[relpath]
        existing_remote = remote_entries.get(relpath)
        previous_entry = previous.get(relpath)
        parent_id = dir_ids[parent_relpath(relpath)]
        if existing_remote and existing_remote.is_dir:
            drive.delete_entry(existing_remote.id)
            summary.deleted += 1
            existing_remote = None
        if previous_entry and previous_entry.kind == "file":
            if existing_remote and not existing_remote.is_dir:
                drive_id = existing_remote.id
            else:
                drive_id = ""
            if not drive_id:
                drive_id = drive.upload_file(parent_id, Path(relpath).name, str(local_root / relpath))
                summary.created += 1
            elif (
                drive_id != previous_entry.drive_id
                or local_entry.size != previous_entry.size
                or local_entry.sha1 != previous_entry.sha1
            ):
                drive.update_file(drive_id, str(local_root / relpath))
                summary.updated += 1
            if previous_entry.parent_relpath != parent_relpath(relpath):
                old_parent_id = remote_root_id if not previous_entry.parent_relpath else dir_ids[previous_entry.parent_relpath]
                drive.move_entry(drive_id, parent_id, Path(relpath).name, old_parent_id)
                summary.moved += 1
            elif Path(relpath).name != Path(previous_entry.relpath).name:
                drive.rename_entry(drive_id, Path(relpath).name)
                summary.moved += 1
        elif existing_remote and not existing_remote.is_dir:
            drive_id = existing_remote.id
            drive.update_file(drive_id, str(local_root / relpath))
            summary.updated += 1
        else:
            drive_id = drive.upload_file(parent_id, Path(relpath).name, str(local_root / relpath))
            summary.created += 1
        current_state[relpath] = StateEntry(
            relpath=relpath,
            kind="file",
            drive_id=drive_id,
            parent_relpath=parent_relpath(relpath),
            size=local_entry.size,
            mtime_ns=local_entry.mtime_ns,
            sha1=local_entry.sha1,
        )

    for relpath in sort_paths_deep(list(removed_file_paths), reverse=True):
        drive.delete_entry(previous[relpath].drive_id)
        summary.deleted += 1

    desired_paths = set(local_entries)
    remote_extras = [
        relpath for relpath in remote_entries
        if relpath not in desired_paths and relpath not in previous
    ]
    for relpath in sort_paths_deep(remote_extras, reverse=True):
        drive.delete_entry(remote_entries[relpath].id)
        summary.deleted += 1

    local_dir_paths = [path for path, entry in local_entries.items() if entry.kind == "dir"]
    for relpath in sort_paths_deep(local_dir_paths):
        current_state[relpath] = StateEntry(
            relpath=relpath,
            kind="dir",
            drive_id=dir_ids[relpath],
            parent_relpath=parent_relpath(relpath),
            size=0,
            mtime_ns=local_entries[relpath].mtime_ns,
            sha1=None,
        )

    removed_dirs = [
        path for path, entry in previous.items()
        if entry.kind == "dir" and path not in local_entries
    ]
    for relpath in sort_paths_deep(removed_dirs, reverse=True):
        drive.delete_entry(previous[relpath].drive_id)
        summary.deleted += 1

    save_state(registration.id, current_state)
    return summary
