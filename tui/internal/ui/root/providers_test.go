// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"path/filepath"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/providers"
	tea "github.com/charmbracelet/bubbletea"
)

// Finish the first read-only probe against an absent binary. This avoids a
// real server or Python subprocess while exercising the root/gate boundary.
func providerBlockedRoot(t *testing.T) FlagshipModel {
	t.Helper()
	agent := catalog.Agent{ID: catalog.FlagshipID, Name: "GAIA", BinaryPath: filepath.Join(t.TempDir(), "missing-agent")}
	m := NewFlagshipModel(agent, true)
	next, cmd := m.beginPreflight(agent)
	m = next.(FlagshipModel)
	batch := cmd().(tea.BatchMsg)
	next, _ = m.Update(batch[0]())
	m = next.(FlagshipModel)
	if m.preflight.Busy() || !m.preflight.Report().Blocked() {
		t.Fatal("fixture did not reach a blocked, idle preflight")
	}
	return m
}

func TestProviderSelectionRebuildsStartupGateWithSelectedModel(t *testing.T) {
	for _, model := range []string{"fireworks.gemma-4-31b-it", "amd.gpt-4.1", "Gemma-4-E4B-it-GGUF"} {
		t.Run(model, func(t *testing.T) {
			m := providerBlockedRoot(t).WithClaude(true, "claude-sonnet-5")
			old := m.preflight
			next, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("p")})
			m = next.(FlagshipModel)
			if m.ControlSnapshot().Overlay != "provider" {
				t.Fatal("startup did not open provider setup")
			}
			next, _ = m.Update(providers.SelectedMsg{ID: model})
			m = next.(FlagshipModel)
			defer m.preflight.Cancel()
			opts := m.localOptions(m.agent)
			if m.model != model || opts.Model != model || opts.ClaudeMode || m.useClaude || m.claudeModel != "" {
				t.Fatalf("selection did not replace launch and readiness settings: %+v", opts)
			}
			if m.providerPanel != nil || m.preflight == old || !m.preflight.Busy() || m.chat != nil {
				t.Fatal("provider selection bypassed the new readiness check")
			}
		})
	}
}

// While an /agents switch gate is up, m.agent is still the OUTGOING agent —
// launchAgent only overwrites it once the switch commits — so a provider
// pick made at that gate must reopen readiness for m.pending (the incoming
// agent the gate on screen is actually for), never for m.agent.
func TestProviderSelectionAtASwitchGateReopensForTheIncomingAgent(t *testing.T) {
	m, _ := liveChatModel(t, "email")
	incoming := m.catalog.Get(catalog.FlagshipID)
	if incoming == nil {
		t.Fatal("test setup: catalog has no flagship entry")
	}
	// Mid-switch: the gate on screen is for the incoming agent, the live
	// session behind it is still the outgoing one.
	m.pending = incoming
	m.activeView = viewPreflight
	panel := providers.New("", 80, 24)
	m.providerPanel = &panel

	updated, _ := m.Update(providers.SelectedMsg{ID: "amd.gpt-4.1"})
	m = updated.(FlagshipModel)

	if m.pending == nil || m.pending.ID != catalog.FlagshipID {
		t.Fatalf("gate reopened for %v, want the incoming agent %q", m.pending, catalog.FlagshipID)
	}
	if m.agent.ID != "email" {
		t.Fatalf("the outgoing agent must not change before the switch commits, got %q", m.agent.ID)
	}
}

func TestClosingProviderSetupRechecksWithoutChangingStartupModel(t *testing.T) {
	m := providerBlockedRoot(t).WithModel("fireworks.gemma-4-31b-it")
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("p")})
	m = next.(FlagshipModel)
	next, cmd := m.Update(providers.ClosedMsg{})
	m = next.(FlagshipModel)
	defer m.preflight.Cancel()
	if m.providerPanel != nil || m.model != "fireworks.gemma-4-31b-it" || m.activeView != viewPreflight || cmd == nil {
		t.Fatal("cancel changed model or left the canceled provider screen active")
	}
	batch := cmd().(tea.BatchMsg)
	next, _ = m.Update(batch[0]())
	m = next.(FlagshipModel)
	row, ok := m.preflight.Report().Find(preflight.KeyBinary)
	if !ok || row.State != preflight.StateFailed || m.chat != nil {
		t.Fatal("cancel bypassed readiness or failed to restart the probe")
	}
}
