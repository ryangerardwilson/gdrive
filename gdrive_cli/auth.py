from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .errors import ApiError
from .paths import ensure_dirs, token_file

SCOPES = ["https://www.googleapis.com/auth/drive"]


def load_credentials(client_secret_file: Path) -> Credentials:
    ensure_dirs()
    token_path = token_file()
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise ApiError(f"oauth refresh failed: {exc}") from exc
        token_path.write_text(creds.to_json())
        return creds
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as exc:
        raise ApiError(f"oauth authorization failed: {exc}") from exc
    token_path.write_text(creds.to_json())
    return creds
