from __future__ import annotations

import curses
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import HandlerSpec
from .drive_api import DriveClient, NavEntry
from .file_handlers import resolve_download_name, select_handler_spec
from .transfer import UploadSummary, download_directory_as_folder, upload_local_paths


@dataclass(slots=True)
class FolderState:
    folder_id: str
    path: str
    entries: list[NavEntry]
    selected: int = 0
    scroll: int = 0


@dataclass(slots=True)
class DisplayItem:
    entry: NavEntry
    depth: int
    path: str


@dataclass(slots=True)
class ClipboardEntry:
    id: str
    name: str
    mime_type: str
    parent_id: str

    @property
    def is_dir(self) -> bool:
        return self.mime_type == "application/vnd.google-apps.folder"


@dataclass(slots=True)
class ClipboardState:
    entries: list[ClipboardEntry] = field(default_factory=list)
    cut: bool = False

    @property
    def has_entries(self) -> bool:
        return bool(self.entries)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def clear(self) -> None:
        self.entries = []
        self.cut = False

    def status_text(self) -> str:
        if not self.entries:
            return ""
        if len(self.entries) == 1:
            suffix = "/" if self.entries[0].is_dir else ""
            prefix = "CUT " if self.cut else ""
            return f"{prefix}{self.entries[0].name}{suffix}"
        prefix = "CUT " if self.cut else ""
        return f"{prefix}{len(self.entries)} items"


@dataclass(slots=True)
class DownloadJob:
    entry_name: str
    target_path: Path
    completed_path: Path | None = None
    error: str | None = None


@dataclass(slots=True)
class NavigatorResult:
    exit_code: int = 0
    upload_summary: UploadSummary | None = None
    upload_target_path: str | None = None
    cancelled: bool = False


CHEATSHEET = r"""
GDRIVE NAV CHEATSHEET

Navigation
  h               Parent directory
  l               Enter directory or open file through handlers
  j / k           Down / Up
  Enter           Download files or directories to cwd, or confirm upload target in up mode
  Esc             Exit visual mode, or quit when visual mode is off

Clipboard & Multi Operations
  m               Toggle mark on current item (✓) - auto-advance
  y               Yank (copy) all marked items into clipboard immediately
  yy              Yank current row into clipboard when nothing marked
  dd              Cut marked items (or current row) into clipboard
  p               Paste clipboard into selected directory
                  or into the current folder when a file is selected
  x               Prompt before deleting marked items or current entry

Visual Mode
  v               Enter visual selection; press v again to add range to marks
  j / k           Extend / shrink selection while in visual mode
  Esc             Exit visual mode without adding range

Other
  .               Repeat last repeatable command
  ?               Toggle this help
  q               Quit the app

Leader Commands (press "," first)
  ,xr             Toggle inline expansion / collapse for selection
  ,xc             Collapse all inline expansions
  ,xar            Expand all directories recursively
  ,k / ,j         Jump to top / bottom
  ,rn             Rename selected item
""".strip()


def _display_name(entry: NavEntry) -> str:
    return f"{entry.name}/" if entry.is_dir else entry.name


def _clamp_scroll(selected: int, scroll: int, height: int, total: int) -> int:
    if total <= 0 or height <= 0:
        return 0
    if selected < scroll:
        return selected
    bottom = scroll + height - 1
    if selected > bottom:
        return max(0, selected - height + 1)
    return min(scroll, max(0, total - height))


def _resolve_download_path(download_dir: Path, name: str) -> Path:
    target = download_dir / name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 10_000):
        candidate = download_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not allocate download path for {name}")


class DriveNavigator:
    def __init__(
        self,
        stdscr,
        client: DriveClient,
        preset: str,
        download_dir: Path,
        handlers: dict[str, HandlerSpec],
        upload_paths: list[Path] | None = None,
    ):
        self.stdscr = stdscr
        self.client = client
        self.preset = preset
        self.download_dir = download_dir
        self.handlers = handlers
        self.upload_paths = list(upload_paths or [])
        self.upload_mode = bool(self.upload_paths)
        if self.upload_mode:
            count = len(self.upload_paths)
            noun = "item" if count == 1 else "items"
            self.status_message = f"choose upload destination and press Enter ({count} {noun})"
        else:
            self.status_message = f"download dir {self._pretty_path(download_dir)}"
        self.show_help = False
        self.help_scroll = 0
        self.marked_ids: set[str] = set()
        self.clipboard = ClipboardState()
        self.pending_operator: str | None = None
        self.pending_comma = False
        self.comma_sequence = ""
        self.expanded_ids: set[str] = set()
        self.children_cache: dict[str, list[NavEntry]] = {}
        self.visual_mode = False
        self.visual_anchor_index: int | None = None
        self.visual_active_index: int | None = None
        self.last_repeat_sequence: list[int] | None = None
        self.is_repeating = False
        self._spinner_lock = threading.Lock()
        self._spinner_message = ""
        self._spinner_frame = 0
        self._spinner_stop: threading.Event | None = None
        self._cut_hidden_ids: set[str] = set()
        self._cut_hidden_until: float | None = None
        self._download_lock = threading.Lock()
        self._active_downloads: dict[str, DownloadJob] = {}
        self._completed_downloads: list[DownloadJob] = []
        self.result = NavigatorResult()
        self.stack = [self._load_folder("root", "/")]

    @property
    def current(self) -> FolderState:
        return self.stack[-1]

    def _load_folder(self, folder_id: str, path: str) -> FolderState:
        entries = self._run_with_spinner("loading folder...", lambda: self.client.list_children(folder_id))
        self.children_cache[folder_id] = entries
        return FolderState(folder_id=folder_id, path=path, entries=entries)

    def _render_status_only(self) -> None:
        if self.show_help:
            return
        height, width = self.stdscr.getmaxyx()
        items = self._display_items()
        scroll_indicator = ""
        list_height = max(0, height - 3)
        if len(items) > list_height and list_height > 0:
            top = self.current.scroll + 1
            bottom = min(len(items), self.current.scroll + list_height)
            scroll_indicator = f"[{top}-{bottom}/{len(items)}]"
        self._render_line(height - 1, self._compose_status(scroll_indicator=scroll_indicator)[: max(0, width - 1)])
        self.stdscr.refresh()

    def _spinner_worker(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            with self._spinner_lock:
                self._spinner_frame = (self._spinner_frame + 1) % 10
            try:
                self._render_status_only()
            except Exception:
                pass
            time.sleep(0.08)

    def _busy_indicator(self) -> str:
        with self._spinner_lock:
            message = self._spinner_message
            frame = self._spinner_frame
        if not message:
            return ""
        dots = "." * ((frame % 3) + 1)
        pulse = "><>•"[frame % 4]
        return f"{pulse} {message}{dots}"

    def _download_indicator(self) -> str:
        with self._download_lock:
            jobs = list(self._active_downloads.values())
        if not jobs:
            return ""
        frame = int(time.monotonic() * 10) % 4
        pulse = "><>•"[frame]
        if len(jobs) == 1:
            return f"{pulse} dl {jobs[0].entry_name}"
        return f"{pulse} dl {len(jobs)} files"

    def _run_with_spinner(self, message: str, func):
        if self._spinner_stop is not None:
            return func()
        stop_event = threading.Event()
        with self._spinner_lock:
            self._spinner_message = message
            self._spinner_frame = 0
            self._spinner_stop = stop_event
        worker = threading.Thread(target=self._spinner_worker, args=(stop_event,), daemon=True)
        worker.start()
        try:
            return func()
        finally:
            stop_event.set()
            worker.join(timeout=0.2)
            with self._spinner_lock:
                self._spinner_message = ""
                self._spinner_stop = None
            try:
                self._render_status_only()
            except Exception:
                pass

    def _download_worker(self, job_id: str, entry: NavEntry, target_path: Path) -> None:
        completed_path: Path | None = None
        error: str | None = None
        try:
            completed_path = self.client.download_entry(entry, target_path)
        except Exception as exc:
            error = str(exc)
        with self._download_lock:
            job = self._active_downloads.pop(job_id, None)
            if job is None:
                return
            job.completed_path = completed_path
            job.error = error
            self._completed_downloads.append(job)

    def _download_target_path(self, base_dir: Path, entry: NavEntry) -> Path:
        filename = resolve_download_name(entry.name, entry.mime_type)
        return _resolve_download_path(base_dir, filename)

    def _start_download(self, entry: NavEntry) -> None:
        target_path = self._download_target_path(self.download_dir, entry)
        job_id = f"{entry.id}:{time.monotonic_ns()}"
        job = DownloadJob(entry_name=entry.name, target_path=target_path)
        with self._download_lock:
            self._active_downloads[job_id] = job
        worker = threading.Thread(
            target=self._download_worker,
            args=(job_id, entry, target_path),
            daemon=True,
        )
        worker.start()
        self.status_message = f"started download {entry.name}"

    def _poll_background_jobs(self) -> None:
        with self._download_lock:
            completed = list(self._completed_downloads)
            self._completed_downloads.clear()
        if not completed:
            return
        latest = completed[-1]
        if latest.error:
            self.status_message = latest.error
            return
        self.status_message = f"downloaded {latest.entry_name} -> {latest.completed_path}"

    def _expand_command(self, raw_cmd: list[str], filepath: str) -> list[str] | None:
        if not raw_cmd:
            return None
        tokens: list[str] = []
        has_placeholder = False
        for part in raw_cmd:
            replaced = part.replace("{file}", filepath)
            if replaced != part:
                has_placeholder = True
            tokens.append(replaced)
        if not tokens:
            return None
        if not has_placeholder:
            tokens.append(filepath)
        return tokens

    def _run_external_handlers(self, handlers: list[list[str]], filepath: str, *, background: bool) -> bool:
        for raw_cmd in handlers:
            tokens = self._expand_command(raw_cmd, filepath)
            if not tokens:
                continue
            if shutil.which(tokens[0]) is None:
                continue
            try:
                if background:
                    subprocess.Popen(
                        tokens,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        preexec_fn=os.setsid,
                    )
                else:
                    return_code = subprocess.call(tokens)
                    if return_code != 0:
                        self.status_message = f"handler failed: {tokens[0]}"
                        curses.flash()
                        return True
                return True
            except FileNotFoundError:
                continue
            except Exception:
                continue
        return False

    def _run_internal_handler(self, handlers: list[list[str]], filepath: str) -> bool:
        attempted = False
        last_cmd = ""
        self._suspend_curses()
        try:
            for raw_cmd in handlers:
                tokens = self._expand_command(raw_cmd, filepath)
                if not tokens:
                    continue
                if shutil.which(tokens[0]) is None:
                    continue
                attempted = True
                last_cmd = tokens[0]
                try:
                    return_code = subprocess.call(tokens)
                except FileNotFoundError:
                    continue
                except Exception:
                    continue
                if return_code == 0:
                    self.status_message = f"handler exited: {tokens[0]}"
                    return True
                self.status_message = f"handler failed: {tokens[0]}"
                curses.flash()
                return True
        finally:
            self._resume_curses()
        if attempted:
            self.status_message = f"handler failed: {last_cmd or 'handler'}"
            curses.flash()
            return True
        return False

    def _open_terminal(self, command: list[str]) -> bool:
        commands: list[list[str]] = []
        terminal_env = os.environ.get("TERMINAL")
        if terminal_env:
            commands.append(shlex.split(terminal_env))
        commands.extend([[name] for name in ("alacritty", "foot", "kitty", "wezterm", "gnome-terminal", "xterm")])
        for raw_cmd in commands:
            if not raw_cmd:
                continue
            if shutil.which(raw_cmd[0]) is None:
                continue
            launch_cmd = list(raw_cmd)
            if any("{cmd}" in token for token in launch_cmd):
                launch_cmd = [token.replace("{cmd}", " ".join(command)) for token in launch_cmd]
            else:
                launch_cmd.extend(["-e", *command])
            try:
                subprocess.Popen(
                    launch_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    preexec_fn=os.setsid,
                )
                self.status_message = f"opened terminal: {launch_cmd[0]}"
                return True
            except Exception:
                continue
        self.status_message = "no terminal found"
        curses.flash()
        return False

    def _run_terminal_handlers(self, handlers: list[list[str]], filepath: str) -> bool:
        for raw_cmd in handlers:
            tokens = self._expand_command(raw_cmd, filepath)
            if not tokens:
                continue
            if self._open_terminal(tokens):
                return True
        return False

    def _suspend_curses(self) -> None:
        try:
            curses.def_prog_mode()
        except curses.error:
            pass
        try:
            curses.endwin()
        except curses.error:
            pass

    def _resume_curses(self) -> None:
        try:
            curses.reset_prog_mode()
        except curses.error:
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            self.stdscr.refresh()
        except Exception:
            pass

    def _invoke_handler(self, spec: HandlerSpec, filepath: Path, *, default_strategy: str) -> bool:
        if not spec.commands:
            return False
        if spec.is_internal:
            return self._run_internal_handler(spec.commands, str(filepath))
        if default_strategy == "terminal":
            return self._run_terminal_handlers(spec.commands, str(filepath))
        if default_strategy == "external_background":
            return self._run_external_handlers(spec.commands, str(filepath), background=True)
        return self._run_external_handlers(spec.commands, str(filepath), background=False)

    def _open_with_vim(self, filepath: Path) -> bool:
        if shutil.which("vim") is None:
            return False
        self._suspend_curses()
        try:
            subprocess.call(["vim", str(filepath)])
            return True
        except Exception:
            return False
        finally:
            self._resume_curses()

    def _open_downloaded_file(self, path: Path) -> bool:
        spec, default_strategy, is_text_like = select_handler_spec(self.handlers, path)
        handled = False
        try:
            handled = self._invoke_handler(spec, path, default_strategy=default_strategy)
        except FileNotFoundError:
            handled = False
        if not handled and is_text_like:
            handled = self._open_with_vim(path)
        return handled

    def _selection_target_folder(self) -> tuple[str, str]:
        selected_item = self._selected_item()
        if selected_item is None:
            return self.current.folder_id, self.current.path
        if selected_item.entry.is_dir:
            return selected_item.entry.id, selected_item.path
        return selected_item.entry.parent_id or self.current.folder_id, self._parent_path(selected_item.path)

    def _confirm_upload_target(self) -> bool:
        target_folder_id, target_folder_path = self._selection_target_folder()
        try:
            summary = self._run_with_spinner(
                f"uploading to {target_folder_path}",
                lambda: upload_local_paths(self.client, target_folder_id, self.upload_paths),
            )
        except Exception as exc:
            self.status_message = str(exc)
            return False
        self.result.upload_summary = summary
        self.result.upload_target_path = target_folder_path
        self.status_message = f"uploaded to {target_folder_path}"
        return True

    def _enter_directory(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.status_message = "empty folder"
            return
        if not entry.is_dir:
            self.status_message = "select a directory"
            return
        self._exit_visual_mode()
        next_path = "/" if self.current.path == "/" and not entry.name else f"{self.current.path.rstrip('/')}/{entry.name}"
        self.stack.append(self._load_folder(entry.id, next_path))
        self.status_message = next_path

    def _open_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.status_message = "empty folder"
            return
        try:
            if entry.is_dir:
                self._enter_directory()
                return
            temp_dir = Path(tempfile.mkdtemp(prefix="gdrive-nav-"))
            target_path = self._download_target_path(temp_dir, entry)
            resolved_path = self._run_with_spinner(
                f"downloading {entry.name}",
                lambda: self.client.download_entry(entry, target_path),
            )
            if self._open_downloaded_file(resolved_path):
                return
            else:
                self.status_message = f"no handler configured; temp file kept at {resolved_path}"
                curses.flash()
        except Exception as exc:
            self.status_message = str(exc)

    def _display_items(self) -> list[DisplayItem]:
        self._refresh_cut_overlay()
        items: list[DisplayItem] = []
        for entry in self.current.entries:
            if self._is_temporarily_hidden(entry.id):
                continue
            entry_path = self._join_path(self.current.path, entry.name)
            items.append(DisplayItem(entry=entry, depth=0, path=entry_path))
            if entry.is_dir and entry.id in self.expanded_ids:
                self._append_expanded(entry.id, entry_path, depth=1, items=items)
        return items

    def _append_expanded(self, folder_id: str, parent_path: str, depth: int, items: list[DisplayItem]) -> None:
        children = self.children_cache.get(folder_id)
        if children is None:
            children = self._run_with_spinner("loading folder...", lambda: self.client.list_children(folder_id))
            self.children_cache[folder_id] = children
        for child in children:
            if self._is_temporarily_hidden(child.id):
                continue
            child_path = self._join_path(parent_path, child.name)
            items.append(DisplayItem(entry=child, depth=depth, path=child_path))
            if child.is_dir and child.id in self.expanded_ids:
                self._append_expanded(child.id, child_path, depth + 1, items)

    @staticmethod
    def _join_path(parent: str, name: str) -> str:
        if parent == "/":
            return f"/{name}"
        return f"{parent.rstrip('/')}/{name}"

    @staticmethod
    def _parent_path(path: str) -> str:
        if path == "/":
            return "/"
        parent = path.rsplit("/", 1)[0]
        return parent or "/"

    def _selected_item(self) -> DisplayItem | None:
        items = self._display_items()
        if not items:
            return None
        index = max(0, min(self.current.selected, len(items) - 1))
        self.current.selected = index
        return items[index]

    def _selected_entry(self) -> NavEntry | None:
        item = self._selected_item()
        return item.entry if item else None

    def _clear_operator(self) -> None:
        self.pending_operator = None
        self.pending_comma = False
        self.comma_sequence = ""

    def _record_repeat_sequence(self, keys: list[int]) -> None:
        if self.is_repeating:
            return
        self.last_repeat_sequence = list(keys) if keys else None

    def _clear_cut_overlay(self) -> None:
        self._cut_hidden_ids.clear()
        self._cut_hidden_until = None

    def _refresh_cut_overlay(self) -> None:
        if self._cut_hidden_until is None:
            return
        if time.monotonic() < self._cut_hidden_until:
            return
        self._clear_cut_overlay()

    def _is_temporarily_hidden(self, entry_id: str) -> bool:
        self._refresh_cut_overlay()
        return entry_id in self._cut_hidden_ids

    def _reload_current_folder(self) -> None:
        current = self.current
        selected_entry = self._selected_entry()
        selected_id = selected_entry.id if selected_entry else None
        refreshed = self._load_folder(current.folder_id, current.path)
        self.stack[-1] = refreshed
        display_items = self._display_items()
        if selected_id:
            for index, item in enumerate(display_items):
                if item.entry.id == selected_id:
                    refreshed.selected = index
                    break
            else:
                refreshed.selected = min(current.selected, max(0, len(display_items) - 1))
        else:
            refreshed.selected = min(current.selected, max(0, len(display_items) - 1))
        refreshed.scroll = current.scroll
        self._clear_missing_marks()

    def _clear_missing_marks(self) -> None:
        visible_ids = set(self.children_cache.keys())
        for entries in self.children_cache.values():
            visible_ids.update(entry.id for entry in entries)
        self.marked_ids.intersection_update(visible_ids)

    def _mark_current(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.status_message = "nothing to mark"
            return
        if entry.id in self.marked_ids:
            self.marked_ids.remove(entry.id)
            self.status_message = f"unmarked {entry.name}"
        else:
            self.marked_ids.add(entry.id)
            self.status_message = f"marked {entry.name}"
            self._move(1)

    def _selected_or_marked_entries(self) -> list[NavEntry]:
        display_items = self._display_items()
        if self.marked_ids:
            entries: list[NavEntry] = []
            seen: set[str] = set()
            for item in display_items:
                if item.entry.id in self.marked_ids and item.entry.id not in seen:
                    entries.append(item.entry)
                    seen.add(item.entry.id)
            return sorted(entries, key=lambda item: (item.name.lower(), item.name))
        current = self._selected_entry()
        return [current] if current else []

    def _visual_indices(self, total: int) -> list[int]:
        if not self.visual_mode or self.visual_anchor_index is None or self.visual_active_index is None:
            return []
        start = max(0, min(self.visual_anchor_index, self.visual_active_index))
        end = min(total - 1, max(self.visual_anchor_index, self.visual_active_index))
        return list(range(start, end + 1)) if total > 0 else []

    def _visual_entries(self) -> list[NavEntry]:
        items = self._display_items()
        return [items[index].entry for index in self._visual_indices(len(items)) if 0 <= index < len(items)]

    def _enter_visual_mode(self) -> None:
        items = self._display_items()
        if not items:
            return
        self.visual_mode = True
        self.visual_anchor_index = self.current.selected
        self.visual_active_index = self.current.selected
        self.status_message = "-- VISUAL --"

    def _exit_visual_mode(self, *, clear_message: bool = True) -> None:
        self.visual_mode = False
        self.visual_anchor_index = None
        self.visual_active_index = None
        if clear_message:
            self.status_message = ""

    def _commit_visual_marks(self) -> None:
        entries = self._visual_entries()
        if not entries:
            self._exit_visual_mode()
            return
        for entry in entries:
            self.marked_ids.add(entry.id)
        count = len(entries)
        noun = "item" if count == 1 else "items"
        self._exit_visual_mode(clear_message=False)
        self.status_message = f"Pinned {count} {noun}"

    def _stage_clipboard(self, entries: list[NavEntry], *, cut: bool) -> None:
        if not entries:
            self.status_message = "nothing selected"
            return
        self.clipboard.entries = [
            ClipboardEntry(id=entry.id, name=entry.name, mime_type=entry.mime_type, parent_id=entry.parent_id)
            for entry in entries
        ]
        self.clipboard.cut = cut
        count = len(entries)
        noun = "item" if count == 1 else "items"
        action = "Cut" if cut else "Yanked"
        self.status_message = f"{action} {count} {noun} to clipboard"
        self.marked_ids.clear()
        if cut:
            self._cut_hidden_ids = {entry.id for entry in entries}
            self._cut_hidden_until = time.monotonic() + 10.0
        else:
            self._clear_cut_overlay()

    def _target_folder_id(self) -> str:
        selected = self._selected_entry()
        if selected and selected.is_dir:
            return selected.id
        return self.current.folder_id

    def _current_selected_parent_id(self) -> str:
        selected = self._selected_entry()
        if selected:
            return selected.parent_id
        return self.current.folder_id

    def _copy_entry_recursive(self, source: ClipboardEntry, target_parent_id: str) -> None:
        new_name = self.client.find_available_name(target_parent_id, source.name)
        if source.is_dir:
            new_folder_id = self.client.create_folder(target_parent_id, new_name)
            for child in self.client.list_children(source.id):
                self._copy_entry_recursive(
                    ClipboardEntry(
                        id=child.id,
                        name=child.name,
                        mime_type=child.mime_type,
                        parent_id=child.parent_id,
                    ),
                    new_folder_id,
                )
            return
        self.client.copy_file(source.id, target_parent_id, new_name)

    def _move_entry(self, source: ClipboardEntry, target_parent_id: str) -> None:
        new_name = self.client.find_available_name(target_parent_id, source.name)
        self.client.move_entry(
            file_id=source.id,
            new_parent_id=target_parent_id,
            new_name=new_name,
            old_parent_id=source.parent_id,
        )

    def _paste_clipboard(self) -> None:
        if not self.clipboard.has_entries:
            self.status_message = "clipboard empty"
            return
        target_parent_id = self._target_folder_id()
        try:
            for entry in list(self.clipboard.entries):
                if self.clipboard.cut:
                    self._run_with_spinner(f"moving {entry.name}", lambda entry=entry: self._move_entry(entry, target_parent_id))
                else:
                    self._run_with_spinner(f"copying {entry.name}", lambda entry=entry: self._copy_entry_recursive(entry, target_parent_id))
            count = self.clipboard.entry_count
            noun = "item" if count == 1 else "items"
            action = "Moved" if self.clipboard.cut else "Pasted"
            self.status_message = f"{action} {count} {noun}"
            if self.clipboard.cut:
                self.clipboard.clear()
                self._clear_cut_overlay()
            self._reload_current_folder()
            self._clear_missing_marks()
        except Exception as exc:
            self.status_message = str(exc)

    def _delete_prompt(self, entries: list[NavEntry]) -> bool:
        count = len(entries)
        noun = "item" if count == 1 else "items"
        prompt = f"Delete {count} {noun}? [y/N]"
        height, width = self.stdscr.getmaxyx()
        self._render_line(height - 1, prompt[: max(0, width - 1)], curses.A_BOLD)
        while True:
            key = self.stdscr.getch()
            if key in (ord("y"), ord("Y")):
                return True
            if key in (ord("n"), ord("N"), 27, 10, 13):
                return False

    def _delete_entries(self) -> None:
        entries = self._visual_entries() if self.visual_mode else self._selected_or_marked_entries()
        if not entries:
            self.status_message = "nothing selected"
            return
        if not self._delete_prompt(entries):
            self.status_message = "Deletion cancelled"
            return
        try:
            for entry in entries:
                self._run_with_spinner(f"deleting {entry.name}", lambda entry=entry: self.client.delete_entry(entry.id))
            count = len(entries)
            noun = "item" if count == 1 else "items"
            self.status_message = f"Deleted {count} {noun}"
            self.marked_ids.difference_update({entry.id for entry in entries})
            if self.visual_mode:
                self._exit_visual_mode(clear_message=False)
            self._reload_current_folder()
        except Exception as exc:
            self.status_message = str(exc)

    def _pretty_path(self, path: Path) -> str:
        try:
            return str(path).replace(str(Path.home()), "~", 1)
        except Exception:
            return str(path)

    @staticmethod
    def _is_word_char(ch: str) -> bool:
        return ch.isalnum() or ch == "_"

    def _move_word_left(self, text: str, cursor: int) -> int:
        i = max(0, min(cursor, len(text)))
        while i > 0 and not self._is_word_char(text[i - 1]):
            i -= 1
        while i > 0 and self._is_word_char(text[i - 1]):
            i -= 1
        return i

    def _move_word_right(self, text: str, cursor: int) -> int:
        n = len(text)
        i = max(0, min(cursor, n))
        while i < n and not self._is_word_char(text[i]):
            i += 1
        while i < n and self._is_word_char(text[i]):
            i += 1
        return i

    def _delete_prev_word(self, text: str, cursor: int) -> tuple[str, int]:
        if cursor <= 0:
            return text, cursor
        start = self._move_word_left(text, cursor)
        return text[:start] + text[cursor:], start

    def _read_key_with_meta(self) -> tuple[int, int | None]:
        key = self.stdscr.getch()
        if key != 27:
            return key, None
        self.stdscr.timeout(25)
        next_key = self.stdscr.getch()
        self.stdscr.timeout(-1)
        if next_key == -1:
            return 27, None
        return 27, next_key

    def _render_prompt_input(
        self,
        prompt_y: int,
        max_x: int,
        prompt_display: str,
        text: str,
        cursor: int,
    ) -> None:
        available = max(1, max_x - len(prompt_display) - 1)
        max_start = max(0, len(text) - available)
        viewport_start = max(0, cursor - available + 1)
        if cursor < viewport_start:
            viewport_start = cursor
        if viewport_start > max_start:
            viewport_start = max_start
        visible = text[viewport_start : viewport_start + available]
        cursor_screen_x = min(max_x - 1, len(prompt_display) + (cursor - viewport_start))

        self.stdscr.move(prompt_y, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addstr(prompt_y, 0, prompt_display)
        if visible:
            self.stdscr.addstr(prompt_y, len(prompt_display), visible)
        self.stdscr.move(prompt_y, cursor_screen_x)
        self.stdscr.refresh()

    def _download_selected_to_pwd(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.status_message = "empty folder"
            return
        try:
            if entry.is_dir:
                target_path = _resolve_download_path(self.download_dir, entry.name)
                resolved_path = self._run_with_spinner(
                    f"downloading {entry.name}",
                    lambda: download_directory_as_folder(self.client, entry, target_path),
                )
                self.status_message = f"downloaded {entry.name}/ -> {resolved_path}"
                return
            self._start_download(entry)
        except Exception as exc:
            self.status_message = str(exc)

    def _back(self) -> None:
        if len(self.stack) == 1:
            self.status_message = "at root"
            return
        self._exit_visual_mode()
        self.stack.pop()
        self.status_message = self.current.path

    def _move(self, delta: int) -> None:
        items = self._display_items()
        if not items:
            self.current.selected = 0
            return
        self.current.selected = max(0, min(len(items) - 1, self.current.selected + delta))
        if self.visual_mode:
            self.visual_active_index = self.current.selected

    def _toggle_expand_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.is_dir:
            self.status_message = "select a directory"
            return
        if entry.id in self.expanded_ids:
            for folder_id in list(self.expanded_ids):
                if folder_id == entry.id:
                    self.expanded_ids.discard(folder_id)
            self.status_message = f"collapsed {entry.name}"
            return
        self.children_cache[entry.id] = self._run_with_spinner("expanding directory...", lambda: self.client.list_children(entry.id))
        self.expanded_ids.add(entry.id)
        self.status_message = f"expanded {entry.name}"

    def _collapse_all_expanded(self) -> None:
        self.expanded_ids.clear()
        self.status_message = "collapsed all"

    def _expand_all_recursive(self) -> None:
        count = 0
        queue = [item.entry for item in self._display_items() if item.entry.is_dir]
        seen: set[str] = set()
        while queue:
            entry = queue.pop(0)
            if entry.id in seen:
                continue
            seen.add(entry.id)
            children = self._run_with_spinner(f"expanding {entry.name}", lambda entry=entry: self.client.list_children(entry.id))
            self.children_cache[entry.id] = children
            if entry.id not in self.expanded_ids:
                self.expanded_ids.add(entry.id)
                count += 1
            for child in children:
                if child.is_dir:
                    queue.append(child)
        self.status_message = f"expanded {count} directories" if count else "no directories to expand"

    def _jump_to(self, which: str) -> None:
        items = self._display_items()
        if not items:
            return
        self.current.selected = 0 if which == "top" else len(items) - 1
        if self.visual_mode:
            self.visual_active_index = self.current.selected

    def _prompt_input(self, prompt: str, initial: str = "") -> str | None:
        max_y, max_x = self.stdscr.getmaxyx()
        if max_y < 2 or max_x < 20:
            return None

        prompt_y = max_y - 1
        prompt_display = prompt[: max_x - 1] if max_x > 0 else ""
        max_input_width = max(10, max_x - len(prompt_display) - 1)
        text = initial[:max_input_width]
        cursor = len(text)

        leaveok_changed = False
        try:
            self.stdscr.timeout(-1)
            try:
                self.stdscr.leaveok(False)
                leaveok_changed = True
            except Exception:
                pass
            try:
                curses.curs_set(1)
            except curses.error:
                pass

            self._render_prompt_input(prompt_y, max_x, prompt_display, text, cursor)

            while True:
                key, meta = self._read_key_with_meta()
                if key in (10, 13, curses.KEY_ENTER):
                    break
                if key == 27 and meta is None:
                    text = ""
                    break

                handled = False
                if key == 27 and meta is not None:
                    if meta in (ord("b"), ord("B")):
                        cursor = self._move_word_left(text, cursor)
                        handled = True
                    elif meta in (ord("f"), ord("F")):
                        cursor = self._move_word_right(text, cursor)
                        handled = True
                    elif meta in (127, 8, curses.KEY_BACKSPACE):
                        text, cursor = self._delete_prev_word(text, cursor)
                        handled = True
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    if cursor > 0:
                        text = text[: cursor - 1] + text[cursor:]
                        cursor -= 1
                    handled = True
                elif key == curses.KEY_DC:
                    if cursor < len(text):
                        text = text[:cursor] + text[cursor + 1 :]
                    handled = True
                elif key in (curses.KEY_LEFT, 2):
                    cursor = max(0, cursor - 1)
                    handled = True
                elif key in (curses.KEY_RIGHT, 6):
                    cursor = min(len(text), cursor + 1)
                    handled = True
                elif key in (curses.KEY_HOME, 1):
                    cursor = 0
                    handled = True
                elif key in (curses.KEY_END, 5):
                    cursor = len(text)
                    handled = True
                elif key == 23:
                    text, cursor = self._delete_prev_word(text, cursor)
                    handled = True
                elif 32 <= key <= 126 and len(text) < max_input_width:
                    text = text[:cursor] + chr(key) + text[cursor:]
                    cursor += 1
                    handled = True

                if handled:
                    self._render_prompt_input(prompt_y, max_x, prompt_display, text, cursor)
        finally:
            self.stdscr.timeout(80)
            if leaveok_changed:
                try:
                    self.stdscr.leaveok(True)
                except Exception:
                    pass
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        result = text.strip()
        return result or None

    def _rename_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.status_message = "nothing selected"
            return
        new_name = self._prompt_input("Rename to: ", entry.name)
        if new_name is None:
            self.status_message = "Rename cancelled"
            return
        if not new_name or new_name == entry.name:
            self.status_message = "Rename cancelled"
            return
        try:
            unique_name = self.client.find_available_name(entry.parent_id, new_name)
            self._run_with_spinner(f"renaming {entry.name}", lambda: self.client.rename_entry(entry.id, unique_name))
            self.status_message = f"Renamed to {unique_name}"
            self._reload_current_folder()
        except Exception as exc:
            self.status_message = str(exc)

    def _handle_normal_key(self, key: int) -> bool:
        if key == 27 and self.visual_mode:
            self._clear_operator()
            self._exit_visual_mode(clear_message=False)
            self.status_message = "Visual cancelled"
            return False
        if key in (ord("q"), 27):
            if self.upload_mode and self.result.upload_summary is None:
                self.result.cancelled = True
            return True
        if self.pending_comma:
            if 32 <= key <= 126:
                self.comma_sequence += chr(key)
                if self.comma_sequence == "j":
                    self._jump_to("bottom")
                    self._record_repeat_sequence([ord(","), ord("j")])
                    self._clear_operator()
                    return False
                if self.comma_sequence == "k":
                    self._jump_to("top")
                    self._record_repeat_sequence([ord(","), ord("k")])
                    self._clear_operator()
                    return False
                if self.comma_sequence == "xr":
                    self._toggle_expand_selected()
                    self._record_repeat_sequence([ord(","), ord("x"), ord("r")])
                    self._clear_operator()
                    return False
                if self.comma_sequence == "xc":
                    self._collapse_all_expanded()
                    self._record_repeat_sequence([ord(","), ord("x"), ord("c")])
                    self._clear_operator()
                    return False
                if self.comma_sequence == "xar":
                    self._expand_all_recursive()
                    self._record_repeat_sequence([ord(","), ord("x"), ord("a"), ord("r")])
                    self._clear_operator()
                    return False
                if self.comma_sequence == "rn":
                    self._rename_selected()
                    self._record_repeat_sequence([ord(","), ord("r"), ord("n")])
                    self._clear_operator()
                    return False
                if not any(cmd.startswith(self.comma_sequence) for cmd in ("j", "k", "xr", "xc", "xar", "rn")):
                    self._clear_operator()
                return False
            self._clear_operator()
            return False
        if key == ord("."):
            if self.is_repeating:
                return False
            if not self.last_repeat_sequence:
                self.status_message = "Nothing to repeat"
                return False
            sequence = list(self.last_repeat_sequence)
            self.is_repeating = True
            try:
                for seq_key in sequence:
                    if self._handle_normal_key(seq_key):
                        return True
            finally:
                self.is_repeating = False
            return False
        if key == ord("?"):
            self._clear_operator()
            self.show_help = True
            self.help_scroll = 0
            return False
        if key == ord(","):
            self.pending_comma = True
            self.comma_sequence = ""
            return False
        if key in (ord("j"), curses.KEY_DOWN):
            self._clear_operator()
            self._move(1)
            return False
        if key in (ord("k"), curses.KEY_UP):
            self._clear_operator()
            self._move(-1)
            return False
        if key in (ord("h"), curses.KEY_LEFT, curses.KEY_BACKSPACE, 127):
            self._clear_operator()
            self._back()
            return False
        if key in (ord("l"), curses.KEY_RIGHT):
            self._clear_operator()
            self._open_selected()
            return False
        if key in (10, 13, curses.KEY_ENTER):
            self._clear_operator()
            if self.upload_mode:
                if self._confirm_upload_target():
                    return True
                return False
            self._download_selected_to_pwd()
            return False
        if key == ord("m"):
            self._clear_operator()
            self._exit_visual_mode()
            self._mark_current()
            self._record_repeat_sequence([ord("m")])
            return False
        if key == ord("v"):
            self._clear_operator()
            if self.visual_mode:
                self._commit_visual_marks()
            else:
                self._enter_visual_mode()
            return False
        if key == ord("p"):
            self._clear_operator()
            self._exit_visual_mode()
            self._paste_clipboard()
            self._record_repeat_sequence([ord("p")])
            return False
        if key == ord("x"):
            self._clear_operator()
            self._delete_entries()
            self._record_repeat_sequence([ord("x")])
            return False
        if key == ord("y"):
            if self.pending_operator == "y":
                self._stage_clipboard(self._visual_entries() if self.visual_mode else self._selected_or_marked_entries(), cut=False)
                self._clear_operator()
                self._exit_visual_mode()
                self._record_repeat_sequence([ord("y"), ord("y")])
                return False
            if self.marked_ids:
                self._stage_clipboard(self._selected_or_marked_entries(), cut=False)
                self._clear_operator()
                self._record_repeat_sequence([ord("y")])
                return False
            self.pending_operator = "y"
            self.status_message = "y"
            return False
        if key == ord("d"):
            if self.pending_operator == "d":
                self._stage_clipboard(self._visual_entries() if self.visual_mode else self._selected_or_marked_entries(), cut=True)
                self._clear_operator()
                self._exit_visual_mode()
                self._reload_current_folder()
                self._record_repeat_sequence([ord("d"), ord("d")])
                return False
            self.pending_operator = "d"
            self.status_message = "d"
            return False
        self._clear_operator()
        return False

    def _render_line(self, y: int, text: str, attr: int = 0) -> None:
        height, width = self.stdscr.getmaxyx()
        if y >= height:
            return
        try:
            self.stdscr.move(y, 0)
            self.stdscr.clrtoeol()
            self.stdscr.addnstr(y, 0, text, max(0, width - 1), attr)
        except curses.error:
            pass

    def _compose_status(self, *, scroll_indicator: str = "") -> str:
        parts: list[str] = []
        if not self.show_help:
            parts.append("? help")
        clip = self.clipboard.status_text()
        if clip:
            parts.append(f"CLIP: {clip}")
        if self.upload_mode and self.upload_paths:
            count = len(self.upload_paths)
            noun = "item" if count == 1 else "items"
            parts.append(f"UP: {count} {noun}")
        if self.marked_ids:
            parts.append(f"MARKED: {len(self.marked_ids)}")
        visual_count = len(self._visual_indices(len(self._display_items())))
        if visual_count:
            noun = "item" if visual_count == 1 else "items"
            parts.append(f"-- VISUAL -- ({visual_count} {noun})")
        if self.pending_comma:
            parts.append("," + self.comma_sequence)
        if self.pending_operator:
            parts.append(self.pending_operator)
        if scroll_indicator.strip():
            parts.append(scroll_indicator.strip())
        busy = self._busy_indicator()
        if busy:
            parts.append(busy)
        download_busy = self._download_indicator()
        if download_busy:
            parts.append(download_busy)
        if self.status_message:
            parts.append(self.status_message)
        return "  ".join(parts) if parts else " "

    def _render_help(self) -> None:
        height, width = self.stdscr.getmaxyx()
        lines = [line.rstrip() for line in CHEATSHEET.splitlines()]
        total = len(lines)
        visible_rows = max(1, height - 1)
        start = max(0, min(self.help_scroll, max(0, total - visible_rows)))
        visible = lines[start : start + visible_rows]
        self.stdscr.erase()
        for row, line in enumerate(visible):
            self._render_line(row, line[:width])
        self._render_line(height - 1, f"HELP {start + 1}-{start + len(visible)}/{total}", curses.A_BOLD)

    def render(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        if self.show_help:
            self._render_help()
            self.stdscr.refresh()
            return
        list_start_y = 2
        list_height = max(0, height - list_start_y - 1)
        items = self._display_items()
        self.current.scroll = _clamp_scroll(
            selected=self.current.selected,
            scroll=self.current.scroll,
            height=list_height,
            total=len(items),
        )
        self._render_line(0, self.current.path[: max(0, width - 1)])
        visible = items[self.current.scroll : self.current.scroll + list_height]
        if not visible:
            empty_msg = "(empty folder)"
            y = list_start_y + (list_height // 2 if list_height else 0)
            self._render_line(y, empty_msg[: max(0, width - 1)], curses.A_DIM)
        visual_indices = set(self._visual_indices(len(items)))
        for offset, item in enumerate(visible):
            absolute_index = self.current.scroll + offset
            entry = item.entry
            marker = ">" if absolute_index == self.current.selected else " "
            mark = "✓" if entry.id in self.marked_ids else " "
            if entry.is_dir:
                prefix = "▾ " if entry.id in self.expanded_ids else "▸ "
            else:
                prefix = "  "
            indent = "  " * item.depth
            row = f"{indent}{marker}{mark} {prefix}{_display_name(entry)}"
            attr = curses.A_BOLD if absolute_index == self.current.selected else curses.A_NORMAL
            if absolute_index in visual_indices:
                attr |= curses.A_REVERSE
            self._render_line(list_start_y + offset, row[: max(0, width - 1)], attr)
        scroll_indicator = ""
        if len(items) > list_height and list_height > 0:
            top = self.current.scroll + 1
            bottom = min(len(items), self.current.scroll + list_height)
            scroll_indicator = f"[{top}-{bottom}/{len(items)}]"
        self._render_line(height - 1, self._compose_status(scroll_indicator=scroll_indicator)[: max(0, width - 1)])
        self.stdscr.refresh()

    def run(self) -> NavigatorResult:
        while True:
            self._poll_background_jobs()
            self.render()
            key = self.stdscr.getch()
            if key == -1:
                continue
            if self.show_help:
                lines = len(CHEATSHEET.splitlines())
                visible_rows = max(1, self.stdscr.getmaxyx()[0] - 1)
                max_scroll = max(0, lines - visible_rows)
                if key == ord("?"):
                    self.show_help = False
                    self.help_scroll = 0
                    continue
                if key in (ord("j"), curses.KEY_DOWN):
                    self.help_scroll = min(max_scroll, self.help_scroll + 1)
                    continue
                if key in (ord("k"), curses.KEY_UP):
                    self.help_scroll = max(0, self.help_scroll - 1)
                    continue
                if key in (ord("q"), 27):
                    self.show_help = False
                    self.help_scroll = 0
                    continue
                continue
            if self._handle_normal_key(key):
                return self.result


def _curses_main(
    stdscr,
    client: DriveClient,
    preset: str,
    download_dir: Path,
    handlers: dict[str, HandlerSpec],
    upload_paths: list[Path] | None = None,
) -> NavigatorResult:
    curses.curs_set(0)
    try:
        curses.noecho()
        curses.raw()
        curses.nonl()
    except curses.error:
        pass
    try:
        curses.start_color()
        curses.use_default_colors()
    except Exception:
        pass
    stdscr.keypad(True)
    try:
        stdscr.leaveok(True)
        stdscr.idlok(True)
    except Exception:
        pass
    stdscr.timeout(80)
    navigator = DriveNavigator(
        stdscr=stdscr,
        client=client,
        preset=preset,
        download_dir=download_dir,
        handlers=handlers,
        upload_paths=upload_paths,
    )
    return navigator.run()


def browse_drive(client: DriveClient, preset: str, download_dir: Path, handlers: dict[str, HandlerSpec]) -> int:
    result_holder: dict[str, NavigatorResult] = {}

    def _runner(stdscr):
        result_holder["result"] = _curses_main(stdscr, client, preset, download_dir, handlers)
        return result_holder["result"].exit_code

    curses.wrapper(_runner)
    return result_holder.get("result", NavigatorResult()).exit_code


def upload_with_picker(
    client: DriveClient,
    preset: str,
    download_dir: Path,
    handlers: dict[str, HandlerSpec],
    upload_paths: list[Path],
) -> NavigatorResult:
    result_holder: dict[str, NavigatorResult] = {}

    def _runner(stdscr):
        result_holder["result"] = _curses_main(
            stdscr,
            client,
            preset,
            download_dir,
            handlers,
            upload_paths=upload_paths,
        )
        return result_holder["result"].exit_code

    curses.wrapper(_runner)
    return result_holder.get("result", NavigatorResult(cancelled=True))
