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


def compact_usage() -> str:
    return "\n".join(
        [
            "usage: gdrive -v",
            "       gdrive -u",
            "usage: gdrive reg <local_dir> <drive_path>",
            "       gdrive ls",
            "       gdrive run [id]",
            "       gdrive rm <id>",
            "       gdrive ti",
            "       gdrive td",
            "       gdrive st",
        ]
    )


def prompt_client_secret_file() -> Path:
    while True:
        value = input("Google client secret file path: ").strip()
        if not value:
            print("enter a path to a Google desktop OAuth client JSON file", file=sys.stderr)
            continue
        try:
            return set_client_secret(value)
        except CliError as exc:
            print(str(exc), file=sys.stderr)


def ensure_client_secret(interactive: bool) -> Path:
    config = load_config()
    if config.client_secret_file:
        return require_client_secret(config)
    if not interactive or not sys.stdin.isatty():
        raise CliError("missing client secret in config: run `gdrive` interactively first")
    return prompt_client_secret_file()


def prompt_backup_root_name() -> str:
    while True:
        value = input("Drive backup root dir name: ").strip()
        if value:
            return set_backup_root_name(value)
        print("enter a folder name like `Backups` or `ComputerBackups`", file=sys.stderr)


def ensure_backup_root_name(interactive: bool) -> str:
    config = load_config()
    if config.backup_root_name:
        return config.backup_root_name
    if not interactive or not sys.stdin.isatty():
        raise CliError("missing backup root in config: run `gdrive` interactively first")
    return prompt_backup_root_name()


def ensure_setup(interactive: bool) -> tuple[Path, str]:
    client_secret = ensure_client_secret(interactive=interactive)
    backup_root_name = ensure_backup_root_name(interactive=interactive)
    return client_secret, backup_root_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gdrive",
        description="Google Drive backup CLI",
        add_help=False,
    )
    parser.add_argument("-h", action="help", help="show help and exit")
    parser.add_argument("-v", action="store_true", dest="version", help="print version")
    parser.add_argument("-u", action="store_true", dest="upgrade", help="upgrade to latest release")
    subs = parser.add_subparsers(dest="command")

    reg_p = subs.add_parser("reg", help="register folder sync", add_help=False)
    reg_p.add_argument("-h", action="help", help="show help and exit")
    reg_p.add_argument("local_dir")
    reg_p.add_argument("drive_path")

    ls_p = subs.add_parser("ls", help="list registrations", add_help=False)
    ls_p.add_argument("-h", action="help", help="show help and exit")

    run_p = subs.add_parser("run", help="run sync", add_help=False)
    run_p.add_argument("-h", action="help", help="show help and exit")
    run_p.add_argument("id", nargs="?")

    rm_p = subs.add_parser("rm", help="remove registration", add_help=False)
    rm_p.add_argument("-h", action="help", help="show help and exit")
    rm_p.add_argument("id")

    ti_p = subs.add_parser("ti", help="install hourly timer", add_help=False)
    ti_p.add_argument("-h", action="help", help="show help and exit")
    td_p = subs.add_parser("td", help="disable timer", add_help=False)
    td_p.add_argument("-h", action="help", help="show help and exit")
    st_p = subs.add_parser("st", help="timer status", add_help=False)
    st_p.add_argument("-h", action="help", help="show help and exit")
    return parser


def print_registrations() -> int:
    config = load_config()
    root_name = require_backup_root_name(config)
    regs = config.registrations
    if not regs:
        print("no registrations")
        return 0
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    label_width = len("edit_id") + 1
    sections: list[str] = []
    for index, reg in enumerate(regs, start=1):
        url = f"https://drive.google.com/drive/folders/{reg.remote_root_id}" if reg.remote_root_id else "-"
        prefix = f"[{index}]"
        header = prefix + ("-" * max(1, 79 - len(prefix)))
        body_lines = [
            f"{'edit_id':<{label_width}}: {reg.id}",
            f"{'local':<{label_width}}: {reg.local_dir}",
            f"{'drive':<{label_width}}: {root_name}/{reg.drive_path}",
            url,
        ]
        if use_color:
            body_lines = [f"{ANSI_GRAY}{line}{ANSI_RESET}" for line in body_lines]
        sections.append(
            "\n".join(
                [
                    header,
                    *body_lines,
                ]
            )
        )
    print("\n".join(sections))
    return 0


def drive_client() -> DriveClient:
    from .auth import load_credentials
    from .drive_api import DriveClient

    config = load_config()
    secret = require_client_secret(config)
    creds = load_credentials(secret)
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
        env = os.environ.copy()
        result = subprocess.run(
            ["/usr/bin/env", "bash", str(script_path), "-u"],
            check=False,
            text=True,
            env=env,
        )
        return result.returncode
    finally:
        script_path.unlink(missing_ok=True)


def write_timer_units() -> None:
    ensure_dirs()
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path = systemd_dir / "gdrive.service"
    timer_path = systemd_dir / "gdrive.timer"
    entrypoint = Path(__file__).resolve().parents[1] / "main.py"
    python_bin = Path(sys.executable).resolve()
    service_body = "\n".join(
        [
            "[Unit]",
            "Description=gdrive sync",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={entrypoint.parent}",
            f"ExecStart={python_bin} {entrypoint} run",
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
    service_path.write_text(service_body)
    timer_path.write_text(timer_body)


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
    systemctl_user("enable", "--now", "gdrive.timer")
    print("timer enabled: gdrive.timer")
    return 0


def disable_timer() -> int:
    write_timer_units()
    systemctl_user("disable", "--now", "gdrive.timer")
    print("timer disabled: gdrive.timer")
    return 0


def timer_status() -> int:
    result = systemctl_user("status", "gdrive.timer")
    print(result.stdout.strip())
    return 0


def run_sync(target_id: str | None) -> int:
    config = load_config()
    backup_root_name = require_backup_root_name(config)
    regs = config.registrations
    if target_id:
        regs = [get_registration(target_id)]
    if not regs:
        raise CliError("no registrations")
    client = drive_client()
    for reg in regs:
        summary = sync_registration(reg, client, backup_root_name)
        update_registration(reg)
        print(
            f"{reg.id}\tcreated={summary.created}\tupdated={summary.updated}\tmoved={summary.moved}\tdeleted={summary.deleted}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(compact_usage())
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.version:
            print(__version__)
            return 0
        if args.upgrade:
            return upgrade_app()
        if args.command == "reg":
            ensure_setup(interactive=True)
            reg = add_registration(args.local_dir, args.drive_path)
            print(f"registered\t{reg.id}\t{reg.local_dir}\t{reg.drive_path}")
            return 0
        if args.command == "ls":
            ensure_setup(interactive=True)
            return print_registrations()
        if args.command == "run":
            ensure_setup(interactive=False)
            return run_sync(args.id)
        if args.command == "rm":
            ensure_setup(interactive=True)
            reg = remove_registration(args.id)
            delete_state(reg.id)
            print(f"removed\t{reg.id}\t{reg.local_dir}")
            return 0
        if args.command == "ti":
            ensure_setup(interactive=True)
            return install_timer()
        if args.command == "td":
            ensure_setup(interactive=True)
            return disable_timer()
        if args.command == "st":
            ensure_setup(interactive=True)
            return timer_status()
        print(compact_usage())
        return 1
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        print(f"systemctl failed: {message}", file=sys.stderr)
        return 2
