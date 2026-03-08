# gdrive

Google Drive backup CLI with local-first sync semantics.

## Install

```bash
cd ~/Apps/gdrive
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py -h
```

Release install:

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/gdrive/main/install.sh | bash
```

## Google OAuth setup

1. Open Google Cloud Console.
2. Create or select a project.
3. Enable the Google Drive API.
4. Configure the OAuth consent screen.
5. Create an `OAuth client ID` with application type `Desktop app`.
6. Download the client JSON.
The first interactive command asks for:
- the client secret JSON path
- the Google Drive folder name that should hold all managed backups

The first authenticated sync opens a browser and stores the refresh token locally.

## Storage

- Config: `~/.config/gdrive/config.json`
- Data: `~/.local/share/gdrive/`
- OAuth token: `~/.local/share/gdrive/token.json`
- Per-registration sync state: `~/.local/share/gdrive/state/<id>.json`

Example config:

```json
{
  "client_secret_file": "/home/ryan/.config/gdrive/client_secret.json",
  "backup_root_name": "Backups",
  "registrations": []
}
```

## Usage

```bash
gdrive
gdrive -h
gdrive -v
gdrive -u
gdrive reg <local_dir> <drive_path>
gdrive ls
gdrive run
gdrive run <id>
gdrive rm <id>
gdrive ti
gdrive td
gdrive st
```

Examples:

```bash
python main.py reg ~/Documents "Documents"
python main.py reg ~/Pictures "Pictures"
python main.py -v
python main.py ls
python main.py run
python main.py run 1
python main.py ti
```

Notes:
- `backup_root_name` is the single top-level Drive folder under `My Drive` that holds all managed backups.
- `drive_path` is always relative to that backup root. Do not include the root itself in `reg`.
- `ls` prints each registration as a simple record and includes the Drive folder URL once the folder exists remotely.
- The local folder is authoritative. If you remove a local file, the matching Drive file is removed on the next sync.
- If a file is renamed locally without content changes, the CLI attempts to propagate it as a Drive rename/move by matching the prior snapshot.
- Remote files created manually inside a managed Drive folder are deleted on the next sync if they do not exist locally.

## Timer

`ti` writes user service files to `~/.config/systemd/user/` and enables an hourly timer.

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
