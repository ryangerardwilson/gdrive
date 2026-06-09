# Product Engineer Role

## Purpose

Own gdrive-specific facts that should not live in root generalists.

## Load Guidance

Load this file for `gdrive` implementation, CLI/TUI, installer, release,
storage, configuration, OAuth, sync, or project-specific product work.

## Owns

- CLI/TUI contract, command grammar, config, storage, and installer constraints.
- Release, upgrade, and verification expectations specific to this app.
- Google Drive backup product facts and local-authoritative sync behavior.

## Project Context

`gdrive` is a Go/Bubble Tea app. The old Python/curses implementation is
retired. Do not add new Python curses modules or PyInstaller release paths.

Current structure:

- `cmd/gdrive/main.go`: binary entrypoint.
- `internal/cli/`: help/version/upgrade/config, timer units, command grammar.
- `internal/config/`: config JSON compatibility and registration management.
- `internal/auth/`: OAuth desktop flow and email-scoped token storage.
- `internal/driveapi/`: Google Drive API wrapper.
- `internal/syncer/`: local scan, state, sync, restore, rename detection.
- `internal/transfer/`: upload and folder zip download helpers.
- `internal/tui/`: Bubble Tea navigator/upload picker.
- `internal/version/`: release-time version variable.

## Command Contract

```bash
gdrive auth <client_secret_path>
gdrive <preset> register <local_dir> as <drive_path>
gdrive <preset> list registrations
gdrive <preset> remove registration <id>
gdrive <preset> browse
gdrive <preset> upload <path...>
gdrive sync run
gdrive sync restore
gdrive <preset> restore registrations
gdrive <preset> restore registration <id>
gdrive timer install
gdrive timer disable
gdrive timer status
gdrive config
```

## Release Constraints

- Keep `internal/version.Version` at `0.0.0` in source.
- Inject the release version with Go ldflags.
- Release asset name: `gdrive-linux-x64.tar.gz`.
- Installer must accept both old `# Managed by gdrive local-bin launcher` and
  new `# Managed by gdrive installer local-bin launcher` markers.
