from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

try:
    import charset_normalizer  # noqa: F401
except Exception:  # pragma: no cover - optional during source-only edge cases
    charset_normalizer = None

from . import __version__
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
    update_registration,
)
from .errors import CliError
from .paths import ensure_dirs
from .sync import delete_state, sync_registration

ANSI_RESET = "\033[0m"
ANSI_GRAY = "\033[38;5;245m"
COMMAND_HELP = {
    "reg": "register folder sync",
    "ls": "list registrations",
    "run": "run sync",
    "rm": "remove registration",
    "ti": "install hourly timer",
    "td": "disable timer",
    "st": "timer status",
}


def _muted_text(text: str) -> str:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        return text
    return f"{ANSI_GRAY}{text}{ANSI_RESET}"


def compact_usage() -> str:
    return "\n".join(
        [
            "usage: gdrive -v",
            "       gdrive -u",
            "       gdrive <preset> reg <local_dir> <drive_path>",
            "       gdrive <preset> ls",
            "       gdrive <preset> run [edit_id]",
            "       gdrive <preset> rm <edit_id>",
            "       gdrive <preset> ti",
            "       gdrive <preset> td",
            "       gdrive <preset> st",
        ]
    )


def print_help_text() -> None:
    lines = [
        compact_usage(),
        "",
        "Google Drive backup CLI",
        "",
        "commands:",
        "  reg  register folder sync",
        "  ls   list registrations",
        "  run  run sync",
        "  rm   remove registration",
        "  ti   install hourly timer",
        "  td   disable timer",
        "  st   timer status",
        "",
        "options:",
        "  -h   show help and exit",
        "  -v   print version",
        "  -u   upgrade to latest release",
    ]
    print(_muted_text("\n".join(lines)))


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
    secret = require_client_secret(account)
    creds = load_credentials(preset, secret)
    return DriveClient(creds)


def upgrade_app() -> int:
    script_url = "https://raw.githubusercontent.com/ryangerardwilson/gdrive/main/install.sh"
    with urllib.request.urlopen(script_url) as response:
        script_body = response.read()
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(script_body)
        script_path = Path(handle.name)
    try:
        script_path.chmod(0o700)
        result = subprocess.run(
            ["/usr/bin/env", "bash", str(script_path), "-u"],
            check=False,
            text=True,
            env=os.environ.copy(),
        )
        return result.returncode
    finally:
        script_path.unlink(missing_ok=True)


def unit_name(preset: str) -> str:
    return f"gdrive-{preset}"


def write_timer_units(preset: str) -> None:
    ensure_dirs()
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path = systemd_dir / f"{unit_name(preset)}.service"
    timer_path = systemd_dir / f"{unit_name(preset)}.timer"
    entrypoint = Path(__file__).resolve().parents[1] / "main.py"
    python_bin = Path(sys.executable).resolve()
    service_body = "\n".join(
        [
            "[Unit]",
            f"Description=gdrive sync preset {preset}",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={entrypoint.parent}",
            f"ExecStart={python_bin} {entrypoint} {preset} run",
            "",
        ]
    )
    timer_body = "\n".join(
        [
            "[Unit]",
            f"Description=Run gdrive preset {preset} hourly",
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


def install_timer(preset: str) -> int:
    write_timer_units(preset)
    systemctl_user("daemon-reload")
    systemctl_user("enable", "--now", f"{unit_name(preset)}.timer")
    print(f"timer enabled: {unit_name(preset)}.timer")
    return 0


def disable_timer(preset: str) -> int:
    write_timer_units(preset)
    systemctl_user("disable", "--now", f"{unit_name(preset)}.timer")
    print(f"timer disabled: {unit_name(preset)}.timer")
    return 0


def timer_status(preset: str) -> int:
    result = systemctl_user("status", f"{unit_name(preset)}.timer")
    print(result.stdout.strip())
    return 0


def run_sync(preset: str, target_id: str | None) -> int:
    account = get_account(load_config(), preset)
    backup_root_name = require_backup_root_name(account)
    regs = account.registrations
    if target_id:
        regs = [get_registration(preset, target_id)]
    if not regs:
        raise CliError("no registrations")
    client = drive_client(preset)
    for reg in regs:
        summary = sync_registration(preset, reg, client, backup_root_name)
        update_registration(preset, reg)
        print(
            f"{reg.id}\tcreated={summary.created}\tupdated={summary.updated}\tmoved={summary.moved}\tdeleted={summary.deleted}"
        )
    return 0


def parse_command(preset: str | None, command: str | None, params: list[str]) -> tuple[str, list[str]]:
    if not preset:
        raise CliError("missing preset: use `gdrive <preset> <command>`")
    if not str(preset).isdigit():
        raise CliError("preset must be numeric, like `1` or `2`")
    if not command:
        raise CliError("missing command")
    if command not in COMMAND_HELP:
        raise CliError(f"unknown command `{command}`")
    return str(preset), list(params)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        print_help_text()
        return 0
    args = parser.parse_args(argv)
    if args.help:
        print_help_text()
        return 0
    try:
        if args.version:
            print(__version__)
            return 0
        if args.upgrade:
            return upgrade_app()
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
        if args.command == "run":
            if len(params) > 1:
                raise CliError("usage: gdrive <preset> run [edit_id]")
            ensure_setup(preset, interactive=False)
            return run_sync(preset, params[0] if params else None)
        if args.command == "rm":
            if len(params) != 1:
                raise CliError("usage: gdrive <preset> rm <edit_id>")
            ensure_setup(preset, interactive=True)
            reg = remove_registration(preset, params[0])
            delete_state(preset, reg.id)
            print(f"removed\t{preset}\t{reg.id}\t{reg.local_dir}")
            return 0
        if args.command == "ti":
            if params:
                raise CliError("usage: gdrive <preset> ti")
            ensure_setup(preset, interactive=True)
            return install_timer(preset)
        if args.command == "td":
            if params:
                raise CliError("usage: gdrive <preset> td")
            ensure_setup(preset, interactive=True)
            return disable_timer(preset)
        if args.command == "st":
            if params:
                raise CliError("usage: gdrive <preset> st")
            ensure_setup(preset, interactive=True)
            return timer_status(preset)
        print(compact_usage())
        return 1
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        print(f"systemctl failed: {message}", file=sys.stderr)
        return 2
