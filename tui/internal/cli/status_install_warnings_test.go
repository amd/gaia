// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

// An install record GAIA cannot read must be named by `gaia tui status`, even
// with the background service down: otherwise the agent just disappears, which
// looks exactly like never having installed it.
func TestStatusNamesAnUnreadableInstallWithTheServiceDown(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, ".gaia", "agents", "notes")
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
	t.Setenv("GAIA_HOME", filepath.Join(home, ".gaia"))

	var out bytes.Buffer
	if err := printStatus(&out); err == nil {
		t.Fatal("status exited 0 with no background service")
	}

	got := out.String()
	if !strings.Contains(got, "Not listed:") ||
		!strings.Contains(got, "'notes' is not listed as installed") ||
		!strings.Contains(got, "reinstall it") {
		t.Fatalf("status did not name the unreadable install:\n%s", got)
	}
}
