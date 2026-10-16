// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"os"
	"testing"

	"github.com/amd/gaia/tui/internal/control"
)

// TestMain points the session log at a temp dir: transport diagnostics are
// always written there, and a test run must not append to the developer's own
// ~/.gaia/logs/gaia-tui.log.
func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "gaia-tui-log-")
	if err != nil {
		panic(err)
	}
	os.Setenv(control.EnvHome, dir)
	code := m.Run()
	control.CloseLog()
	os.RemoveAll(dir)
	os.Exit(code)
}
