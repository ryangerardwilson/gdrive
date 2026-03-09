from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import AccountConfig, normalize_account_email
from .errors import ApiError
from .paths import ensure_dirs, token_file_for_email

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _write_token(token_path: Path, creds: Credentials) -> None:
    token_path.write_text(creds.to_json(), encoding="utf-8")


def authorize_account(client_secret_file: Path) -> tuple[Credentials, str]:
    ensure_dirs()
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as exc:
        raise ApiError(f"oauth authorization failed: {exc}") from exc
    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = service.about().get(fields="user(emailAddress)").execute()
        email = normalize_account_email(str(about.get("user", {}).get("emailAddress", "")))
    except Exception as exc:
        raise ApiError(f"drive profile lookup failed after oauth: {exc}") from exc
    if not email:
        raise ApiError("drive profile lookup returned no email address")
    _write_token(token_file_for_email(email), creds)
    return creds, email

def load_credentials(account: AccountConfig) -> Credentials:
    ensure_dirs()
    if not account.email:
        raise ApiError(
            f"preset {account.preset} is missing email; re-run `gdrive auth <client_secret_path>`"
        )
    token_path = token_file_for_email(account.email)
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
        _write_token(token_path, creds)
        return creds
    creds, _ = authorize_account(account.client_secret_file)
    return creds
