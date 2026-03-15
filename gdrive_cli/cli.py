from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import charset_normalizer  # noqa: F401
except Exception:  # pragma: no cover - optional during source-only edge cases
    charset_normalizer = None

from _version import __version__
from .config import (
    add_registration,
    ensure_account,
    get_account,
    get_registration,
    list_registrations,
    load_config,
    remove_registration,
    require_backup_root_name,
    require_client_secret,
    set_backup_root_name,
    set_client_secret,
    upsert_authenticated_account,
    update_registration,
)
from .errors import CliError
from .paths import ensure_dirs
from .sync import delete_state, sync_registration
from .transfer import normalize_upload_paths
from rgw_cli_contract import AppSpec, resolve_install_script_path, run_app

ANSI_RESET = "\033[0m"
ANSI_GRAY = "\033[38;5;245m"
PRESET_COMMANDS = {"reg", "ls", "rm", "nav", "up"}
GLOBAL_COMMANDS = {"run", "ti", "td", "st", "conf"}
INSTALL_SCRIPT = resolve_install_script_path(Path(__file__).resolve().parents[1] / "main.py")
HELP_TEXT = """gdrive

flags:
  gdrive -h
    show this help
  gdrive -v
    print the installed version
  gdrive -u
    upgrade to the latest release
  gdrive conf
    open the config in your editor

features:
  authorize a Google account and save or refresh its preset
  # gdrive auth <client_secret_path>
  gdrive auth ~/Documents/credentials/client_secret.json

  register folders to sync into Drive, then inspect or remove registrations
  # gdrive <preset> reg <local_dir> <drive_path> | gdrive <preset> ls | gdrive <preset> rm <edit_id>
  gdrive 1 reg ~/Documents Documents
  gdrive 1 ls
  gdrive 1 rm abcd1234

  browse Drive, upload local files, and run sync flows
  # gdrive <preset> nav | gdrive <preset> up <path...> | gdrive run
  gdrive 1 nav
  gdrive 1 up ~/Downloads/report.pdf ~/Pictures
  gdrive run
"""


def _muted_text(text: str) -> str:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        return text
    return f"{ANSI_GRAY}{text}{ANSI_RESET}"


def compact_usage() -> str:
    return "\n".join(
        [
            "usage: gdrive -v",
            "       gdrive -u",
            "       gdrive auth <client_secret_path>",
            "       gdrive <preset> reg <local_dir> <drive_path>",
            "       gdrive <preset> ls",
            "       gdrive <preset> nav",
            "       gdrive <preset> up <file_path> <file_path> ...",
            "       gdrive conf",
            "       gdrive run",
            "       gdrive <preset> rm <edit_id>",
            "       gdrive ti",
            "       gdrive td",
            "       gdrive st",
        ]
    )


def print_help_text() -> None:
    print(_muted_text(HELP_TEXT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gdrive", add_help=False)
    parser.add_argument("-h", action="store_true", dest="help")
    parser.add_argument("-v", action="store_true", dest="version")
    parser.add_argument("-u", action="store_true", dest="upgrade")
    parser.add_argument("preset", nargs="?")
    parser.add_argument("command", nargs="?")
    parser.add_argument("params", nargs=argparse.REMAINDER)
    return parser


def prompt_client_secret_file(preset: str) -> Path:
    while True:
        value = input(f"Preset {preset} Google client secret file path: ").strip()
        if not value:
            print("enter a path to a Google desktop OAuth client JSON file", file=sys.stderr)
            continue
        try:
            return set_client_secret(preset, value)
        except CliError as exc:
            print(str(exc), file=sys.stderr)


def ensure_client_secret(preset: str, interactive: bool) -> Path:
    config = load_config()
    account = ensure_account(config, preset)
    if account.client_secret_file:
        return require_client_secret(account)
    if not interactive or not sys.stdin.isatty():
        raise CliError(f"missing client secret in config for preset `{preset}`: run `gdrive {preset} ls` interactively first")
    return prompt_client_secret_file(preset)


def prompt_backup_root_name(preset: str) -> str:
    while True:
        value = input(f"Preset {preset} Drive backup root dir name: ").strip()
        if value:
            return set_backup_root_name(preset, value)
        print("enter a folder name like `Backups` or `ComputerBackups`", file=sys.stderr)


def ensure_backup_root_name(preset: str, interactive: bool) -> str:
    config = load_config()
    account = ensure_account(config, preset)
    if account.backup_root_name:
        return require_backup_root_name(account)
    if not interactive or not sys.stdin.isatty():
        raise CliError(f"missing backup root in config for preset `{preset}`: run `gdrive {preset} ls` interactively first")
    return prompt_backup_root_name(preset)


def ensure_setup(preset: str, interactive: bool) -> tuple[Path, str]:
    client_secret = ensure_client_secret(preset, interactive=interactive)
    backup_root_name = ensure_backup_root_name(preset, interactive=interactive)
    return client_secret, backup_root_name


def print_registrations(preset: str) -> int:
    account = get_account(load_config(), preset)
    root_name = require_backup_root_name(account)
    regs = account.registrations
    if not regs:
        print("no registrations")
        return 0
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    label_width = len("edit_id")
    sections: list[str] = []
    for index, reg in enumerate(regs, start=1):
        url = f"https://drive.google.com/drive/folders/{reg.remote_root_id}" if reg.remote_root_id else "-"
        prefix = f"[{index}]"
        header = prefix + ("-" * max(1, 79 - len(prefix)))
        body_lines = [
            f"{'edit_id':<{label_width}} : {reg.id}",
            f"{'local':<{label_width}}   : {reg.local_dir}",
            f"{'drive':<{label_width}}   : {root_name}/{reg.drive_path}",
            url,
        ]
        if use_color:
            body_lines = [f"{ANSI_GRAY}{line}{ANSI_RESET}" for line in body_lines]
        sections.append("\n".join([header, *body_lines]))
    print("\n".join(sections))
    return 0


def drive_client(preset: str):
    from .auth import load_credentials
    from .drive_api import DriveClient

    account = get_account(load_config(), preset)
    require_client_secret(account)
    creds = load_credentials(account)
    return DriveClient(creds)


def run_nav(preset: str) -> int:
    ensure_client_secret(preset, interactive=True)
    from .nav import browse_drive

    config = load_config()
    client = drive_client(preset)
    return browse_drive(client=client, preset=preset, download_dir=Path.cwd(), handlers=config.handlers)


def run_upload_picker(preset: str, values: list[str]) -> int:
    ensure_client_secret(preset, interactive=True)
    from .nav import upload_with_picker

    upload_paths = normalize_upload_paths(values)
    config = load_config()
    client = drive_client(preset)
    result = upload_with_picker(
        client=client,
        preset=preset,
        download_dir=Path.cwd(),
        handlers=config.handlers,
        upload_paths=upload_paths,
    )
    if result.upload_summary is None:
        print("cancelled")
        return 0
    print(
        f"uploaded\tfiles={result.upload_summary.files_uploaded}\tdirs={result.upload_summary.dirs_created}\ttarget={result.upload_target_path}"
    )
    return 0


def auth_account(client_secret_path: str) -> int:
    from .auth import authorize_account

    client_secret = Path(client_secret_path).expanduser()
    if not client_secret.exists() or not client_secret.is_file():
        raise CliError(f"missing client secret file: {client_secret}")
    backup_root_name = ""
    while not backup_root_name:
        backup_root_name = input("Drive backup root dir name: ").strip()
        if not backup_root_name:
            print("enter a folder name like `Backups` or `ComputerBackups`", file=sys.stderr)
    _, email = authorize_account(client_secret)
    account = upsert_authenticated_account(client_secret, email, backup_root_name)
    print(f"authorized\t{account.preset}\t{email}\t{account.backup_root_name}")
    return 0


def open_config_in_editor() -> int:
    config = load_config()
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
    result = subprocess.run([editor, str(config.path)], check=False)
    return result.returncode


def unit_name() -> str:
    return "gdrive"


def _build_runtime_command(*args: str) -> str:
    command_parts = [shlex.quote(str(Path(sys.executable).resolve()))]
    if not getattr(sys, "frozen", False):
        entrypoint = Path(__file__).resolve().parents[1] / "main.py"
        command_parts.append(shlex.quote(str(entrypoint)))
    command_parts.extend(shlex.quote(arg) for arg in args)
    return " ".join(command_parts)


def write_timer_units() -> None:
    ensure_dirs()
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path = systemd_dir / f"{unit_name()}.service"
    timer_path = systemd_dir / f"{unit_name()}.timer"
    entrypoint = Path(__file__).resolve().parents[1] / "main.py"
    run_command = _build_runtime_command("run")
    notify_command = "notify-send 'gdrive' 'Hourly backup finished successfully'"
    service_body = "\n".join(
        [
            "[Unit]",
            "Description=gdrive sync all presets",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={entrypoint.parent}",
            f"ExecStart=/usr/bin/env bash -lc {shlex.quote(f'{run_command} && {notify_command}')}",
            "",
        ]
    )
    timer_body = "\n".join(
        [
            "[Unit]",
            "Description=Run gdrive hourly",
            "",
            "[Timer]",
            "OnBootSec=5m",
            "OnUnitActiveSec=1h",
            "Persistent=true",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    service_path.write_text(service_body, encoding="utf-8")
    timer_path.write_text(timer_body, encoding="utf-8")


def systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=True,
        text=True,
        capture_output=True,
    )


def install_timer() -> int:
    write_timer_units()
    systemctl_user("daemon-reload")
    systemctl_user("enable", "--now", f"{unit_name()}.timer")
    print(f"timer enabled: {unit_name()}.timer")
    return 0


def disable_timer() -> int:
    write_timer_units()
    systemctl_user("disable", "--now", f"{unit_name()}.timer")
    print(f"timer disabled: {unit_name()}.timer")
    return 0


def timer_status() -> int:
    result = systemctl_user("status", f"{unit_name()}.timer")
    print(result.stdout.strip())
    return 0


def run_sync_all() -> int:
    config = load_config()
    did_work = False
    for preset, account in config.accounts.items():
        regs = account.registrations
        if not regs:
            continue
        require_client_secret(account)
        backup_root_name = require_backup_root_name(account)
        client = drive_client(preset)
        did_work = True
        for reg in regs:
            summary = sync_registration(preset, reg, client, backup_root_name)
            update_registration(preset, reg)
            print(
                f"{preset}:{reg.id}\tcreated={summary.created}\tupdated={summary.updated}\tmoved={summary.moved}\tdeleted={summary.deleted}"
            )
    if not did_work:
        raise CliError("no registrations")
    return 0


def parse_command(preset: str | None, command: str | None, params: list[str]) -> tuple[str, list[str]]:
    if not preset:
        raise CliError("missing preset: use `gdrive <preset> <command>`")
    if not str(preset).isdigit():
        raise CliError("preset must be numeric, like `1` or `2`")
    if not command:
        raise CliError("missing command")
    if command in GLOBAL_COMMANDS:
        raise CliError(f"`{command}` is global: use `gdrive {command}`")
    if command not in PRESET_COMMANDS:
        raise CliError(f"unknown command `{command}`")
    return str(preset), list(params)


def _config_path() -> Path:
    ensure_dirs()
    return load_config().path


def _dispatch(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.preset == "auth":
            if args.command is None or args.params:
                raise CliError("usage: gdrive auth <client_secret_path>")
            return auth_account(args.command)
        if args.preset in {"run", "ti", "td", "st", "conf"}:
            if args.command or args.params:
                raise CliError(f"usage: gdrive {args.preset}")
            if args.preset == "conf":
                return open_config_in_editor()
            if args.preset == "run":
                return run_sync_all()
            if args.preset == "ti":
                return install_timer()
            if args.preset == "td":
                return disable_timer()
            return timer_status()
        preset, params = parse_command(args.preset, args.command, args.params)
        if args.command == "reg":
            if len(params) != 2:
                raise CliError("usage: gdrive <preset> reg <local_dir> <drive_path>")
            ensure_setup(preset, interactive=True)
            reg = add_registration(preset, params[0], params[1])
            print(f"registered\t{preset}\t{reg.id}\t{reg.local_dir}\t{reg.drive_path}")
            return 0
        if args.command == "ls":
            if params:
                raise CliError("usage: gdrive <preset> ls")
            ensure_setup(preset, interactive=True)
            return print_registrations(preset)
        if args.command == "rm":
            if len(params) != 1:
                raise CliError("usage: gdrive <preset> rm <edit_id>")
            ensure_setup(preset, interactive=True)
            reg = remove_registration(preset, params[0])
            delete_state(preset, reg.id)
            print(f"removed\t{preset}\t{reg.id}\t{reg.local_dir}")
            return 0
        if args.command == "nav":
            if params:
                raise CliError("usage: gdrive <preset> nav")
            return run_nav(preset)
        if args.command == "up":
            return run_upload_picker(preset, params)
        print(compact_usage())
        return 1
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        print(f"systemctl failed: {message}", file=sys.stderr)
        return 2


APP_SPEC = AppSpec(
    app_name="gdrive",
    version=__version__,
    help_text=HELP_TEXT,
    install_script_path=INSTALL_SCRIPT,
    no_args_mode="help",
    config_path_factory=_config_path,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return run_app(APP_SPEC, args, _dispatch)
