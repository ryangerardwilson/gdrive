# gdrive

Google Drive backup CLI with local-first sync semantics.

`gdrive` is now a Go binary. The command grammar, config shape, token location,
and sync state files remain compatible with the previous Python app, while the
terminal navigator is a smaller Bubble Tea UI.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/gdrive/main/install.sh | bash
```

The installer places the binary at `~/.gdrive/bin/gdrive` and writes the public
launcher at `~/.local/bin/gdrive`.

## Google OAuth Setup

1. Open Google Cloud Console.
2. Create or select a project.
3. Enable the Google Drive API.
4. Configure the OAuth consent screen.
5. Create an OAuth client ID with application type `Desktop app`.
6. Download the client JSON.

Authorize a preset:

```bash
gdrive auth ~/Documents/credentials/client_secret.json
```

The first authorization stores the token by account email under
`~/.local/share/gdrive/tokens/`.

## Storage

- Config: `~/.config/gdrive/config.json`
- Data: `~/.local/share/gdrive/`
- OAuth token: `~/.local/share/gdrive/tokens/<email>.json`
- Per-registration sync state: `~/.local/share/gdrive/state/<preset>-<id>.json`

Config shape:

```json
{
  "accounts": {
    "1": {
      "client_secret_file": "/home/ryan/.config/gdrive/client_secret.json",
      "email": "you@example.com",
      "backup_root_name": "Backups",
      "registrations": []
    }
  },
  "handlers": {}
}
```

## Usage

```bash
gdrive
gdrive help
gdrive version
gdrive upgrade
gdrive config
gdrive auth <client_secret_path>
gdrive <preset> register <local_dir> as <drive_path>
gdrive <preset> list registrations
gdrive <preset> browse
gdrive <preset> upload <file_path> <file_path> ...
gdrive <preset> restore registrations
gdrive <preset> restore registration <id>
gdrive <preset> remove registration <id>
gdrive sync restore
gdrive sync run
gdrive timer install
gdrive timer disable
gdrive timer status
```

Examples:

```bash
gdrive auth ~/Documents/credentials/client_secret.json
gdrive 1 register ~/Documents as Documents
gdrive 1 list registrations
gdrive 1 browse
gdrive 1 upload ~/Downloads/report.pdf ~/Pictures
gdrive sync restore
gdrive sync run
gdrive timer install
```

Notes:

- Each preset is an independent Google account setup with its own OAuth token,
  backup root, and registrations.
- `auth <client_secret_path>` is the account bootstrap command.
- `backup_root_name` is the single top-level Drive folder under My Drive that
  holds all managed backups for that preset.
- `drive_path` is always relative to that preset backup root. Do not include the
  root itself in `register`.
- `sync restore` hydrates registered local folders from Drive and seeds sync
  state before the local-authoritative timer starts.
- `sync run` makes Drive match local content, including local deletes and
  detectable content-preserving renames.
- `browse` downloads the selected file to the current working directory; folder
  downloads are zipped.
- `upload <path...>` opens the Drive navigator in upload mode and uploads the
  paths into the selected Drive folder.

## Timer

`timer install` writes a global user service and hourly timer under
`~/.config/systemd/user/`. The service sends desktop notifications through the
Quickshell `omarchy-bar` IPC endpoint when available, falling back to
`notify-send`.

```bash
gdrive sync restore
gdrive timer install
systemctl --user list-timers gdrive.timer
```

## Development

```bash
go test ./...
go run ./cmd/gdrive help
./install.sh from .
./push_release_upgrade.sh
```
