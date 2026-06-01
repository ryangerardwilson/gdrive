# AGENTS.md

## Workspace Defaults
- Follow `/home/ryan/Subagents/cpo/CLI_TUI_STYLE_GUIDE.md` for CLI/TUI taste and help shape.
- Follow `/home/ryan/Subagents/cto/CANONICAL_REFERENCE_IMPLEMENTATION_FOR_CLI_AND_TUI_APPS.md` for executable contract details such as `help`, `version`, `upgrade`, installer behavior, release workflow expectations, and regression expectations.
- This file only records `gdrive`-specific constraints or durable deviations.

## Mission
Implement a Google Drive backup CLI that treats the local filesystem as the source of truth for registered folders.

## Product boundaries
- Scope is Google Drive folder backup and reconciliation only.
- The app manages explicit folder registrations; it is not a general Drive browser.
- Local state must be sufficient to propagate local deletes and renames to Drive.
- Hourly automation should use a user-level systemd timer on Linux.
- Config is multi-account and preset-based, matching the Gmail app's numeric preset model.

## Interface constraints
- Keep the command surface compact and keyboard-first.
- No-arg invocation prints the same help as `help`.
- Canonical commands are declarative English only:
  - `gdrive auth <client_secret_path>` authorizes a Google account and creates or updates a preset
  - `gdrive <preset> register <local_dir> as <drive_path>` registers a local folder to a Drive path
  - `gdrive <preset> list registrations` lists registrations
  - `gdrive <preset> remove registration <id>` removes a registration
  - `gdrive <preset> browse` opens the Drive navigator
  - `gdrive <preset> upload <path...>` opens upload-picker mode for local paths
  - `gdrive sync run` syncs all registrations across all presets
  - `gdrive sync restore` restores all registered folders without deleting remote files
  - `gdrive <preset> restore registrations` restores one preset
  - `gdrive <preset> restore registration <id>` restores one registration
  - `gdrive timer install|disable|status` manages the user-level systemd timer
  - `gdrive config` opens the editable app config
- `auth <client_secret_path>` is the only no-preset account bootstrap command.
- Registration, browse, upload, and preset restore commands are preset-scoped.
- `sync run`, `sync restore`, and `timer install|disable|status` are global and must not take a preset.
- `sync restore` is the non-destructive fresh-machine restore path. It must hydrate local registered folders from Drive and seed sync state before any timer-driven `sync run`.
- Output should stay plain-text and deterministic.
- Do not keep terse aliases such as `reg`, `ls`, `rm`, `nav`, `up`, `run`, `pull`, `ti`, `td`, `st`, or `conf` as product surfaces.

## Architecture expectations
- Keep CLI parsing separate from Drive API calls and sync planning.
- Keep config under XDG config paths and sync state under XDG data paths.
- Keep OAuth tokens account-scoped and sync snapshots per preset so multiple Google accounts do not collide.
- Token filenames should use the authorized account email, not the preset number.
- Do not add legacy-token fallback branches to normal runtime code.
- Persist remote ids per tracked path so future syncs can delete/update the correct Drive items.
- Prefer small testable helpers for path normalization, state reconciliation, and rename detection.
- If the client secret path or backup root name is missing for a preset, interactive commands should prompt once and persist them for that preset.
- Registration paths are always relative to the preset's backup root and must not include that prefix.

## Implementation rules
- Python 3.11+.
- Do not print secrets or token contents.
- Create config/data directories automatically with restrictive permissions where practical.
- Avoid hidden remote magic; if the app manages a Drive folder tree, store that mapping locally.

## Done when
- A user can authenticate with a Google desktop OAuth client.
- A user can register one or more local folders with Drive target paths under a numeric preset.
- `sync run` makes Drive match local content across every configured preset, including local deletes.
- Content-preserving local file renames are propagated as Drive moves/renames when detectable; otherwise the end state must still match local.
- `timer install` installs one hourly user timer that runs the same global sync command and sends notifications through the Quickshell bar, with `notify-send` only as a fallback.
- Fresh-machine restore can run `gdrive sync restore` before `gdrive timer install` without deleting remote files due to an empty local tree.
