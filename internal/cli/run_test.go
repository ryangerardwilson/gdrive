package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ryangerardwilson/gdrive/internal/version"
)

func TestNoArgsMatchesHelp(t *testing.T) {
	var noArgs bytes.Buffer
	var help bytes.Buffer
	if code := Run(nil, &noArgs, &bytes.Buffer{}); code != 0 {
		t.Fatalf("no args code = %d", code)
	}
	if code := Run([]string{"help"}, &help, &bytes.Buffer{}); code != 0 {
		t.Fatalf("help code = %d", code)
	}
	if noArgs.String() != help.String() {
		t.Fatalf("no args output differs from help")
	}
	if !strings.Contains(help.String(), "features:") || !strings.Contains(help.String(), "# gdrive auth <client_secret_path>") {
		t.Fatalf("help missing expected text:\n%s", help.String())
	}
	if strings.Contains(help.String(), "commands:") || strings.Contains(help.String(), "usage:") {
		t.Fatalf("help should avoid commands/usage headings:\n%s", help.String())
	}
}

func TestVersionPrintsSingleLine(t *testing.T) {
	var out bytes.Buffer
	if code := Run([]string{"version"}, &out, &bytes.Buffer{}); code != 0 {
		t.Fatalf("version code = %d", code)
	}
	if out.String() != version.Version+"\n" {
		t.Fatalf("version output = %q", out.String())
	}
}

func TestConfigOpensEditorAndCreatesFile(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	t.Setenv("XDG_DATA_HOME", filepath.Join(home, "data"))
	t.Setenv("EDITOR", "/usr/bin/true")
	if code := Run([]string{"config"}, &bytes.Buffer{}, &bytes.Buffer{}); code != 0 {
		t.Fatalf("config code = %d", code)
	}
	if _, err := os.Stat(filepath.Join(home, "config", "gdrive", "config.json")); err != nil {
		t.Fatal(err)
	}
}

func TestUpgradeUsesOverrideScript(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "marker")
	script := filepath.Join(dir, "install.sh")
	if err := os.WriteFile(script, []byte("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > \"$GDRIVE_MARKER\"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GDRIVE_INSTALL_SCRIPT", script)
	t.Setenv("GDRIVE_MARKER", marker)
	if code := Run([]string{"upgrade"}, &bytes.Buffer{}, &bytes.Buffer{}); code != 0 {
		t.Fatalf("upgrade code = %d", code)
	}
	data, err := os.ReadFile(marker)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(data)) != "upgrade" {
		t.Fatalf("marker = %q", string(data))
	}
}
