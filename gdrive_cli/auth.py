from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import AccountConfig, generate_account_key
from .errors import ApiError
from .paths import ensure_dirs, legacy_token_file, token_file_for_account_key, token_file_for_preset

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _write_token(token_path: Path, creds: Credentials) -> None:
    token_path.write_text(creds.to_json(), encoding="utf-8")


def authorize_account(client_secret_file: Path) -> tuple[Credentials, str, str]:
    ensure_dirs()
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as exc:
        raise ApiError(f"oauth authorization failed: {exc}") from exc
    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = service.about().get(fields="user(emailAddress)").execute()
        email = str(about.get("user", {}).get("emailAddress", "")).strip().lower()
    except Exception as exc:
        raise ApiError(f"drive profile lookup failed after oauth: {exc}") from exc
    if not email:
        raise ApiError("drive profile lookup returned no email address")
    account_key = generate_account_key(client_secret_file, email)
    _write_token(token_file_for_account_key(account_key), creds)
    return creds, email, account_key


def load_credentials(account: AccountConfig) -> Credentials:
    ensure_dirs()
    token_path = token_file_for_account_key(account.account_key) if account.account_key else token_file_for_preset(account.preset)
    preset_token_path = token_file_for_preset(account.preset)
    legacy_path = legacy_token_file()
    if account.account_key and not token_path.exists():
        if preset_token_path.exists():
            _write_token(token_path, Credentials.from_authorized_user_file(str(preset_token_path), SCOPES))
        elif account.preset == "1" and legacy_path.exists():
            token_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
    elif not account.account_key and account.preset == "1" and not token_path.exists() and legacy_path.exists():
        token_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
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
    creds, _, _ = authorize_account(account.client_secret_file)
    return creds
