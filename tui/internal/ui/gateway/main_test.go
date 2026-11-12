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
	os.Exit(m.Run())
}
