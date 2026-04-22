from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from googleapiclient.discovery import build

from .config import AccountConfig, normalize_account_email
from .errors import ApiError
from .paths import ensure_dirs, token_file_for_email

SCOPES = ["https://www.googleapis.com/auth/drive"]
LOCAL_SERVER_TIMEOUT_SECONDS = 60
AUTH_PROMPT_MESSAGE = """Please visit this URL to authorize this application:
{url}

If the final browser page says localhost refused to connect, keep this terminal
open. When prompted, paste the full localhost URL from the browser address bar.
"""


def _write_token(token_path: Path, creds: Credentials) -> None:
    token_path.write_text(creds.to_json(), encoding="utf-8")


def _clean_callback_value(value: str) -> str:
    return value.strip().strip("'\"`").rstrip(";")


def _authorization_response_from_input(value: str) -> tuple[str, str]:
    cleaned = _clean_callback_value(value)
    if not cleaned:
        raise ApiError("oauth callback was empty")
    if cleaned.startswith(("http://", "https://")):
        parts = urlsplit(cleaned)
        query = parse_qs(parts.query)
        if not query.get("code"):
            raise ApiError("oauth callback URL did not contain a code")
        if not query.get("state"):
            raise ApiError("oauth callback URL did not contain a state")
        normalized_query = urlencode(query, doseq=True)
        normalized = urlunsplit(("https", parts.netloc, parts.path, normalized_query, ""))
        return "authorization_response", normalized
    return "code", cleaned


def _prompt_for_callback(flow: InstalledAppFlow) -> Credentials:
    if not sys.stdin.isatty():
        raise ApiError(
            "oauth browser callback timed out and no terminal is available to paste the callback URL"
        )
    callback = input(
        "Paste the full localhost callback URL from the browser address bar, or just the code value: "
    )
    mode, value = _authorization_response_from_input(callback)
    try:
        if mode == "authorization_response":
            flow.fetch_token(authorization_response=value)
        else:
            flow.fetch_token(code=value)
    except Exception as exc:
        raise ApiError(f"oauth token exchange failed from pasted callback: {exc}") from exc
    return flow.credentials


def _complete_oauth(flow: InstalledAppFlow) -> Credentials:
    try:
        return flow.run_local_server(
            port=0,
            timeout_seconds=LOCAL_SERVER_TIMEOUT_SECONDS,
            authorization_prompt_message=AUTH_PROMPT_MESSAGE,
        )
    except WSGITimeoutError:
        print(
            "No browser callback was received. If your browser showed "
            "`localhost refused to connect`, paste that URL below.",
            file=sys.stderr,
        )
        return _prompt_for_callback(flow)


def authorize_account(client_secret_file: Path) -> tuple[Credentials, str]:
    ensure_dirs()
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
        creds = _complete_oauth(flow)
    except ApiError:
        raise
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
