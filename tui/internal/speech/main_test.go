// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// TestMain keeps these tests off the developer's own GAIA home and Lemonade.
func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "gaia-speech-test-")
	if err != nil {
		panic(err)
	}
	os.Setenv("GAIA_HOME", home)
	os.Setenv("GAIA_POLICY_FILE", filepath.Join(home, "policy.json"))
	os.Setenv("LEMONADE_BASE_URL", "")
	os.Setenv("LEMONADE_API_KEY", "")
	os.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	os.Setenv("GAIA_CONFIG_FILE", "")
	os.Setenv("GAIA_CONFIG_DIR", "")
	lemonade.StubEnsureEmbeddedForTest(func(context.Context) error { return errors.New("test: no daemon") })
	code := m.Run()
	os.RemoveAll(home)
	os.Exit(code)
}
