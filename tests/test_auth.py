import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from google_auth_oauthlib.flow import WSGITimeoutError

from gdrive_cli.auth import (
    LOCAL_SERVER_TIMEOUT_SECONDS,
    _authorization_response_from_input,
    authorize_account,
)


class FakeCredentials:
    def to_json(self):
        return "{}"


class FakeFlow:
    def __init__(self):
        self.credentials = FakeCredentials()
        self.local_server_kwargs = None
        self.fetch_token_kwargs = None

    def run_local_server(self, **kwargs):
        self.local_server_kwargs = kwargs
        raise WSGITimeoutError("timed out")

    def fetch_token(self, **kwargs):
        self.fetch_token_kwargs = kwargs


class AuthTests(unittest.TestCase):
    def test_callback_url_is_sanitized_for_token_exchange(self):
        mode, value = _authorization_response_from_input(
            " http://localhost:59769/?state=abc&code=def&scope=https://www.googleapis.com/auth/drive; "
        )

        self.assertEqual(mode, "authorization_response")
        self.assertEqual(
            value,
            "https://localhost:59769/?state=abc&code=def&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive",
        )

    def test_raw_code_can_be_pasted(self):
        self.assertEqual(_authorization_response_from_input(" 4/abc123; "), ("code", "4/abc123"))

    def test_authorize_account_accepts_pasted_callback_after_timeout(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            token_path = tmp_path / "token.json"
            client_secret = tmp_path / "client.json"
            client_secret.write_text("{}", encoding="utf-8")
            fake_flow = FakeFlow()
            service = SimpleNamespace(
                about=lambda: SimpleNamespace(
                    get=lambda fields: SimpleNamespace(
                        execute=lambda: {"user": {"emailAddress": "Info@WilsonFamilyOffice.in"}}
                    )
                )
            )

            with patch(
                "gdrive_cli.auth.InstalledAppFlow.from_client_secrets_file",
                return_value=fake_flow,
            ), patch("gdrive_cli.auth.build", return_value=service), patch(
                "gdrive_cli.auth.token_file_for_email", return_value=token_path
            ), patch(
                "gdrive_cli.auth.ensure_dirs"
            ), patch(
                "sys.stdin.isatty", return_value=True
            ), patch(
                "builtins.input",
                return_value="http://localhost:59769/?state=abc&code=def&scope=drive;",
            ):
                creds, email = authorize_account(client_secret)
                token_json = token_path.read_text(encoding="utf-8")

            self.assertIs(creds, fake_flow.credentials)
            self.assertEqual(email, "info@wilsonfamilyoffice.in")
            self.assertEqual(fake_flow.local_server_kwargs["port"], 0)
            self.assertEqual(
                fake_flow.local_server_kwargs["timeout_seconds"], LOCAL_SERVER_TIMEOUT_SECONDS
            )
            self.assertEqual(
                fake_flow.fetch_token_kwargs,
                {
                    "authorization_response": "https://localhost:59769/?state=abc&code=def&scope=drive"
                },
            )
            self.assertEqual(token_json, "{}")
