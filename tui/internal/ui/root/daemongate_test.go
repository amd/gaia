// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// The daemon gate may skip the local chat model only for a sidecar that runs
// the launch model. The flagship's does; email's takes no per-turn model.
func TestDaemonGateIsToldTheCloudModelOnlyForTheFlagship(t *testing.T) {
	flagship := catalog.Agent{ID: catalog.FlagshipID, Name: "GAIA", Transport: catalog.TransportDaemon}
	m := NewFlagshipModel(flagship, false).
		WithSavedModel(preflight.LastModel{Provider: "amd", Model: "amd.Gemma-4-31B"})
	updated, _ := m.beginPreflight(flagship)
	if got := updated.(FlagshipModel).preflight.LaunchModel(); got != "amd.Gemma-4-31B" {
		t.Fatalf("flagship gate checks %q, want the restored cloud model", got)
	}

	email := catalog.Agent{ID: "email", Name: "Email", Transport: catalog.TransportDaemon}
	m = NewFlagshipModel(email, false)
	m.model = "amd.Gemma-4-31B" // a gate pick sets this whatever the agent
	updated, _ = m.beginPreflight(email)
	if got := updated.(FlagshipModel).preflight.LaunchModel(); got != "" {
		t.Fatalf("email gate was told %q; its sidecar ignores a per-turn model", got)
	}
}
