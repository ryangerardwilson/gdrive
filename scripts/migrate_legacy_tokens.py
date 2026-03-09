#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gdrive_cli.config import get_account, load_config
from gdrive_cli.errors import CliError
from gdrive_cli.paths import legacy_token_file, token_file_for_account_key, token_file_for_preset


def migrate_preset_token(preset: str) -> Path:
    account = get_account(load_config(), preset)
    if not account.account_key:
        raise CliError(f"preset {preset} is missing account_key; re-run `gdrive auth <client_secret_path>`")
    target_path = token_file_for_account_key(account.account_key)
    if target_path.exists():
        return target_path
    preset_token_path = token_file_for_preset(account.preset)
    if preset_token_path.exists():
        preset_token_path.rename(target_path)
        return target_path
    root_legacy_path = legacy_token_file()
    if account.preset == "1" and root_legacy_path.exists():
        root_legacy_path.rename(target_path)
        return target_path
    raise CliError(f"no legacy token found for preset {preset}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/migrate_legacy_tokens.py",
        add_help=True,
        description="One-time migration of legacy gdrive token filenames to account-key names.",
    )
    parser.add_argument("presets", nargs="*", help="Preset ids to migrate. Default: all presets.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    config = load_config()
    presets = args.presets or sorted(config.accounts)
    if not presets:
        print("no presets configured", file=sys.stderr)
        return 1
    for preset in presets:
        try:
            target = migrate_preset_token(preset)
        except CliError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"migrated\t{preset}\t{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
