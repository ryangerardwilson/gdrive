from __future__ import annotations

import os
from pathlib import Path


def config_home() -> Path:
    override = os.environ.get("XDG_CONFIG_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".config"
    return base / "gdrive"


def data_home() -> Path:
    override = os.environ.get("XDG_DATA_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".local" / "share"
    return base / "gdrive"


def config_file() -> Path:
    return config_home() / "config.json"


def legacy_token_file() -> Path:
    return data_home() / "token.json"


def token_dir() -> Path:
    return data_home() / "tokens"


def token_file_for_email(email: str) -> Path:
    return token_dir() / f"{email.strip().lower()}.json"


def state_dir() -> Path:
    return data_home() / "state"


def ensure_dirs() -> None:
    config_home().mkdir(parents=True, exist_ok=True, mode=0o700)
    data_home().mkdir(parents=True, exist_ok=True, mode=0o700)
    token_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
