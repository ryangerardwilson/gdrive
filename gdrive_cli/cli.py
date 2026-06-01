from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request
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
from .sync import delete_state, restore_registration_from_remote, sync_registration
from .transfer import normalize_upload_paths

ANSI_GRAY = "\033[38;5;245m"
ANSI_RESET = "\033[0m"
INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/ryangerardwilson/gdrive/main/install.sh"
HELP_TEXT = """gdrive

global actions:
  gdrive help
    show this help
  gdrive version
    print the installed version
  gdrive upgrade
    upgrade to the latest release

features:
  authorize a Google account and save or refresh its preset
  # gdrive auth <client_secret_path>
  gdrive auth ~/Documents/credentials/client_secret.json

  register folders to sync into Drive, then inspect or remove registrations
  # gdrive <preset> register <local_dir> as <drive_path> | gdrive <preset> list registrations | gdrive <preset> remove registration <id>
  gdrive 1 register ~/Documents as Documents
  gdrive 1 list registrations
  gdrive 1 remove registration abcd1234

  browse Drive, upload local files, restore registered folders, and run sync flows
  # gdrive <preset> browse | gdrive <preset> upload <path...> | gdrive sync restore | gdrive sync run
  gdrive 1 browse
  gdrive 1 upload ~/Downloads/report.pdf ~/Pictures
  gdrive sync restore
  gdrive sync run

  install, disable, or inspect the hourly systemd timer
  # gdrive timer install | gdrive timer disable | gdrive timer status
  gdrive timer install
  gdrive timer disable
  gdrive timer status

  open the editable app config
  # gdrive config
  gdrive config
"""


def muted(text: str) -> str:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        return text
    return f"{ANSI_GRAY}{text}{ANSI_RESET}"


def print_help_text() -> None:
    print(muted(HELP_TEXT.rstrip()))


def upgrade_app() -> int:
    try:
        with urllib.request.urlopen(INSTALL_SCRIPT_URL) as response:
            script_body = response.read()
    except OSError as exc:
        print(f"upgrade failed: {exc}", file=sys.stderr)
        return 2

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(script_body)
        script_path = Path(handle.name)
    try:
        script_path.chmod(0o700)
        result = subprocess.run(
            ["/usr/bin/env", "bash", str(script_path), "upgrade"],
            check=False,
            text=True,
            env=os.environ.copy(),
        )
        return result.returncode
    finally:
        script_path.unlink(missing_ok=True)


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
        raise CliError(f"missing client secret in config for preset `{preset}`: run `gdrive auth <client_secret_path>` first")
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
        raise CliError(f"missing backup root in config for preset `{preset}`: run `gdrive {preset} list registrations` interactively first")
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


def _notification_shell_function() -> str:
    return " ".join(
        [
            "notify() {",
            'summary="$1";',
            'body="${2:-}";',
            'urgency="${3:-normal}";',
            'qs="${XDG_CONFIG_HOME:-$HOME/.config}/quickshell/omarchy-bar";',
            'if command -v quickshell >/dev/null 2>&1 && quickshell ipc -p "$qs" call bar notify "$summary" "$body" "$urgency" >/dev/null 2>&1; then return 0; fi;',
            'if command -v notify-send >/dev/null 2>&1; then notify-send -a "$summary" -u "$urgency" "$summary" "$body" || true; fi;',
            "};",
        ]
    )


def _build_timer_service_script(run_command: str) -> str:
    return " ".join(
        [
            _notification_shell_function(),
            "notify 'gdrive' 'Hourly backup started' normal;",
            f"if {run_command}; then",
            "notify 'gdrive' 'Hourly backup finished successfully' normal;",
            "else",
            "rc=$?;",
            "notify 'gdrive' 'Hourly backup failed' critical;",
            'exit "$rc";',
            "fi",
        ]
    )


def write_timer_units() -> None:
    ensure_dirs()
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path = systemd_dir / f"{unit_name()}.service"
    timer_path = systemd_dir / f"{unit_name()}.timer"
    entrypoint = Path(__file__).resolve().parents[1] / "main.py"
    run_command = _build_runtime_command("sync", "run")
    service_script = _build_timer_service_script(run_command)
    service_body = "\n".join(
        [
            "[Unit]",
            "Description=gdrive sync all presets",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={entrypoint.parent}",
            f"ExecStart=/usr/bin/env bash -lc {shlex.quote(service_script)}",
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
            "OnActiveSec=5m",
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
    systemctl_user("enable", f"{unit_name()}.timer")
    systemctl_user("restart", f"{unit_name()}.timer")
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


def run_restore_all() -> int:
    config = load_config()
    did_work = False
    for preset, account in config.accounts.items():
        if not account.registrations:
            continue
        did_work = _restore_account_registrations(preset) or did_work
    if not did_work:
        raise CliError("no registrations")
    return 0


def _usage(shape: str) -> CliError:
    return CliError(f"usage: {shape}")


def _restore_account_registrations(preset: str, registration_id: str | None = None) -> bool:
    config = load_config()
    account = get_account(config, preset)
    regs = account.registrations
    if registration_id is not None:
        regs = [get_registration(preset, registration_id)]
    if not regs:
        return False
    require_client_secret(account)
    backup_root_name = require_backup_root_name(account)
    client = drive_client(preset)
    did_work = False
    for reg in regs:
        if not reg.enabled:
            continue
        summary = restore_registration_from_remote(preset, reg, client, backup_root_name)
        update_registration(preset, reg)
        did_work = True
        print(
            f"{preset}:{reg.id}\tdownloaded={summary.downloaded}\tdirs_created={summary.dirs_created}\tskipped_existing={summary.skipped_existing}\tstate_entries={summary.state_entries}"
        )
    return did_work


def _dispatch_preset(preset: str, params: list[str]) -> int:
    if not params:
        raise CliError(f"missing command: use `gdrive {preset} list registrations`")
    command = params[0]
    if command == "register":
        if len(params) != 4 or params[2] != "as":
            raise _usage("gdrive <preset> register <local_dir> as <drive_path>")
        ensure_setup(preset, interactive=True)
        reg = add_registration(preset, params[1], params[3])
        print(f"registered\t{preset}\t{reg.id}\t{reg.local_dir}\t{reg.drive_path}")
        return 0
    if params == ["list", "registrations"]:
        ensure_setup(preset, interactive=True)
        return print_registrations(preset)
    if params[:2] == ["remove", "registration"]:
        if len(params) != 3:
            raise _usage("gdrive <preset> remove registration <id>")
        ensure_setup(preset, interactive=True)
        reg = remove_registration(preset, params[2])
        delete_state(preset, reg.id)
        print(f"removed\t{preset}\t{reg.id}\t{reg.local_dir}")
        return 0
    if params == ["browse"]:
        return run_nav(preset)
    if command == "upload":
        return run_upload_picker(preset, params[1:])
    if params == ["restore", "registrations"]:
        _restore_account_registrations(preset)
        return 0
    if params[:2] == ["restore", "registration"]:
        if len(params) != 3:
            raise _usage("gdrive <preset> restore registration <id>")
        _restore_account_registrations(preset, params[2])
        return 0
    raise CliError(f"unknown command `{command}`")


def _dispatch(argv: list[str]) -> int:
    try:
        if not argv:
            print_help_text()
            return 0
        command = argv[0]
        params = argv[1:]

        if command == "auth":
            if len(params) != 1:
                raise CliError("usage: gdrive auth <client_secret_path>")
            return auth_account(params[0])

        if command == "config":
            if params:
                raise _usage("gdrive config")
            return open_config_in_editor()

        if command == "sync":
            if params == ["run"]:
                return run_sync_all()
            if params == ["restore"]:
                return run_restore_all()
            raise _usage("gdrive sync run | gdrive sync restore")

        if command == "timer":
            if len(params) != 1:
                raise _usage("gdrive timer install|disable|status")
            if params[0] == "install":
                return install_timer()
            if params[0] == "disable":
                return disable_timer()
            if params[0] == "status":
                return timer_status()
            raise _usage("gdrive timer install|disable|status")

        if str(command).isdigit():
            return _dispatch_preset(str(command), params)

        raise CliError(f"unknown command `{command}`")
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        print(f"systemctl failed: {message}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args == ["help"]:
        print_help_text()
        return 0
    if args == ["version"]:
        print(__version__)
        return 0
    if args == ["upgrade"]:
        return upgrade_app()
    if args and args[0] in {"help", "version", "upgrade"}:
        print(f"usage: gdrive {args[0]}", file=sys.stderr)
        return 2
    return _dispatch(args)
