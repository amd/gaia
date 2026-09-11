package test

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestInstalledFlagshipRejectsBypassBeforeReadiness(t *testing.T) {
	bin, _ := buildBinaries(t)
	home := t.TempDir()
	installed := filepath.Join(home, ".gaia", "agents", "gaia")
	if err := os.MkdirAll(installed, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(installed, ".installed"), []byte(`{"id":"gaia","version":"0.1.1","artifact_kind":"binary"}`), 0600); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"--bypass-permissions"},
		{"run", "gaia", "--bypass-permissions", "--query", "hello"},
		{"run", "email", "--bypass-permissions", "--query", "hello"},
		{"chat", "--subprocess", "missing-agent-binary", "--bypass-permissions", "--query", "hello"},
	} {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		cmd := exec.CommandContext(ctx, bin, args...)
		cmd.Env = append(os.Environ(), "HOME="+home, "USERPROFILE="+home)
		output, err := cmd.CombinedOutput()
		cancel()
		if err == nil || !strings.Contains(string(output), "--bypass-permissions is not supported") {
			t.Fatalf("%v did not reject bypass: %v %s", args, err, output)
		}
		if strings.Contains(string(output), altScreenEnter) {
			t.Fatal("invalid flag opened the UI")
		}
		if _, err := os.Stat(filepath.Join(home, ".gaia", "daemon", "instance.json")); !os.IsNotExist(err) {
			t.Fatalf("invalid option launched a daemon: %v", err)
		}
	}
}
