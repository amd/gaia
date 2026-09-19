// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/agents"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// stubHubLister is a minimal agents.HubAgentLister for opening the in-chat
// "/agents" panel without a real daemon.
type stubHubLister struct{}

func (stubHubLister) Catalog(ctx context.Context, start, installedOnly, refresh bool) (*catalog.HubCatalog, error) {
	return &catalog.HubCatalog{Agents: []catalog.HubEntry{
		{ID: catalog.FlagshipID, Name: "GAIA", Installed: true},
		{ID: "email", Name: "Email", Installed: true},
	}}, nil
}
func (stubHubLister) Agents(ctx context.Context, start bool) ([]catalog.AgentRuntime, error) {
	return nil, nil
}

// openAgentsPanel drives the same keys a user presses — typing "/agents" then
// Enter — into the live chat, so the fixture reaches the open-panel state
// through the chat model's own command path rather than an unexported field.
func openAgentsPanel(t *testing.T, m FlagshipModel) FlagshipModel {
	t.Helper()
	chatModel := m.chat.WithHubClient(stubHubLister{})
	m.chat = &chatModel
	// View() renders the welcome screen instead of the transcript at width 0
	// (chat/model.go) — every assertion below needs the real frame, not that.
	sized, _ := m.chat.Update(tea.WindowSizeMsg{Width: 100, Height: 40})
	cmSized := sized.(chat.ChatModel)
	m.chat = &cmSized
	for _, r := range "/agents" {
		updated, _ := m.chat.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		cm := updated.(chat.ChatModel)
		m.chat = &cm
	}
	updated, cmd := m.chat.Update(tea.KeyMsg{Type: tea.KeyEnter})
	cm := updated.(chat.ChatModel)
	m.chat = &cm
	if cmd != nil {
		// submit("/agents") hands back the fresh panel's Init(), which fetches
		// the row list from the stub — run it and feed the result back in, the
		// same round trip Bubble Tea's runtime does for a real session.
		updated, _ = m.chat.Update(cmd())
		cm = updated.(chat.ChatModel)
		m.chat = &cm
	}
	// The panel's own heading, not a row: rows arrive from an async load these
	// tests never need to drain, and what they assert is that a SelectedMsg
	// takes the panel back off the screen.
	if !strings.Contains(ansi.Strip(m.chat.View()), "Installed agents") {
		t.Fatalf("test setup: /agents did not open the panel:\n%s", ansi.Strip(m.chat.View()))
	}
	return m
}

// agents.Model never emits ClosedMsg for a pick, only SelectedMsg (see its
// own doc comment), so root.Update closing the panel is the only thing that
// ever does. This and the two tests after it keep rendering through the SAME
// ChatModel the panel was opened on, and View() draws agentsPanel first when
// it is set — deleting the close call makes all three fail, because the
// rendered frame would still be the picker, not the status line underneath.
func TestAgentsSelectedMsgClosesThePanelOnASameIDNoOp(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	m = openAgentsPanel(t, m)

	updated, _ := m.Update(agents.SelectedMsg{ID: catalog.FlagshipID})
	m = updated.(FlagshipModel)

	frame := ansi.Strip(m.View())
	if !strings.Contains(frame, "Already running") {
		t.Fatalf("the same-id outcome is not visible — the agents panel is still drawn over it:\n%s", frame)
	}
}

func TestAgentsSelectedMsgClosesThePanelOnAnUnknownID(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	m = openAgentsPanel(t, m)

	updated, _ := m.Update(agents.SelectedMsg{ID: "not-a-real-agent"})
	m = updated.(FlagshipModel)

	frame := ansi.Strip(m.View())
	if !strings.Contains(frame, "not an installed agent") {
		t.Fatalf("the unknown-id refusal is not visible — the agents panel is still drawn over it:\n%s", frame)
	}
}

func TestAgentsSelectedMsgClosesThePanelBeforeTheSwitchGateCancels(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	m = openAgentsPanel(t, m)

	updated, _ := m.Update(agents.SelectedMsg{ID: "email"})
	m = updated.(FlagshipModel)
	if m.activeView != viewPreflight {
		t.Fatalf("test setup: switching did not open the readiness gate: view=%v", m.activeView)
	}

	updated, _ = m.cancelFromGate()
	m = updated.(FlagshipModel)

	frame := ansi.Strip(m.View())
	if !strings.Contains(frame, "Switch cancelled") {
		t.Fatalf("the cancellation note is not visible — the agents panel is still drawn over it:\n%s", frame)
	}
}
