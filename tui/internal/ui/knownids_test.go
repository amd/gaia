// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

// Asking to run an agent whose install record is unreadable must say why it
// is missing, not just list the agents that are there.
func TestAnUnknownAgentNamesAnUnreadableInstall(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, ".gaia", "agents", "email")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, catalog.SentinelName), []byte("{"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	if runtime.GOOS == "windows" {
		t.Setenv("USERPROFILE", home)
	}

	cat := catalog.NewCatalog()
	cat.LoadInstalledAgents()

	got := knownIDs(cat)
	if !strings.Contains(got, "'email' is not listed as installed") {
		t.Fatalf("knownIDs = %q", got)
	}
}
