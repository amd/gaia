// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiainit

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSetupFollowsTheProfileThisPCWasSetUpWith(t *testing.T) {
	home := t.TempDir()
	t.Setenv("GAIA_CONFIG_FILE", "")
	t.Setenv("GAIA_CONFIG_DIR", "")
	t.Setenv("GAIA_HOME", home)
	if got := ConfiguredProfile(); got != Profile {
		t.Fatalf("a fresh PC sets up %q, got %q", Profile, got)
	}
	write := func(body string) {
		if err := os.WriteFile(filepath.Join(home, "config.json"), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(`{"profile": "npu", "default_model": "gemma4-it-e4b-FLM"}`)
	if got := CheckArgs(false); got[3] != NPUProfile {
		t.Fatalf("an NPU PC must be checked against the npu profile: %v", got)
	}
	if got := RunArgs(false); got[2] != NPUProfile {
		t.Fatalf("an NPU PC must be set up with the npu profile: %v", got)
	}
	write(`{"profile": "chat"}`)
	if got := ConfiguredProfile(); got != Profile {
		t.Fatalf("any other saved profile keeps the flagship's, got %q", got)
	}
	write(`not json`)
	if got := ConfiguredProfile(); got != Profile {
		t.Fatalf("an unreadable config is gaia init's to report, got %q", got)
	}
}
