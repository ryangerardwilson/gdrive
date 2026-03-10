from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from .errors import ApiError

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


@dataclass(slots=True)
class RemoteEntry:
    id: str
    relpath: str
    name: str
    parent_id: str
    mime_type: str

    @property
    def is_dir(self) -> bool:
        return self.mime_type == FOLDER_MIME


@dataclass(slots=True)
class NavEntry:
    id: str
    name: str
    mime_type: str
    parent_id: str

    @property
    def is_dir(self) -> bool:
        return self.mime_type == FOLDER_MIME


def escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    def __init__(self, creds):
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _execute(self, request):
        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            body = ""
            if hasattr(exc, "content") and exc.content:
                try:
                    body = exc.content.decode("utf-8", errors="replace")
                except Exception:
                    body = str(exc)
            text = body or str(exc)
            if status == 403 and "accessNotConfigured" in text:
                raise ApiError(
                    "google drive api is disabled for this oauth project. "
                    "enable Drive API in Google Cloud Console for this client id, wait a few minutes, then retry."
                ) from exc
            raise ApiError(f"google drive api error ({status}): {text}") from exc

    def find_child(self, parent_id: str, name: str, mime_type: str | None = None) -> dict | None:
        query = [f"'{parent_id}' in parents", f"name = '{escape_query(name)}'", "trashed = false"]
        if mime_type:
            query.append(f"mimeType = '{mime_type}'")
        response = self._execute(self.service.files().list(
            q=" and ".join(query),
            fields="files(id,name,mimeType,parents)",
            pageSize=10,
            supportsAllDrives=False,
        ))
        files = response.get("files", [])
        return files[0] if files else None

    def create_folder(self, parent_id: str, name: str) -> str:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        result = self._execute(
            self.service.files().create(body=body, fields="id", supportsAllDrives=False)
        )
        return result["id"]

    def ensure_drive_path(self, drive_path: str) -> str:
        parent_id = "root"
        for segment in drive_path.split("/"):
            existing = self.find_child(parent_id, segment, FOLDER_MIME)
            parent_id = existing["id"] if existing else self.create_folder(parent_id, segment)
        return parent_id

    def list_children(self, parent_id: str) -> list[NavEntry]:
        entries: list[NavEntry] = []
        page_token = None
        while True:
            response = self._execute(self.service.files().list(
                q=f"'{parent_id}' in parents and trashed = false",
                fields="nextPageToken,files(id,name,mimeType,parents)",
                pageSize=1000,
                pageToken=page_token,
                orderBy="folder,name_natural",
                supportsAllDrives=False,
            ))
            for item in response.get("files", []):
                entries.append(
                    NavEntry(
                        id=item["id"],
                        name=item["name"],
                        mime_type=item["mimeType"],
                        parent_id=parent_id,
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        entries.sort(key=lambda entry: (not entry.is_dir, entry.name.lower(), entry.name))
        return entries

    def get_entry(self, file_id: str) -> NavEntry:
        item = self._execute(
            self.service.files().get(
                fileId=file_id,
                fields="id,name,mimeType,parents",
                supportsAllDrives=False,
            )
        )
        parents = item.get("parents", [])
        return NavEntry(
            id=item["id"],
            name=item["name"],
            mime_type=item["mimeType"],
            parent_id=parents[0] if parents else "",
        )

    def copy_file(self, file_id: str, parent_id: str, new_name: str) -> str:
        result = self._execute(
            self.service.files().copy(
                fileId=file_id,
                body={"name": new_name, "parents": [parent_id]},
                fields="id",
                supportsAllDrives=False,
            )
        )
        return result["id"]

    def find_available_name(self, parent_id: str, name: str) -> str:
        if not self.find_child(parent_id, name):
            return name
        stem, dot, suffix = name.rpartition(".")
        base = stem if dot else name
        ext = f".{suffix}" if dot else ""
        for index in range(1, 10_000):
            candidate = f"{base}-{index}{ext}"
            if not self.find_child(parent_id, candidate):
                return candidate
        raise ApiError(f"could not allocate name for {name}")

    def _download_request_for_entry(self, entry: NavEntry):
        export = EXPORT_MIME_TYPES.get(entry.mime_type)
        if export is not None:
            export_mime, _ = export
            return self.service.files().export_media(fileId=entry.id, mimeType=export_mime)
        if entry.mime_type.startswith("application/vnd.google-apps."):
            raise ApiError(f"download not supported for Google file type `{entry.mime_type}`")
        return self.service.files().get_media(fileId=entry.id, supportsAllDrives=False)

    def _download_target_path(self, entry: NavEntry, target_path: Path) -> Path:
        export = EXPORT_MIME_TYPES.get(entry.mime_type)
        if export is None:
            return target_path
        _, suffix = export
        if target_path.suffix.lower() == suffix.lower():
            return target_path
        if target_path.suffix:
            return target_path.with_name(f"{target_path.stem}{suffix}")
        return target_path.with_name(f"{target_path.name}{suffix}")

    def download_entry(self, entry: NavEntry, target_path: Path) -> Path:
        request = self._download_request_for_entry(entry)
        resolved_target = self._download_target_path(entry, target_path)
        resolved_target.parent.mkdir(parents=True, exist_ok=True)
        with resolved_target.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return resolved_target

    def list_tree(self, root_id: str) -> dict[str, RemoteEntry]:
        result: dict[str, RemoteEntry] = {}
        queue: list[tuple[str, str]] = [("", root_id)]
        while queue:
            relbase, parent_id = queue.pop(0)
            page_token = None
            while True:
                response = self._execute(self.service.files().list(
                    q=f"'{parent_id}' in parents and trashed = false",
                    fields="nextPageToken,files(id,name,mimeType,parents)",
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=False,
                ))
                for item in response.get("files", []):
                    relpath = f"{relbase}/{item['name']}".strip("/")
                    entry = RemoteEntry(
                        id=item["id"],
                        relpath=relpath,
                        name=item["name"],
                        parent_id=parent_id,
                        mime_type=item["mimeType"],
                    )
                    result[relpath] = entry
                    if entry.is_dir:
                        queue.append((relpath, entry.id))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        return result

    def upload_file(self, parent_id: str, name: str, file_path: str) -> str:
        media = MediaFileUpload(file_path, resumable=False)
        body = {"name": name, "parents": [parent_id]}
        result = self._execute(self.service.files().create(
            body=body,
            media_body=media,
            fields="id",
            supportsAllDrives=False,
        ))
        return result["id"]

    def update_file(self, file_id: str, file_path: str) -> None:
        media = MediaFileUpload(file_path, resumable=False)
        self._execute(self.service.files().update(
            fileId=file_id,
            media_body=media,
            fields="id",
            supportsAllDrives=False,
        ))

    def move_entry(self, file_id: str, new_parent_id: str, new_name: str, old_parent_id: str) -> None:
        self._execute(self.service.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=old_parent_id,
            body={"name": new_name},
            fields="id,parents",
            supportsAllDrives=False,
        ))

    def rename_entry(self, file_id: str, new_name: str) -> None:
        self._execute(self.service.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id",
            supportsAllDrives=False,
        ))

    def delete_entry(self, file_id: str) -> None:
        self._execute(self.service.files().delete(fileId=file_id, supportsAllDrives=False))
