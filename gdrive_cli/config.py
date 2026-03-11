from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import CliError
from .paths import config_file, ensure_dirs


@dataclass(slots=True)
class Registration:
    id: str
    local_dir: str
    drive_path: str
    remote_root_id: str | None = None
    enabled: bool = True


@dataclass(slots=True)
class AccountConfig:
    preset: str
    client_secret_file: Path | None = None
    email: str | None = None
    backup_root_name: str | None = None
    registrations: list[Registration] = field(default_factory=list)


@dataclass(slots=True)
class HandlerSpec:
    commands: list[list[str]] = field(default_factory=list)
    is_internal: bool = False


@dataclass(slots=True)
class AppConfig:
    path: Path
    accounts: dict[str, AccountConfig]
    handlers: dict[str, HandlerSpec] = field(default_factory=dict)


def _sorted_accounts(accounts: dict[str, AccountConfig]) -> dict[str, AccountConfig]:
    return dict(sorted(accounts.items(), key=lambda item: (int(item[0]) if item[0].isdigit() else item[0])))


def _normalize_command(entry: Any) -> list[str]:
    if isinstance(entry, str):
        return shlex.split(entry) if entry.strip() else []
    if isinstance(entry, list) and all(isinstance(token, str) for token in entry):
        return [token for token in entry if token]
    return []


def _normalize_handler_commands(raw_value: Any) -> list[list[str]]:
    commands: list[list[str]] = []
    if isinstance(raw_value, list):
        if raw_value and all(isinstance(entry, str) for entry in raw_value):
            command = _normalize_command(raw_value)
            if command:
                commands.append(command)
        else:
            for entry in raw_value:
                command = _normalize_command(entry)
                if command:
                    commands.append(command)
    else:
        command = _normalize_command(raw_value)
        if command:
            commands.append(command)
    return commands


def _normalize_handlers(raw_handlers: Any) -> dict[str, HandlerSpec]:
    handlers: dict[str, HandlerSpec] = {}
    if not isinstance(raw_handlers, dict):
        return handlers
    for raw_key, raw_value in raw_handlers.items():
        key = raw_key.strip() if isinstance(raw_key, str) else ""
        if not key:
            continue
        commands: list[list[str]] = []
        is_internal = False
        if isinstance(raw_value, dict):
            commands_value = raw_value.get("commands")
            if commands_value is None and "command" in raw_value:
                commands_value = raw_value.get("command")
            commands = _normalize_handler_commands(commands_value)
            is_internal = bool(raw_value.get("is_internal"))
        else:
            commands = _normalize_handler_commands(raw_value)
        if not commands:
            continue
        handlers[key] = HandlerSpec(commands=commands, is_internal=is_internal)
    return handlers


def resolve_config_path() -> Path:
    override = os.environ.get("GDRIVE_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_file()


def _normalize_preset(preset: str) -> str:
    value = str(preset).strip()
    if not value or not value.isdigit():
        raise CliError("preset must be numeric, like `1` or `2`")
    return value


def normalize_drive_path(value: str) -> str:
    parts = [segment.strip() for segment in str(value).strip().replace("\\", "/").split("/") if segment.strip()]
    if not parts:
        raise CliError("path cannot be empty")
    return "/".join(parts)


def normalize_relative_drive_path(value: str, backup_root_name: str) -> str:
    drive_path = normalize_drive_path(value)
    root = normalize_drive_path(backup_root_name)
    if drive_path == root or drive_path.startswith(f"{root}/"):
        raise CliError(f"drive path must be relative to backup root `{root}`")
    return drive_path


def normalize_account_email(email: str) -> str:
    return email.strip().lower()


def _registration_from_raw(raw: Any) -> Registration | None:
    if not isinstance(raw, dict):
        return None
    reg_id = str(raw.get("id", "")).strip()
    local_dir = str(raw.get("local_dir", "")).strip()
    drive_path = str(raw.get("drive_path", "")).strip()
    if not reg_id or not local_dir or not drive_path:
        return None
    return Registration(
        id=reg_id,
        local_dir=str(Path(local_dir).expanduser().resolve()),
        drive_path=normalize_drive_path(drive_path),
        remote_root_id=str(raw.get("remote_root_id")).strip() or None if raw.get("remote_root_id") is not None else None,
        enabled=bool(raw.get("enabled", True)),
    )


def _account_from_raw(preset: str, raw: Any) -> AccountConfig:
    if raw is None:
        return AccountConfig(preset=preset)
    if not isinstance(raw, dict):
        raise CliError(f"invalid config: accounts['{preset}'] must be an object")
    client_secret_raw = raw.get("client_secret_file")
    email_raw = raw.get("email")
    backup_root_raw = raw.get("backup_root_name")
    registrations_raw = raw.get("registrations", [])
    client_secret = None
    if isinstance(client_secret_raw, str) and client_secret_raw.strip():
        client_secret = Path(client_secret_raw).expanduser()
    backup_root_name = None
    if isinstance(backup_root_raw, str) and backup_root_raw.strip():
        backup_root_name = normalize_drive_path(backup_root_raw)
    email = normalize_account_email(str(email_raw)) if email_raw is not None and str(email_raw).strip() else None
    registrations: list[Registration] = []
    if registrations_raw is None:
        registrations_raw = []
    if not isinstance(registrations_raw, list):
        raise CliError(f"invalid config: accounts['{preset}'].registrations must be a list")
    for item in registrations_raw:
        registration = _registration_from_raw(item)
        if registration:
            registrations.append(registration)
    return AccountConfig(
        preset=preset,
        client_secret_file=client_secret,
        email=email,
        backup_root_name=backup_root_name,
        registrations=sorted(registrations, key=lambda reg: int(reg.id) if reg.id.isdigit() else reg.id),
    )


def _migrate_legacy_root(raw: dict[str, Any]) -> dict[str, Any]:
    if "accounts" in raw and isinstance(raw.get("accounts"), dict):
        return raw
    return {
        "handlers": raw.get("handlers", {}),
        "accounts": {
            "1": {
                "client_secret_file": raw.get("client_secret_file"),
                "backup_root_name": raw.get("backup_root_name"),
                "registrations": raw.get("registrations", []),
            }
        }
    }


def load_config(path: Path | None = None) -> AppConfig:
    config_path = (path or resolve_config_path()).expanduser()
    ensure_dirs()
    if not config_path.exists():
        save_config(AppConfig(path=config_path, accounts={}))
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"invalid JSON in config {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CliError(f"invalid config at {config_path}: root must be an object")
    raw = _migrate_legacy_root(raw)
    accounts_raw = raw.get("accounts", {})
    if not isinstance(accounts_raw, dict):
        raise CliError(f"invalid config at {config_path}: 'accounts' must be an object")
    accounts: dict[str, AccountConfig] = {}
    for preset, account_raw in accounts_raw.items():
        preset_key = _normalize_preset(str(preset))
        accounts[preset_key] = _account_from_raw(preset_key, account_raw)
    return AppConfig(
        path=config_path,
        accounts=_sorted_accounts(accounts),
        handlers=_normalize_handlers(raw.get("handlers", {})),
    )


def _serialize_config(config: AppConfig) -> dict[str, Any]:
    accounts_payload: dict[str, Any] = {}
    for preset, account in _sorted_accounts(config.accounts).items():
        accounts_payload[preset] = {
            "client_secret_file": str(account.client_secret_file.expanduser()) if account.client_secret_file else "",
            "email": account.email or "",
            "backup_root_name": account.backup_root_name or "",
            "registrations": [
                {
                    "id": registration.id,
                    "local_dir": registration.local_dir,
                    "drive_path": registration.drive_path,
                    "remote_root_id": registration.remote_root_id,
                    "enabled": registration.enabled,
                }
                for registration in sorted(
                    account.registrations,
                    key=lambda reg: int(reg.id) if reg.id.isdigit() else reg.id,
                )
            ],
        }
    handlers_payload = {
        name: {
            "commands": spec.commands,
            "is_internal": spec.is_internal,
        }
        for name, spec in sorted(config.handlers.items())
    }
    return {"accounts": accounts_payload, "handlers": handlers_payload}


def save_config(config: AppConfig) -> None:
    config.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config.path.write_text(json.dumps(_serialize_config(config), indent=2) + "\n", encoding="utf-8")


def ensure_account(config: AppConfig, preset: str) -> AccountConfig:
    preset_key = _normalize_preset(preset)
    account = config.accounts.get(preset_key)
    if account is None:
        account = AccountConfig(preset=preset_key)
        config.accounts[preset_key] = account
        config.accounts = _sorted_accounts(config.accounts)
    return account


def get_account(config: AppConfig, preset: str) -> AccountConfig:
    preset_key = _normalize_preset(preset)
    account = config.accounts.get(preset_key)
    if account is None:
        available = ", ".join(sorted(config.accounts)) or "none"
        raise CliError(f"preset `{preset_key}` not found. available presets: {available}")
    return account


def require_client_secret(account: AccountConfig) -> Path:
    if not account.client_secret_file:
        raise CliError(f"preset `{account.preset}` is missing a client secret file")
    return account.client_secret_file


def require_backup_root_name(account: AccountConfig) -> str:
    if not account.backup_root_name:
        raise CliError(f"preset `{account.preset}` is missing a Drive backup root dir name")
    return account.backup_root_name


def set_client_secret(preset: str, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists():
        raise CliError(f"missing client secret file: {path}")
    if not path.is_file():
        raise CliError(f"client secret path is not a file: {path}")
    config = load_config()
    account = ensure_account(config, preset)
    account.client_secret_file = path.resolve()
    save_config(config)
    return account.client_secret_file


def _next_preset(accounts: dict[str, AccountConfig]) -> str:
    numeric_ids = [int(item) for item in accounts if item.isdigit()]
    return str(max(numeric_ids, default=0) + 1)


def upsert_authenticated_account(
    client_secret_file: Path,
    account_email: str,
    backup_root_name: str,
) -> AccountConfig:
    normalized_secret = client_secret_file.expanduser().resolve()
    normalized_email = normalize_account_email(account_email)
    normalized_root = normalize_drive_path(backup_root_name)
    config = load_config()
    for account in config.accounts.values():
        if account.email and normalize_account_email(account.email) == normalized_email:
            account.client_secret_file = normalized_secret
            account.email = normalized_email
            account.backup_root_name = normalized_root
            save_config(config)
            return account
    preset = _next_preset(config.accounts)
    account = AccountConfig(
        preset=preset,
        client_secret_file=normalized_secret,
        email=normalized_email,
        backup_root_name=normalized_root,
    )
    config.accounts[preset] = account
    config.accounts = _sorted_accounts(config.accounts)
    save_config(config)
    return account


def set_backup_root_name(preset: str, value: str) -> str:
    normalized = normalize_drive_path(value)
    config = load_config()
    account = ensure_account(config, preset)
    account.backup_root_name = normalized
    save_config(config)
    return normalized


def list_registrations(preset: str) -> list[Registration]:
    return list(get_account(load_config(), preset).registrations)


def _next_registration_id(registrations: list[Registration]) -> str:
    numeric_ids = [int(reg.id) for reg in registrations if reg.id.isdigit()]
    return str(max(numeric_ids, default=0) + 1)


def add_registration(preset: str, local_dir: str, drive_path: str) -> Registration:
    config = load_config()
    account = ensure_account(config, preset)
    backup_root_name = require_backup_root_name(account)
    local_path = Path(local_dir).expanduser().resolve()
    if not local_path.exists() or not local_path.is_dir():
        raise CliError(f"missing local dir: {local_path}")
    normalized_drive_path = normalize_relative_drive_path(drive_path, backup_root_name)
    for registration in account.registrations:
        if registration.local_dir == str(local_path):
            raise CliError(f"local dir already registered as id {registration.id}")
        if registration.drive_path == normalized_drive_path:
            raise CliError(f"drive path already registered as id {registration.id}")
    registration = Registration(
        id=_next_registration_id(account.registrations),
        local_dir=str(local_path),
        drive_path=normalized_drive_path,
    )
    account.registrations.append(registration)
    account.registrations.sort(key=lambda reg: int(reg.id) if reg.id.isdigit() else reg.id)
    save_config(config)
    return registration


def get_registration(preset: str, reg_id: str) -> Registration:
    for registration in get_account(load_config(), preset).registrations:
        if registration.id == reg_id:
            return registration
    raise CliError(f"registration `{reg_id}` not found in preset `{_normalize_preset(preset)}`")


def update_registration(preset: str, updated: Registration) -> None:
    config = load_config()
    account = get_account(config, preset)
    for index, registration in enumerate(account.registrations):
        if registration.id == updated.id:
            account.registrations[index] = updated
            save_config(config)
            return
    raise CliError(f"registration `{updated.id}` not found in preset `{account.preset}`")


def remove_registration(preset: str, reg_id: str) -> Registration:
    config = load_config()
    account = get_account(config, preset)
    for index, registration in enumerate(account.registrations):
        if registration.id == reg_id:
            removed = account.registrations.pop(index)
            save_config(config)
            return removed
    raise CliError(f"registration `{reg_id}` not found in preset `{account.preset}`")
