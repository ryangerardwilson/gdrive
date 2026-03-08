from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
class AppConfig:
    client_secret_file: str | None = None
    backup_root_name: str | None = None
    registrations: list[Registration] = field(default_factory=list)


def load_config() -> AppConfig:
    ensure_dirs()
    path = config_file()
    if not path.exists():
        return AppConfig()
    data = json.loads(path.read_text())
    regs = [Registration(**item) for item in data.get("registrations", [])]
    return AppConfig(
        client_secret_file=data.get("client_secret_file"),
        backup_root_name=normalize_drive_path(data["backup_root_name"]) if data.get("backup_root_name") else None,
        registrations=regs,
    )


def save_config(config: AppConfig) -> None:
    ensure_dirs()
    path = config_file()
    payload = {
        "client_secret_file": config.client_secret_file,
        "backup_root_name": config.backup_root_name,
        "registrations": [asdict(item) for item in config.registrations],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def require_client_secret(config: AppConfig) -> Path:
    if not config.client_secret_file:
        raise CliError("missing client secret in config")
    path = Path(config.client_secret_file).expanduser().resolve()
    if not path.exists():
        raise CliError(f"missing client secret file: {path}")
    return path


def set_client_secret(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise CliError(f"missing client secret file: {path}")
    config = load_config()
    config.client_secret_file = str(path)
    save_config(config)
    return path


def next_registration_id(config: AppConfig) -> str:
    if not config.registrations:
        return "1"
    return str(max(int(item.id) for item in config.registrations) + 1)


def normalize_drive_path(value: str) -> str:
    parts = [segment.strip() for segment in value.split("/") if segment.strip()]
    if not parts:
        raise CliError("invalid drive path: use `Folder/Subfolder`")
    return "/".join(parts)


def add_registration(local_dir: str, drive_path: str) -> Registration:
    local_path = Path(local_dir).expanduser().resolve()
    if not local_path.exists() or not local_path.is_dir():
        raise CliError(f"missing local dir: {local_path}")
    config = load_config()
    backup_root_name = require_backup_root_name(config)
    normalized_drive_path = normalize_relative_drive_path(drive_path, backup_root_name)
    for reg in config.registrations:
        if Path(reg.local_dir) == local_path:
            raise CliError(f"already registered: {reg.id}")
    reg = Registration(
        id=next_registration_id(config),
        local_dir=str(local_path),
        drive_path=normalized_drive_path,
    )
    config.registrations.append(reg)
    save_config(config)
    return reg


def require_backup_root_name(config: AppConfig) -> str:
    if not config.backup_root_name:
        raise CliError("missing backup root: run any interactive command and set it")
    return config.backup_root_name


def set_backup_root_name(value: str) -> str:
    normalized = normalize_drive_path(value)
    config = load_config()
    config.backup_root_name = normalized
    save_config(config)
    return normalized


def normalize_relative_drive_path(value: str, backup_root_name: str) -> str:
    normalized = normalize_drive_path(value)
    if normalized == backup_root_name or normalized.startswith(f"{backup_root_name}/"):
        raise CliError("drive path must be relative to the backup root, not include it")
    return normalized


def list_registrations() -> list[Registration]:
    return load_config().registrations


def get_registration(reg_id: str) -> Registration:
    config = load_config()
    for reg in config.registrations:
        if reg.id == reg_id:
            return reg
    raise CliError(f"unknown registration: {reg_id}")


def update_registration(updated: Registration) -> None:
    config = load_config()
    replaced = False
    for index, reg in enumerate(config.registrations):
        if reg.id == updated.id:
            config.registrations[index] = updated
            replaced = True
            break
    if not replaced:
        raise CliError(f"unknown registration: {updated.id}")
    save_config(config)


def remove_registration(reg_id: str) -> Registration:
    config = load_config()
    for index, reg in enumerate(config.registrations):
        if reg.id == reg_id:
            config.registrations.pop(index)
            save_config(config)
            return reg
    raise CliError(f"unknown registration: {reg_id}")
