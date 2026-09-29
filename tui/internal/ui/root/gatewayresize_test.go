// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/gateway"
	"github.com/amd/gaia/tui/internal/ui/providers"
)

// A resize while the gateway screen was up reached only that screen, so
// leaving it drew the chat at the old, wider size — every line overflowed.
func TestChatRelaysOutAfterAResizeOnTheGatewayScreen(t *testing.T) {
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(t.TempDir(), "gateway.json"))
	t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
	m, _ := liveChatModel(t, catalog.FlagshipID)
	next, _ := m.Update(tea.WindowSizeMsg{Width: 140, Height: 40})
	next, _ = next.(FlagshipModel).Update(chat.OpenGatewayMsg{})
	next, _ = next.(FlagshipModel).Update(tea.WindowSizeMsg{Width: 50, Height: 20})
	next, _ = next.(FlagshipModel).Update(gateway.CloseMsg{})
	m = next.(FlagshipModel)

	if m.activeView != viewChat {
		t.Fatalf("closing the gateway did not return to chat (view %d)", m.activeView)
	}
	for _, line := range strings.Split(ansi.Strip(m.View()), "\n") {
		if ansi.StringWidth(line) > 50 {
			t.Fatalf("chat still laid out for the old width: %q", line)
		}
	}
}

// Backing out of an agent-switch gate returned to a chat that never saw the
// resizes made while the gate was up.
func TestChatRelaysOutAfterAResizeOnACancelledSwitchGate(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	next, _ := m.Update(tea.WindowSizeMsg{Width: 140, Height: 40})
	next, _ = next.(FlagshipModel).switchAgent("email")
	next, _ = next.(FlagshipModel).Update(tea.WindowSizeMsg{Width: 50, Height: 20})
	next, _ = next.(FlagshipModel).cancelFromGate()
	m = next.(FlagshipModel)

	if m.activeView != viewChat {
		t.Fatalf("cancelling the switch did not return to chat (view %d)", m.activeView)
	}
	for _, line := range strings.Split(ansi.Strip(m.View()), "\n") {
		if ansi.StringWidth(line) > 50 {
			t.Fatalf("chat still laid out for the old width: %q", line)
		}
	}
}

// The same for the readiness gate behind the AI-provider panel: a resize while
// the panel was open left the gate laid out for the old window once it closed.
func TestGateRelaysOutAfterAResizeOnTheProviderPanel(t *testing.T) {
	m := providerBlockedRoot(t)
	next, _ := m.Update(tea.WindowSizeMsg{Width: 140, Height: 40})
	next, _ = next.(FlagshipModel).Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("p")})
	m = next.(FlagshipModel)
	if m.providerPanel == nil {
		t.Fatal("p did not open the provider panel")
	}
	next, _ = m.Update(tea.WindowSizeMsg{Width: 50, Height: 20})
	next, _ = next.(FlagshipModel).Update(providers.ClosedMsg{})
	m = next.(FlagshipModel)

	lines := strings.Split(ansi.Strip(m.View()), "\n")
	if len(lines) > 20 {
		t.Fatalf("gate still laid out for the old height: %d lines", len(lines))
	}
	for _, line := range lines {
		if ansi.StringWidth(line) > 50 {
			t.Fatalf("gate still laid out for the old width: %q", line)
		}
	}
}
