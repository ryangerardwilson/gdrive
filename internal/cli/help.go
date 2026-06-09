package cli

const HelpText = `gdrive

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
`
