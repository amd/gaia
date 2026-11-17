// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/hybridapi/hybridapitest"
)

func TestMain(m *testing.M) {
	// A developer's own Local only must not change what these tests send.
	os.Setenv("GAIA_POLICY_FILE", filepath.Join(os.TempDir(), "gaia-gateway-test-no-policy.json"))
	// Nor reach a daemon for hybrid's entry.
	newHybridAPI = func() hybridapi.API { return &hybridapitest.Fake{EntryErr: errors.New("test: no daemon")} }
	os.Exit(m.Run())
}
