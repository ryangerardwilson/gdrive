# gdrive

Google Drive backup CLI with local-first sync semantics.

## Install

Binary install:

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/gdrive/main/install.sh | bash
```

If `~/.local/bin` is not already on your `PATH`, add it once to `~/.bashrc`
and reload your shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
source ~/.bashrc
```

Source install:

```bash
cd ~/Apps/gdrive
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py -h
```

## External dependencies

- `notify-send` for timer success notifications
- a notification daemon such as Mako to display those notifications

## Google OAuth setup

1. Open Google Cloud Console.
2. Create or select a project.
3. Enable the Google Drive API.
4. Configure the OAuth consent screen.
5. Create an `OAuth client ID` with application type `Desktop app`.
6. Download the client JSON.
The first interactive command asks for:
- the preset-specific client secret JSON path
- the preset-specific Google Drive folder name that should hold all managed backups

The first authenticated sync opens a browser and stores the refresh token
locally. If the final browser page says `localhost refused to connect`, keep
the terminal open and paste the full `localhost` URL from the browser address
bar when prompted.

Recommended setup:

```bash
python main.py auth /path/to/client_secret.json
python main.py 1 reg ~/Documents "Documents"
python main.py run
```

## Storage

- Config: `~/.config/gdrive/config.json`
- Data: `~/.local/share/gdrive/`
- OAuth token: `~/.local/share/gdrive/tokens/<email>.json`
- Per-registration sync state: `~/.local/share/gdrive/state/<preset>-<id>.json`

Example config:

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
  "handlers": {
    "pdf_viewer": { "commands": [["zathura"]], "is_internal": false },
    "image_viewer": { "commands": [["swayimg"]], "is_internal": false },
    "csv_viewer": { "commands": [["vixl"]], "is_internal": true },
    "xlsx_viewer": { "commands": [["vixl"]], "is_internal": true },
    "audio_player": { "commands": [["alacritty", "-e", "rlc"]], "is_internal": false },
    "video_player": { "commands": [["alacritty", "-e", "ffplay", "-nodisp", "-autoexit"]], "is_internal": false }
  }
}
```

## Usage

```bash
gdrive
gdrive -h
gdrive -v
gdrive -u
gdrive auth <client_secret_path>
gdrive <preset> reg <local_dir> <drive_path>
gdrive <preset> ls
gdrive <preset> up <file_path> <file_path> ...
gdrive run
gdrive <preset> rm <edit_id>
gdrive ti
gdrive td
gdrive st
```

Examples:

```bash
python main.py auth ~/Documents/credentials/client_secret.json
python main.py 1 reg ~/Documents "Documents"
python main.py 2 reg ~/Pictures "Pictures"
python main.py 1 up ~/Downloads/report.pdf ~/Pictures
python main.py -v
python main.py 1 ls
python main.py run
python main.py ti
```

Notes:
- Each preset is an independent Google account setup with its own OAuth token, backup root, and registrations.
- `auth <client_secret_path>` is the canonical way to add a new Google account. It completes OAuth, discovers the account email, writes or updates the config entry, and prints the assigned preset.
- Normal app runs only use email-named tokens. Legacy token names are not read implicitly.
- `nav` uses `l` to enter directories or download a file to a temp path and open it through `handlers`, matching the `o` app's handler shape.
- `nav` uses `Enter` to download a file into the current working directory from which you launched `gdrive`.
- In normal `nav`, pressing `Enter` on a directory downloads it, extracts it into a normal directory in the current working directory, and removes the temporary `.zip`.
- `up <file_path> ...` opens the same navigator in upload-picker mode; press `Enter` on a directory to upload there, or on a file to upload into that file's parent directory.
- `handlers` use the same object form as `o`: `commands` is a list of commands to try, `{file}` is substituted if present, and `is_internal: true` runs the handler in the current terminal after suspending the TUI.
- `backup_root_name` is the single top-level Drive folder under `My Drive` that holds all managed backups for that preset.
- `drive_path` is always relative to that preset's backup root. Do not include the root itself in `reg`.
- `run` is global. It syncs every registration across every configured preset.
- `ls` prints each registration as a simple record and includes the Drive folder URL once the folder exists remotely.
- `-v` prints the installed app version from the app's runtime version module. Source checkouts may keep a placeholder value until release automation stamps the shipped artifact.
- The local folder is authoritative. If you remove a local file, the matching Drive file is removed on the next sync.
- If a file is renamed locally without content changes, the CLI attempts to propagate it as a Drive rename/move by matching the prior snapshot.
- Remote files created manually inside a managed Drive folder are deleted on the next sync if they do not exist locally.

## Timer

`ti` writes one global user service to `~/.config/systemd/user/` and enables an hourly timer that runs `gdrive run` across all presets. On success, the service sends a desktop notification through `notify-send` for Mako.

```bash
python main.py ti
systemctl --user list-timers gdrive.timer
```

## Manual test checklist

1. Run an interactive command and enter the client secret path and backup root when prompted.
2. Register a small local test directory.
3. Run `run` and complete OAuth in the browser.
4. Verify files appear in `My Drive/<backup_root_name>/...`.
5. Rename a local file and run `run` again.
6. Delete a local file and run `run` again.
7. Create a remote-only file in the managed Drive folder and verify the next `run` removes it.
