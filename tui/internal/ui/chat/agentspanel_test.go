// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package chat

import (
	"context"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/agents"
)

// stubHubLister is a minimal agents.HubAgentLister for driving /agents
// without a real daemon.
type stubHubLister struct{}

func (stubHubLister) Catalog(ctx context.Context, start, installedOnly, refresh bool) (*catalog.HubCatalog, error) {
	return &catalog.HubCatalog{Agents: []catalog.HubEntry{
		{ID: "gaia", Name: "GAIA", InstalledVersion: "0.2.0"},
		{ID: "email", Name: "Email", InstalledVersion: "0.1.3"},
	}}, nil
}
func (stubHubLister) Agents(ctx context.Context, start bool) ([]catalog.AgentRuntime, error) {
	return nil, nil
}

func TestAgentsCommandRefusesWithoutAHubClient(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.submit("/agents")
	m = updated.(ChatModel)

	if m.agentsPanel != nil {
		t.Fatal("no hub client was wired in — the panel must not open")
	}
	if len(m.messages) == 0 || m.messages[len(m.messages)-1].Role != RoleError {
		t.Fatal("expected an actionable RoleError, got nothing")
	}
}

func TestAgentsCommandOpensThePanelWhenWired(t *testing.T) {
	m, _ := newTestModel(t)
	m = m.WithHubClient(stubHubLister{})
	updated, _ := m.submit("/agents")
	m = updated.(ChatModel)

	if m.agentsPanel == nil {
		t.Fatal("/agents did not open the panel")
	}
	if m.palette.open {
		t.Error("opening the panel must close the \"/\" palette behind it")
	}
}

func TestAgentsClosedMsgClearsThePanel(t *testing.T) {
	m, _ := newTestModel(t)
	m = m.WithHubClient(stubHubLister{})
	opened, _ := m.submit("/agents")
	m = opened.(ChatModel)

	closed, _ := m.Update(agents.ClosedMsg{})
	m = closed.(ChatModel)
	if m.agentsPanel != nil {
		t.Fatal("agents.ClosedMsg must clear the panel")
	}
}

func TestAgentsListedInPaletteAndAvailableCommands(t *testing.T) {
	found := false
	for _, c := range paletteCommands {
		if c.Name == "/agents" {
			found = true
		}
	}
	if !found {
		t.Fatal("/agents is missing from paletteCommands")
	}

	// availableCommandNames (availability.go) is what the /help panel
	// actually renders from (root.FlagshipModel.AvailableCommandNames) — a
	// non-flagship agent must still see it, since it is always available.
	m, _ := newTestModel(t)
	names := m.availableCommandNames()
	if !containsAll(names, "/agents") {
		t.Fatalf("/agents missing from available commands for a non-flagship agent: %v", names)
	}
}
