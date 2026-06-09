# Project Scope

## Overview

`gdrive` is a Go/Bubble Tea Google Drive backup CLI. Local folders are the
source of truth for registered backups, and Drive is reconciled to match local
content during `sync run`.

The previous Python/curses implementation is retired.

## Product Contract

- Numeric presets represent independent Google accounts.
- `gdrive auth <client_secret_path>` authorizes or refreshes an account preset.
- Registrations map local directories to Drive paths under a preset backup root.
- `gdrive sync restore` is the fresh-machine hydration path and must not delete
  remote files.
- `gdrive sync run` is local-authoritative and propagates local creates,
  updates, deletes, and detectable content-preserving renames.
- `gdrive timer install|disable|status` manages one user-level systemd timer.

## Architecture

- `cmd/gdrive`: binary entrypoint.
- `internal/cli`: command grammar, timer units, config editor, orchestration.
- `internal/config`: config JSON compatibility and registration management.
- `internal/auth`: OAuth desktop flow and email-scoped token storage.
- `internal/driveapi`: narrow wrapper around Google Drive API calls.
- `internal/syncer`: local scan, state, sync, restore, rename detection.
- `internal/transfer`: upload and folder zip download helpers.
- `internal/tui`: Bubble Tea Drive navigator/upload picker.
- `internal/version`: release-time version injection.

## Config And State

Config:

```text
$XDG_CONFIG_HOME/gdrive/config.json
~/.config/gdrive/config.json
```

Data:

```text
$XDG_DATA_HOME/gdrive/
~/.local/share/gdrive/
```

Tokens:

```text
tokens/<email>.json
```

State:

```text
state/<preset>-<registration-id>.json
```

## Non-Goals

- General-purpose Drive management.
- Shared-drive support.
- Background daemons outside the systemd user timer.
- Reintroducing Python curses or PyInstaller.

## Verification

```bash
go test ./...
go run ./cmd/gdrive help
go run ./cmd/gdrive version
./install.sh from .
~/.gdrive/bin/gdrive version
```
