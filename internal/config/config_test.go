package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestAddListRemoveRegistration(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	t.Setenv("XDG_DATA_HOME", filepath.Join(home, "data"))
	local := filepath.Join(home, "Documents")
	if err := os.MkdirAll(local, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := SetBackupRootName("1", "Backups"); err != nil {
		t.Fatal(err)
	}
	reg, err := AddRegistration("1", local, "Documents")
	if err != nil {
		t.Fatal(err)
	}
	if reg.ID != "1" || reg.DrivePath != "Documents" {
		t.Fatalf("registration = %#v", reg)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	account, err := GetAccount(cfg, "1")
	if err != nil {
		t.Fatal(err)
	}
	if len(account.Registrations) != 1 {
		t.Fatalf("registrations = %d", len(account.Registrations))
	}
	removed, err := RemoveRegistration("1", "1")
	if err != nil {
		t.Fatal(err)
	}
	if removed.LocalDir != reg.LocalDir {
		t.Fatalf("removed = %#v", removed)
	}
}

func TestRelativeDrivePathRejectsBackupRootPrefix(t *testing.T) {
	if _, err := NormalizeRelativeDrivePath("Backups/Documents", "Backups"); err == nil {
		t.Fatal("expected error")
	}
}
