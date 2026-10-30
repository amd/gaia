// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// Once setup installs the hub flagship, the TUI reaches it through the daemon.
// The sidecar there answers `/model <id>` as a question, so the opening restore
// turn always "failed": every launch printed "Couldn't restore your last model"
// and opened the provider picker, though the client was already sending the
// restored cloud model with every turn.
func relayedFlagship() catalog.Agent {
	return catalog.Agent{ID: catalog.FlagshipID, Name: "GAIA", Transport: catalog.TransportDaemon}
}

const gatewayModel = "amd.Gemma-4-31B"

func TestRelayedFlagshipRestoresACloudModelWithoutAModelTurn(t *testing.T) {
	agent := relayedFlagship()
	m := NewFlagshipModel(agent, false).
		WithSavedModel(preflight.LastModel{Provider: "amd", Model: gatewayModel})

	updated, _ := m.launchAgent(agent, true)
	m = updated.(FlagshipModel)

	// An armed restore turn leaves the header blank until the agent confirms.
	if h := headerOf(m); !strings.Contains(h, "AMD LLM Gateway · Gemma-4-31B (remote)") {
		t.Fatalf("header does not name the restored model: %q", h)
	}
}

func TestRelayedFlagshipRemembersAModelPickedAtTheGate(t *testing.T) {
	agent := relayedFlagship()
	var saved []string
	m := NewFlagshipModel(agent, false).
		WithModelMemory(func(provider, model string) error {
			saved = append(saved, provider+"/"+model)
			return nil
		})
	m.model = gatewayModel
	m = m.pickedAtGate(gatewayModel)

	updated, _ := m.launchAgent(agent, true)
	m = updated.(FlagshipModel)

	if len(saved) != 1 || saved[0] != "amd/"+gatewayModel {
		t.Fatalf("the gate pick was not remembered for the next launch: %v", saved)
	}
	if h := headerOf(m); !strings.Contains(h, "Gemma-4-31B (remote)") {
		t.Fatalf("header does not name the picked model: %q", h)
	}
}

// The subprocess flagship still confirms the restore with the agent.
func TestSubprocessFlagshipStillConfirmsTheRestoreWithTheAgent(t *testing.T) {
	agent := fixtureAgent(t, catalog.FlagshipID)
	var saved []string
	m := NewFlagshipModel(agent, false).
		WithModelMemory(func(_, model string) error { saved = append(saved, model); return nil }).
		WithSavedModel(preflight.LastModel{Provider: "amd", Model: gatewayModel})

	updated, _ := m.launchAgent(agent, true)
	m = updated.(FlagshipModel)

	if len(saved) != 0 {
		t.Fatalf("a restore was saved before the agent confirmed it: %v", saved)
	}
}
