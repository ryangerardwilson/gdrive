# AGENTS.md

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
- No-arg invocation prints the same help as `-h`.
- Primary verbs for v1:
  - `auth` to authorize a Google account and create/update a preset
  - `reg` to register a local folder -> Drive path mapping
  - `ls` to list registrations
  - `run` to sync all or one registration
  - `rm` to remove a registration
  - `ti`, `td`, `st` for timer install/disable/status
- `auth <client_secret_path>` is the only no-preset account bootstrap command.
- Account-scoped commands must use `gdrive <preset> <command> ...`.
- Output should stay plain-text and deterministic.

## Architecture expectations
- Keep CLI parsing separate from Drive API calls and sync planning.
- Keep config under XDG config paths and sync state under XDG data paths.
- Keep OAuth tokens and sync snapshots per preset so multiple Google accounts do not collide.
- Token filenames should be keyed by a stable internal account key, not the preset number.
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
- `run` makes Drive match local content, including local deletes.
- Content-preserving local file renames are propagated as Drive moves/renames when detectable; otherwise the end state must still match local.
- `ti` installs an hourly user timer for that preset that runs the same sync command.
