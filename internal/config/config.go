package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/ryangerardwilson/gdrive/internal/paths"
)

type Registration struct {
	ID           string `json:"id"`
	LocalDir     string `json:"local_dir"`
	DrivePath    string `json:"drive_path"`
	RemoteRootID string `json:"remote_root_id"`
	Enabled      bool   `json:"enabled"`
}

type Account struct {
	Preset           string         `json:"-"`
	ClientSecretFile string         `json:"client_secret_file"`
	Email            string         `json:"email"`
	BackupRootName   string         `json:"backup_root_name"`
	Registrations    []Registration `json:"registrations"`
}

type HandlerSpec struct {
	Commands   [][]string `json:"commands"`
	IsInternal bool       `json:"is_internal"`
}

type AppConfig struct {
	Path     string                 `json:"-"`
	Accounts map[string]*Account    `json:"accounts"`
	Handlers map[string]HandlerSpec `json:"handlers"`
}

type rawConfig map[string]any

func Load() (*AppConfig, error) {
	if err := paths.EnsureDirs(); err != nil {
		return nil, err
	}
	configPath := paths.ConfigFile()
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		cfg := &AppConfig{Path: configPath, Accounts: map[string]*Account{}, Handlers: map[string]HandlerSpec{}}
		return cfg, Save(cfg)
	}
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, err
	}
	var raw rawConfig
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("invalid JSON in config %s: %w", configPath, err)
	}
	raw = migrateLegacy(raw)
	cfg := &AppConfig{Path: configPath, Accounts: map[string]*Account{}, Handlers: normalizeHandlers(raw["handlers"])}
	accountsRaw, _ := raw["accounts"].(map[string]any)
	for preset, value := range accountsRaw {
		key, err := NormalizePreset(preset)
		if err != nil {
			return nil, err
		}
		account, err := accountFromRaw(key, value)
		if err != nil {
			return nil, err
		}
		cfg.Accounts[key] = account
	}
	return cfg, nil
}

func Save(cfg *AppConfig) error {
	if err := os.MkdirAll(filepath.Dir(cfg.Path), 0o700); err != nil {
		return err
	}
	payload := map[string]any{
		"accounts": serializeAccounts(cfg.Accounts),
		"handlers": cfg.Handlers,
	}
	data, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(cfg.Path, data, 0o600)
}

func NormalizePreset(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return "", fmt.Errorf("preset must be numeric, like `1` or `2`")
	}
	if _, err := strconv.Atoi(value); err != nil {
		return "", fmt.Errorf("preset must be numeric, like `1` or `2`")
	}
	return value, nil
}

func NormalizeDrivePath(value string) (string, error) {
	parts := []string{}
	for _, part := range strings.Split(strings.ReplaceAll(strings.TrimSpace(value), "\\", "/"), "/") {
		part = strings.TrimSpace(part)
		if part != "" {
			parts = append(parts, part)
		}
	}
	if len(parts) == 0 {
		return "", fmt.Errorf("path cannot be empty")
	}
	return strings.Join(parts, "/"), nil
}

func NormalizeRelativeDrivePath(value, backupRootName string) (string, error) {
	drivePath, err := NormalizeDrivePath(value)
	if err != nil {
		return "", err
	}
	root, err := NormalizeDrivePath(backupRootName)
	if err != nil {
		return "", err
	}
	if drivePath == root || strings.HasPrefix(drivePath, root+"/") {
		return "", fmt.Errorf("drive path must be relative to backup root `%s`", root)
	}
	return drivePath, nil
}

func NormalizeEmail(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}

func EnsureAccount(cfg *AppConfig, preset string) (*Account, error) {
	key, err := NormalizePreset(preset)
	if err != nil {
		return nil, err
	}
	account := cfg.Accounts[key]
	if account == nil {
		account = &Account{Preset: key}
		cfg.Accounts[key] = account
	}
	return account, nil
}

func GetAccount(cfg *AppConfig, preset string) (*Account, error) {
	key, err := NormalizePreset(preset)
	if err != nil {
		return nil, err
	}
	account := cfg.Accounts[key]
	if account == nil {
		available := sortedKeys(cfg.Accounts)
		label := "none"
		if len(available) > 0 {
			label = strings.Join(available, ", ")
		}
		return nil, fmt.Errorf("preset `%s` not found. available presets: %s", key, label)
	}
	return account, nil
}

func RequireClientSecret(account *Account) (string, error) {
	if strings.TrimSpace(account.ClientSecretFile) == "" {
		return "", fmt.Errorf("preset `%s` is missing a client secret file", account.Preset)
	}
	return account.ClientSecretFile, nil
}

func RequireBackupRootName(account *Account) (string, error) {
	if strings.TrimSpace(account.BackupRootName) == "" {
		return "", fmt.Errorf("preset `%s` is missing a Drive backup root dir name", account.Preset)
	}
	return account.BackupRootName, nil
}

func SetClientSecret(preset, value string) (string, error) {
	path := paths.ExpandHome(value)
	info, err := os.Stat(path)
	if err != nil {
		return "", fmt.Errorf("missing client secret file: %s", path)
	}
	if info.IsDir() {
		return "", fmt.Errorf("client secret path is not a file: %s", path)
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	cfg, err := Load()
	if err != nil {
		return "", err
	}
	account, err := EnsureAccount(cfg, preset)
	if err != nil {
		return "", err
	}
	account.ClientSecretFile = abs
	return abs, Save(cfg)
}

func SetBackupRootName(preset, value string) (string, error) {
	normalized, err := NormalizeDrivePath(value)
	if err != nil {
		return "", err
	}
	cfg, err := Load()
	if err != nil {
		return "", err
	}
	account, err := EnsureAccount(cfg, preset)
	if err != nil {
		return "", err
	}
	account.BackupRootName = normalized
	return normalized, Save(cfg)
}

func UpsertAuthenticatedAccount(clientSecretFile, email, backupRootName string) (*Account, error) {
	abs, err := filepath.Abs(paths.ExpandHome(clientSecretFile))
	if err != nil {
		return nil, err
	}
	normalizedEmail := NormalizeEmail(email)
	normalizedRoot, err := NormalizeDrivePath(backupRootName)
	if err != nil {
		return nil, err
	}
	cfg, err := Load()
	if err != nil {
		return nil, err
	}
	for _, account := range cfg.Accounts {
		if NormalizeEmail(account.Email) == normalizedEmail && normalizedEmail != "" {
			account.ClientSecretFile = abs
			account.Email = normalizedEmail
			account.BackupRootName = normalizedRoot
			return account, Save(cfg)
		}
	}
	preset := nextPreset(cfg.Accounts)
	account := &Account{Preset: preset, ClientSecretFile: abs, Email: normalizedEmail, BackupRootName: normalizedRoot}
	cfg.Accounts[preset] = account
	return account, Save(cfg)
}

func AddRegistration(preset, localDir, drivePath string) (Registration, error) {
	cfg, err := Load()
	if err != nil {
		return Registration{}, err
	}
	account, err := EnsureAccount(cfg, preset)
	if err != nil {
		return Registration{}, err
	}
	if _, err := RequireBackupRootName(account); err != nil {
		return Registration{}, err
	}
	localPath := paths.ExpandHome(localDir)
	info, err := os.Stat(localPath)
	if err != nil || !info.IsDir() {
		return Registration{}, fmt.Errorf("missing local dir: %s", localPath)
	}
	abs, err := filepath.Abs(localPath)
	if err != nil {
		return Registration{}, err
	}
	normalizedDrive, err := NormalizeRelativeDrivePath(drivePath, account.BackupRootName)
	if err != nil {
		return Registration{}, err
	}
	for _, reg := range account.Registrations {
		if reg.LocalDir == abs {
			return Registration{}, fmt.Errorf("local dir already registered as id %s", reg.ID)
		}
		if reg.DrivePath == normalizedDrive {
			return Registration{}, fmt.Errorf("drive path already registered as id %s", reg.ID)
		}
	}
	reg := Registration{ID: nextRegistrationID(account.Registrations), LocalDir: abs, DrivePath: normalizedDrive, Enabled: true}
	account.Registrations = append(account.Registrations, reg)
	sortRegistrations(account.Registrations)
	return reg, Save(cfg)
}

func GetRegistration(preset, id string) (Registration, error) {
	cfg, err := Load()
	if err != nil {
		return Registration{}, err
	}
	account, err := GetAccount(cfg, preset)
	if err != nil {
		return Registration{}, err
	}
	for _, reg := range account.Registrations {
		if reg.ID == id {
			return reg, nil
		}
	}
	return Registration{}, fmt.Errorf("registration `%s` not found in preset `%s`", id, account.Preset)
}

func UpdateRegistration(preset string, updated Registration) error {
	cfg, err := Load()
	if err != nil {
		return err
	}
	account, err := GetAccount(cfg, preset)
	if err != nil {
		return err
	}
	for idx, reg := range account.Registrations {
		if reg.ID == updated.ID {
			account.Registrations[idx] = updated
			return Save(cfg)
		}
	}
	return fmt.Errorf("registration `%s` not found in preset `%s`", updated.ID, account.Preset)
}

func RemoveRegistration(preset, id string) (Registration, error) {
	cfg, err := Load()
	if err != nil {
		return Registration{}, err
	}
	account, err := GetAccount(cfg, preset)
	if err != nil {
		return Registration{}, err
	}
	for idx, reg := range account.Registrations {
		if reg.ID == id {
			account.Registrations = append(account.Registrations[:idx], account.Registrations[idx+1:]...)
			return reg, Save(cfg)
		}
	}
	return Registration{}, fmt.Errorf("registration `%s` not found in preset `%s`", id, account.Preset)
}

func accountFromRaw(preset string, value any) (*Account, error) {
	raw, _ := value.(map[string]any)
	account := &Account{Preset: preset}
	if raw == nil {
		return account, nil
	}
	account.ClientSecretFile = stringField(raw["client_secret_file"])
	if account.ClientSecretFile != "" {
		account.ClientSecretFile = paths.ExpandHome(account.ClientSecretFile)
	}
	account.Email = NormalizeEmail(stringField(raw["email"]))
	if root := stringField(raw["backup_root_name"]); root != "" {
		normalized, err := NormalizeDrivePath(root)
		if err != nil {
			return nil, err
		}
		account.BackupRootName = normalized
	}
	if values, ok := raw["registrations"].([]any); ok {
		for _, item := range values {
			reg := registrationFromRaw(item)
			if reg.ID != "" {
				account.Registrations = append(account.Registrations, reg)
			}
		}
	}
	sortRegistrations(account.Registrations)
	return account, nil
}

func registrationFromRaw(value any) Registration {
	raw, _ := value.(map[string]any)
	if raw == nil {
		return Registration{}
	}
	id := strings.TrimSpace(stringField(raw["id"]))
	localDir := strings.TrimSpace(stringField(raw["local_dir"]))
	drivePath := strings.TrimSpace(stringField(raw["drive_path"]))
	if id == "" || localDir == "" || drivePath == "" {
		return Registration{}
	}
	normalizedDrive, err := NormalizeDrivePath(drivePath)
	if err != nil {
		return Registration{}
	}
	enabled := true
	if value, ok := raw["enabled"].(bool); ok {
		enabled = value
	}
	abs, _ := filepath.Abs(paths.ExpandHome(localDir))
	return Registration{
		ID:           id,
		LocalDir:     abs,
		DrivePath:    normalizedDrive,
		RemoteRootID: strings.TrimSpace(stringField(raw["remote_root_id"])),
		Enabled:      enabled,
	}
}

func normalizeHandlers(value any) map[string]HandlerSpec {
	handlers := map[string]HandlerSpec{}
	raw, ok := value.(map[string]any)
	if !ok {
		return handlers
	}
	for key, item := range raw {
		name := strings.TrimSpace(key)
		if name == "" {
			continue
		}
		spec := HandlerSpec{}
		if object, ok := item.(map[string]any); ok {
			spec.Commands = normalizeCommands(object["commands"])
			if len(spec.Commands) == 0 {
				spec.Commands = normalizeCommands(object["command"])
			}
			if internal, ok := object["is_internal"].(bool); ok {
				spec.IsInternal = internal
			}
		} else {
			spec.Commands = normalizeCommands(item)
		}
		if len(spec.Commands) > 0 {
			handlers[name] = spec
		}
	}
	return handlers
}

func normalizeCommands(value any) [][]string {
	switch typed := value.(type) {
	case string:
		fields := strings.Fields(typed)
		if len(fields) == 0 {
			return nil
		}
		return [][]string{fields}
	case []any:
		if allStrings(typed) {
			command := []string{}
			for _, item := range typed {
				if token := strings.TrimSpace(item.(string)); token != "" {
					command = append(command, token)
				}
			}
			if len(command) > 0 {
				return [][]string{command}
			}
			return nil
		}
		commands := [][]string{}
		for _, item := range typed {
			commands = append(commands, normalizeCommands(item)...)
		}
		return commands
	default:
		return nil
	}
}

func migrateLegacy(raw rawConfig) rawConfig {
	if accounts, ok := raw["accounts"].(map[string]any); ok && accounts != nil {
		return raw
	}
	return rawConfig{
		"handlers": raw["handlers"],
		"accounts": map[string]any{
			"1": map[string]any{
				"client_secret_file": raw["client_secret_file"],
				"backup_root_name":   raw["backup_root_name"],
				"registrations":      raw["registrations"],
			},
		},
	}
}

func serializeAccounts(accounts map[string]*Account) map[string]any {
	payload := map[string]any{}
	for _, key := range sortedKeys(accounts) {
		account := accounts[key]
		regs := append([]Registration(nil), account.Registrations...)
		sortRegistrations(regs)
		payload[key] = map[string]any{
			"client_secret_file": account.ClientSecretFile,
			"email":              account.Email,
			"backup_root_name":   account.BackupRootName,
			"registrations":      regs,
		}
	}
	return payload
}

func sortedKeys(accounts map[string]*Account) []string {
	keys := make([]string, 0, len(accounts))
	for key := range accounts {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		left, leftErr := strconv.Atoi(keys[i])
		right, rightErr := strconv.Atoi(keys[j])
		if leftErr == nil && rightErr == nil {
			return left < right
		}
		return keys[i] < keys[j]
	})
	return keys
}

func sortRegistrations(regs []Registration) {
	sort.SliceStable(regs, func(i, j int) bool {
		left, leftErr := strconv.Atoi(regs[i].ID)
		right, rightErr := strconv.Atoi(regs[j].ID)
		if leftErr == nil && rightErr == nil {
			return left < right
		}
		return regs[i].ID < regs[j].ID
	})
}

func nextPreset(accounts map[string]*Account) string {
	maxID := 0
	for key := range accounts {
		if value, err := strconv.Atoi(key); err == nil && value > maxID {
			maxID = value
		}
	}
	return strconv.Itoa(maxID + 1)
}

func nextRegistrationID(regs []Registration) string {
	maxID := 0
	for _, reg := range regs {
		if value, err := strconv.Atoi(reg.ID); err == nil && value > maxID {
			maxID = value
		}
	}
	return strconv.Itoa(maxID + 1)
}

func stringField(value any) string {
	if value == nil {
		return ""
	}
	return strings.TrimSpace(fmt.Sprint(value))
}

func allStrings(values []any) bool {
	if len(values) == 0 {
		return false
	}
	for _, value := range values {
		if _, ok := value.(string); !ok {
			return false
		}
	}
	return true
}
