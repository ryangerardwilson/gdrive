import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gdrive_cli.drive_api import DriveClient, UPLOAD_CHUNK_SIZE, UPLOAD_TIMEOUT_RETRIES
from gdrive_cli.errors import ApiError


class FakeUploadRequest:
    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0
        self.num_retries_values: list[int] = []

    def next_chunk(self, num_retries: int = 0):
        self.calls += 1
        self.num_retries_values.append(num_retries)
        if self.calls <= self.failures:
            raise TimeoutError("write timed out")
        return None, {"id": "drive-file-id"}


class FakeFilesResource:
    def __init__(self, request):
        self.request = request
        self.create_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.request


class FakeService:
    def __init__(self, request):
        self.files_resource = FakeFilesResource(request)

    def files(self):
        return self.files_resource


class DriveApiTests(unittest.TestCase):
    def test_resumable_upload_retries_write_timeouts(self):
        client = object.__new__(DriveClient)
        request = FakeUploadRequest(failures=2)
        with patch("gdrive_cli.drive_api.time.sleep") as sleep:
            result = client._execute_resumable_upload(request)

        self.assertEqual(result, {"id": "drive-file-id"})
        self.assertEqual(request.calls, 3)
        self.assertEqual(request.num_retries_values, [3, 3, 3])
        self.assertEqual(sleep.call_count, 2)

    def test_resumable_upload_stops_after_timeout_retries(self):
        client = object.__new__(DriveClient)
        request = FakeUploadRequest(failures=UPLOAD_TIMEOUT_RETRIES + 1)
        with patch("gdrive_cli.drive_api.time.sleep"):
            with self.assertRaises(ApiError):
                client._execute_resumable_upload(request)

        self.assertEqual(request.calls, UPLOAD_TIMEOUT_RETRIES + 1)

    def test_upload_file_uses_resumable_media_upload(self):
        client = object.__new__(DriveClient)
        request = FakeUploadRequest(failures=0)
        client.service = FakeService(request)
        with TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "large.mp3"
            file_path.write_text("x", encoding="utf-8")
            with patch("gdrive_cli.drive_api.MediaFileUpload") as media:
                drive_id = client.upload_file("parent-id", "large.mp3", str(file_path))

        self.assertEqual(drive_id, "drive-file-id")
        media.assert_called_once_with(str(file_path), chunksize=UPLOAD_CHUNK_SIZE, resumable=True)
        self.assertEqual(client.service.files_resource.create_kwargs["fields"], "id")
        self.assertEqual(client.service.files_resource.create_kwargs["supportsAllDrives"], False)

