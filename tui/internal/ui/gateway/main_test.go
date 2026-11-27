// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMain(m *testing.M) {
	// A developer's own Local only must not change what these tests send.
	os.Setenv("GAIA_POLICY_FILE", filepath.Join(os.TempDir(), "gaia-gateway-test-no-policy.json"))
	// gateway.json and config.json follow GAIA_HOME, so no test may reach the
	// developer's own GAIA folder; a test that needs another one sets it itself.
	home, err := os.MkdirTemp("", "gaia-gateway-test-home-")
	if err != nil {
		panic(err)
	}
	os.Setenv("GAIA_HOME", home)
	for _, name := range []string{"GAIA_CONFIG_DIR", "GAIA_CONFIG_FILE", "GAIA_GATEWAY_FILE"} {
		os.Unsetenv(name)
	}
	code := m.Run()
	_ = os.RemoveAll(home)
	os.Exit(code)
}
