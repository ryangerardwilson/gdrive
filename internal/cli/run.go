package cli

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/ryangerardwilson/gdrive/internal/auth"
	"github.com/ryangerardwilson/gdrive/internal/config"
	"github.com/ryangerardwilson/gdrive/internal/driveapi"
	"github.com/ryangerardwilson/gdrive/internal/paths"
	"github.com/ryangerardwilson/gdrive/internal/syncer"
	"github.com/ryangerardwilson/gdrive/internal/transfer"
	"github.com/ryangerardwilson/gdrive/internal/tui"
	"github.com/ryangerardwilson/gdrive/internal/version"
)

const installScriptURL = "https://raw.githubusercontent.com/ryangerardwilson/gdrive/main/install.sh"

func Run(args []string, stdout, stderr io.Writer) int {
	switch {
	case len(args) == 0 || sameArgs(args, "help"):
		fmt.Fprint(stdout, HelpText)
		return 0
	case sameArgs(args, "version"):
		fmt.Fprintln(stdout, version.Version)
		return 0
	case sameArgs(args, "upgrade"):
		return upgrade(stdout, stderr)
	case len(args) > 0 && isGlobalAction(args[0]):
		fmt.Fprintf(stderr, "usage: gdrive %s\n", args[0])
		return 2
	default:
		if err := dispatch(args, stdout, stderr); err != nil {
			fmt.Fprintln(stderr, err)
			return 2
		}
		return 0
	}
}

func dispatch(args []string, stdout, stderr io.Writer) error {
	command := args[0]
	params := args[1:]
	switch {
	case command == "auth":
		if len(params) != 1 {
			return fmt.Errorf("usage: gdrive auth <client_secret_path>")
		}
		return authAccount(params[0], stdout, stderr)
	case command == "config":
		if len(params) != 0 {
			return fmt.Errorf("usage: gdrive config")
		}
		return openConfig()
	case command == "sync":
		if sameArgs(params, "run") {
			return runSyncAll(stdout)
		}
		if sameArgs(params, "restore") {
			return runRestoreAll(stdout)
		}
		return fmt.Errorf("usage: gdrive sync run | gdrive sync restore")
	case command == "timer":
		if len(params) != 1 {
			return fmt.Errorf("usage: gdrive timer install|disable|status")
		}
		switch params[0] {
		case "install":
			return installTimer(stdout)
		case "disable":
			return disableTimer(stdout)
		case "status":
			return timerStatus(stdout)
		default:
			return fmt.Errorf("usage: gdrive timer install|disable|status")
		}
	case isNumeric(command):
		return dispatchPreset(command, params, stdout)
	default:
		return fmt.Errorf("unknown command `%s`", command)
	}
}

func dispatchPreset(preset string, params []string, stdout io.Writer) error {
	if len(params) == 0 {
		return fmt.Errorf("missing command: use `gdrive %s list registrations`", preset)
	}
	command := params[0]
	if command == "register" {
		if len(params) != 4 || params[2] != "as" {
			return fmt.Errorf("usage: gdrive <preset> register <local_dir> as <drive_path>")
		}
		if err := ensureSetup(preset, true, stdout); err != nil {
			return err
		}
		reg, err := config.AddRegistration(preset, params[1], params[3])
		if err != nil {
			return err
		}
		fmt.Fprintf(stdout, "registered\t%s\t%s\t%s\t%s\n", preset, reg.ID, reg.LocalDir, reg.DrivePath)
		return nil
	}
	if sameArgs(params, "list", "registrations") {
		if err := ensureSetup(preset, true, stdout); err != nil {
			return err
		}
		return printRegistrations(preset, stdout)
	}
	if len(params) >= 2 && params[0] == "remove" && params[1] == "registration" {
		if len(params) != 3 {
			return fmt.Errorf("usage: gdrive <preset> remove registration <id>")
		}
		if err := ensureSetup(preset, true, stdout); err != nil {
			return err
		}
		reg, err := config.RemoveRegistration(preset, params[2])
		if err != nil {
			return err
		}
		_ = syncer.DeleteState(preset, reg.ID)
		fmt.Fprintf(stdout, "removed\t%s\t%s\t%s\n", preset, reg.ID, reg.LocalDir)
		return nil
	}
	if sameArgs(params, "browse") {
		return runNav(preset)
	}
	if command == "upload" {
		return runUploadPicker(preset, params[1:], stdout)
	}
	if sameArgs(params, "restore", "registrations") {
		_, err := restoreAccountRegistrations(preset, "", stdout)
		return err
	}
	if len(params) >= 2 && params[0] == "restore" && params[1] == "registration" {
		if len(params) != 3 {
			return fmt.Errorf("usage: gdrive <preset> restore registration <id>")
		}
		_, err := restoreAccountRegistrations(preset, params[2], stdout)
		return err
	}
	return fmt.Errorf("unknown command `%s`", command)
}

func authAccount(clientSecretPath string, stdout, stderr io.Writer) error {
	path := paths.ExpandHome(clientSecretPath)
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return fmt.Errorf("missing client secret file: %s", path)
	}
	root, err := prompt("Drive backup root dir name: ", stdout)
	if err != nil {
		return err
	}
	for strings.TrimSpace(root) == "" {
		fmt.Fprintln(stderr, "enter a folder name like `Backups` or `ComputerBackups`")
		root, err = prompt("Drive backup root dir name: ", stdout)
		if err != nil {
			return err
		}
	}
	_, email, err := auth.AuthorizeAccount(path, os.Stdin, os.Stdout, os.Stderr)
	if err != nil {
		return err
	}
	account, err := config.UpsertAuthenticatedAccount(path, email, root)
	if err != nil {
		return err
	}
	fmt.Fprintf(stdout, "authorized\t%s\t%s\t%s\n", account.Preset, account.Email, account.BackupRootName)
	return nil
}

func ensureClientSecret(preset string, interactive bool, stdout io.Writer) (string, error) {
	cfg, err := config.Load()
	if err != nil {
		return "", err
	}
	account, err := config.EnsureAccount(cfg, preset)
	if err != nil {
		return "", err
	}
	if account.ClientSecretFile != "" {
		return config.RequireClientSecret(account)
	}
	if !interactive {
		return "", fmt.Errorf("missing client secret in config for preset `%s`: run `gdrive auth <client_secret_path>` first", preset)
	}
	value, err := prompt(fmt.Sprintf("Preset %s Google client secret file path: ", preset), stdout)
	if err != nil {
		return "", err
	}
	return config.SetClientSecret(preset, value)
}

func ensureBackupRootName(preset string, interactive bool, stdout io.Writer) (string, error) {
	cfg, err := config.Load()
	if err != nil {
		return "", err
	}
	account, err := config.EnsureAccount(cfg, preset)
	if err != nil {
		return "", err
	}
	if account.BackupRootName != "" {
		return config.RequireBackupRootName(account)
	}
	if !interactive {
		return "", fmt.Errorf("missing backup root in config for preset `%s`: run `gdrive %s list registrations` interactively first", preset, preset)
	}
	value, err := prompt(fmt.Sprintf("Preset %s Drive backup root dir name: ", preset), stdout)
	if err != nil {
		return "", err
	}
	return config.SetBackupRootName(preset, value)
}

func ensureSetup(preset string, interactive bool, stdout io.Writer) error {
	if _, err := ensureClientSecret(preset, interactive, stdout); err != nil {
		return err
	}
	_, err := ensureBackupRootName(preset, interactive, stdout)
	return err
}

func printRegistrations(preset string, stdout io.Writer) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	account, err := config.GetAccount(cfg, preset)
	if err != nil {
		return err
	}
	root, err := config.RequireBackupRootName(account)
	if err != nil {
		return err
	}
	if len(account.Registrations) == 0 {
		fmt.Fprintln(stdout, "no registrations")
		return nil
	}
	for idx, reg := range account.Registrations {
		url := "-"
		if reg.RemoteRootID != "" {
			url = "https://drive.google.com/drive/folders/" + reg.RemoteRootID
		}
		prefix := fmt.Sprintf("[%d]", idx+1)
		fmt.Fprintln(stdout, prefix+strings.Repeat("-", max(1, 79-len(prefix))))
		fmt.Fprintf(stdout, "edit_id : %s\n", reg.ID)
		fmt.Fprintf(stdout, "local   : %s\n", reg.LocalDir)
		fmt.Fprintf(stdout, "drive   : %s/%s\n", root, reg.DrivePath)
		fmt.Fprintln(stdout, url)
	}
	return nil
}

func driveClient(preset string) (*driveapi.DriveClient, *config.Account, error) {
	cfg, err := config.Load()
	if err != nil {
		return nil, nil, err
	}
	account, err := config.GetAccount(cfg, preset)
	if err != nil {
		return nil, nil, err
	}
	if _, err := config.RequireClientSecret(account); err != nil {
		return nil, nil, err
	}
	client, err := driveapi.New(context.Background(), account)
	return client, account, err
}

func runNav(preset string) error {
	if _, err := ensureClientSecret(preset, true, os.Stdout); err != nil {
		return err
	}
	client, _, err := driveClient(preset)
	if err != nil {
		return err
	}
	_, err = tui.Browse(client, mustGetwd())
	return err
}

func runUploadPicker(preset string, values []string, stdout io.Writer) error {
	if _, err := ensureClientSecret(preset, true, stdout); err != nil {
		return err
	}
	uploadPaths, err := transfer.NormalizeUploadPaths(values)
	if err != nil {
		return err
	}
	client, _, err := driveClient(preset)
	if err != nil {
		return err
	}
	result, _, err := tui.Upload(client, mustGetwd(), uploadPaths)
	if err != nil {
		return err
	}
	if result.UploadSummary == nil {
		fmt.Fprintln(stdout, "cancelled")
		return nil
	}
	fmt.Fprintf(stdout, "uploaded\tfiles=%d\tdirs=%d\ttarget=%s\n", result.UploadSummary.FilesUploaded, result.UploadSummary.DirsCreated, result.UploadTarget)
	return nil
}

func runSyncAll(stdout io.Writer) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	didWork := false
	for _, preset := range sortedAccountKeys(cfg.Accounts) {
		account := cfg.Accounts[preset]
		if len(account.Registrations) == 0 {
			continue
		}
		if _, err := config.RequireClientSecret(account); err != nil {
			return err
		}
		root, err := config.RequireBackupRootName(account)
		if err != nil {
			return err
		}
		client, err := driveapi.New(context.Background(), account)
		if err != nil {
			return err
		}
		didWork = true
		for idx := range account.Registrations {
			reg := account.Registrations[idx]
			summary, err := syncer.SyncRegistration(preset, &reg, client, root)
			if err != nil {
				return err
			}
			if err := config.UpdateRegistration(preset, reg); err != nil {
				return err
			}
			fmt.Fprintf(stdout, "%s:%s\tcreated=%d\tupdated=%d\tmoved=%d\tdeleted=%d\n", preset, reg.ID, summary.Created, summary.Updated, summary.Moved, summary.Deleted)
		}
	}
	if !didWork {
		return fmt.Errorf("no registrations")
	}
	return nil
}

func runRestoreAll(stdout io.Writer) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	didWork := false
	for _, preset := range sortedAccountKeys(cfg.Accounts) {
		account := cfg.Accounts[preset]
		if len(account.Registrations) == 0 {
			continue
		}
		worked, err := restoreAccountRegistrations(preset, "", stdout)
		if err != nil {
			return err
		}
		didWork = didWork || worked
	}
	if !didWork {
		return fmt.Errorf("no registrations")
	}
	return nil
}

func restoreAccountRegistrations(preset, registrationID string, stdout io.Writer) (bool, error) {
	cfg, err := config.Load()
	if err != nil {
		return false, err
	}
	account, err := config.GetAccount(cfg, preset)
	if err != nil {
		return false, err
	}
	regs := account.Registrations
	if registrationID != "" {
		reg, err := config.GetRegistration(preset, registrationID)
		if err != nil {
			return false, err
		}
		regs = []config.Registration{reg}
	}
	if len(regs) == 0 {
		return false, nil
	}
	if _, err := config.RequireClientSecret(account); err != nil {
		return false, err
	}
	root, err := config.RequireBackupRootName(account)
	if err != nil {
		return false, err
	}
	client, err := driveapi.New(context.Background(), account)
	if err != nil {
		return false, err
	}
	didWork := false
	for _, reg := range regs {
		if !reg.Enabled {
			continue
		}
		summary, err := syncer.RestoreRegistrationFromRemote(preset, &reg, client, root)
		if err != nil {
			return false, err
		}
		if err := config.UpdateRegistration(preset, reg); err != nil {
			return false, err
		}
		didWork = true
		fmt.Fprintf(stdout, "%s:%s\tdownloaded=%d\tdirs_created=%d\tskipped_existing=%d\tstate_entries=%d\n", preset, reg.ID, summary.Downloaded, summary.DirsCreated, summary.SkippedExisting, summary.StateEntries)
	}
	return didWork, nil
}

func openConfig() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	editor := os.Getenv("VISUAL")
	if editor == "" {
		editor = os.Getenv("EDITOR")
	}
	if editor == "" {
		editor = "vim"
	}
	parts := strings.Fields(editor)
	if len(parts) == 0 {
		parts = []string{"vim"}
	}
	cmd := exec.Command(parts[0], append(parts[1:], cfg.Path)...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func unitName() string {
	return "gdrive"
}

func buildRuntimeCommand(args ...string) string {
	exe, err := os.Executable()
	if err != nil {
		exe = "gdrive"
	}
	parts := []string{shellQuote(exe)}
	for _, arg := range args {
		parts = append(parts, shellQuote(arg))
	}
	return strings.Join(parts, " ")
}

func notificationShellFunction() string {
	return strings.Join([]string{
		"notify() {",
		`summary="$1";`,
		`body="${2:-}";`,
		`urgency="${3:-normal}";`,
		`qs="${XDG_CONFIG_HOME:-$HOME/.config}/quickshell/omarchy-bar";`,
		`if command -v quickshell >/dev/null 2>&1 && quickshell ipc -p "$qs" call bar notify "$summary" "$body" "$urgency" >/dev/null 2>&1; then return 0; fi;`,
		`if command -v notify-send >/dev/null 2>&1; then notify-send -a "$summary" -u "$urgency" "$summary" "$body" || true; fi;`,
		"};",
	}, " ")
}

func buildTimerServiceScript(runCommand string) string {
	return strings.Join([]string{
		notificationShellFunction(),
		"notify 'gdrive' 'Hourly backup started' normal;",
		"if " + runCommand + "; then",
		"notify 'gdrive' 'Hourly backup finished successfully' normal;",
		"else",
		"rc=$?;",
		"notify 'gdrive' 'Hourly backup failed' critical;",
		`exit "$rc";`,
		"fi",
	}, " ")
}

func writeTimerUnits() error {
	if err := paths.EnsureDirs(); err != nil {
		return err
	}
	home := os.Getenv("HOME")
	if home == "" {
		home, _ = os.UserHomeDir()
	}
	systemdDir := filepath.Join(home, ".config", "systemd", "user")
	if err := os.MkdirAll(systemdDir, 0o755); err != nil {
		return err
	}
	servicePath := filepath.Join(systemdDir, unitName()+".service")
	timerPath := filepath.Join(systemdDir, unitName()+".timer")
	runCommand := buildRuntimeCommand("sync", "run")
	serviceScript := buildTimerServiceScript(runCommand)
	workingDir := filepath.Dir(mustExecutable())
	serviceBody := strings.Join([]string{
		"[Unit]",
		"Description=gdrive sync all presets",
		"",
		"[Service]",
		"Type=oneshot",
		"WorkingDirectory=" + workingDir,
		"ExecStart=/usr/bin/env bash -lc " + shellQuote(serviceScript),
		"",
	}, "\n")
	timerBody := strings.Join([]string{
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
	}, "\n")
	if err := os.WriteFile(servicePath, []byte(serviceBody), 0o644); err != nil {
		return err
	}
	return os.WriteFile(timerPath, []byte(timerBody), 0o644)
}

func systemctlUser(args ...string) ([]byte, error) {
	cmd := exec.Command("systemctl", append([]string{"--user"}, args...)...)
	return cmd.CombinedOutput()
}

func installTimer(stdout io.Writer) error {
	if err := writeTimerUnits(); err != nil {
		return err
	}
	for _, args := range [][]string{{"daemon-reload"}, {"enable", unitName() + ".timer"}, {"restart", unitName() + ".timer"}} {
		if out, err := systemctlUser(args...); err != nil {
			return fmt.Errorf("systemctl failed: %s", strings.TrimSpace(string(out)))
		}
	}
	fmt.Fprintf(stdout, "timer enabled: %s.timer\n", unitName())
	return nil
}

func disableTimer(stdout io.Writer) error {
	if err := writeTimerUnits(); err != nil {
		return err
	}
	if out, err := systemctlUser("disable", "--now", unitName()+".timer"); err != nil {
		return fmt.Errorf("systemctl failed: %s", strings.TrimSpace(string(out)))
	}
	fmt.Fprintf(stdout, "timer disabled: %s.timer\n", unitName())
	return nil
}

func timerStatus(stdout io.Writer) error {
	out, err := systemctlUser("status", unitName()+".timer")
	if err != nil {
		return fmt.Errorf("systemctl failed: %s", strings.TrimSpace(string(out)))
	}
	fmt.Fprintln(stdout, strings.TrimSpace(string(out)))
	return nil
}

func upgrade(stdout, stderr io.Writer) int {
	if override := os.Getenv("GDRIVE_INSTALL_SCRIPT"); override != "" {
		cmd := exec.Command("bash", override, "upgrade")
		cmd.Stdout = stdout
		cmd.Stderr = stderr
		cmd.Stdin = os.Stdin
		return runCmd(cmd, stderr)
	}
	cmd := exec.Command("bash", "-c", "curl -fsSL "+installScriptURL+" | bash -s -- upgrade")
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	cmd.Stdin = os.Stdin
	return runCmd(cmd, stderr)
}

func runCmd(cmd *exec.Cmd, stderr io.Writer) int {
	if err := cmd.Run(); err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			return exitErr.ExitCode()
		}
		fmt.Fprintln(stderr, err)
		return 1
	}
	return 0
}

func prompt(label string, stdout io.Writer) (string, error) {
	fmt.Fprint(stdout, label)
	reader := bufio.NewReader(os.Stdin)
	value, err := reader.ReadString('\n')
	if err != nil && len(value) == 0 {
		return "", err
	}
	return strings.TrimSpace(value), nil
}

func sameArgs(args []string, want ...string) bool {
	if len(args) != len(want) {
		return false
	}
	for idx := range args {
		if args[idx] != want[idx] {
			return false
		}
	}
	return true
}

func isGlobalAction(value string) bool {
	return value == "help" || value == "version" || value == "upgrade"
}

func isNumeric(value string) bool {
	_, err := strconv.Atoi(value)
	return err == nil
}

func sortedAccountKeys(accounts map[string]*config.Account) []string {
	keys := make([]string, 0, len(accounts))
	for key := range accounts {
		keys = append(keys, key)
	}
	sort.SliceStable(keys, func(i, j int) bool {
		left, leftErr := strconv.Atoi(keys[i])
		right, rightErr := strconv.Atoi(keys[j])
		if leftErr == nil && rightErr == nil {
			return left < right
		}
		return keys[i] < keys[j]
	})
	return keys
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func mustGetwd() string {
	wd, err := os.Getwd()
	if err != nil {
		return "."
	}
	return wd
}

func mustExecutable() string {
	exe, err := os.Executable()
	if err != nil {
		return "gdrive"
	}
	return exe
}

func shellQuote(value string) string {
	if value == "" {
		return "''"
	}
	if !strings.ContainsAny(value, " \t\n'\"\\$`!*?[]{}();&|<>") {
		return value
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}
