// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// TestMain keeps the package off the developer's own GAIA home and logs: a fake
// Lemonade on loopback would otherwise be taken for GAIA's server and its
// requests followed to the real one.
func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "gaia-chat-test-")
	if err != nil {
		panic(err)
	}
	os.Setenv("GAIA_HOME", home)
	// Diagnostics are always written to the session log; keep them off the
	// developer's own ~/.gaia/logs.
	os.Setenv(control.EnvHome, home)
	// The first screen's suggestions read installed skills; keep them off the
	// developer's own ~/.gaia/skills.
	skillsDir = func() string { return filepath.Join(home, "skills") }
	os.Setenv("LEMONADE_BASE_URL", "")
	lemonade.StubEnsureEmbeddedForTest(func(context.Context) error {
		return errors.New("test: no daemon")
	})
	code := m.Run()
	control.CloseLog()
	os.RemoveAll(home)
	os.Exit(code)
}
