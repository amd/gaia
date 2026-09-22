// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/components"
)

// The help panel is drawn over the whole window, and on the flagship path THIS
// model owns it — the chat model's own gate never sees it. A live run caught
// the gap: with /help up, a click still reached the transcript behind the
// panel and opened a link the reader could not see.
//
// Keys were already gated here; mouse was not. Both have to be, for the same
// reason and in the same place.
func TestAClickDoesNotReachThroughTheHelpPanel(t *testing.T) {
	m := providerBlockedRoot(t)
	m.width, m.height = 100, 30

	next, _ := m.Update(components.HelpContextChat)
	m = next.(FlagshipModel)
	if !m.help.Open {
		t.Fatal("test setup: the help panel should be open")
	}

	_, cmd := m.Update(tea.MouseMsg(tea.MouseEvent{
		X: 20, Y: 10, Action: tea.MouseActionPress, Button: tea.MouseButtonLeft,
	}))
	if cmd != nil {
		t.Error("the click was passed through to the view behind the panel")
	}
	if !m.help.Open {
		t.Error("the click closed the panel; only keys dismiss it")
	}
}

// And the wheel moves the panel rather than the content behind it — otherwise
// closing help leaves the reader somewhere they never scrolled to.
func TestTheWheelScrollsTheOpenHelpPanel(t *testing.T) {
	m := providerBlockedRoot(t)
	m.width, m.height = 100, 24

	next, _ := m.Update(components.HelpContextChat)
	m = next.(FlagshipModel)

	next, _ = m.Update(tea.MouseMsg(tea.MouseEvent{
		Action: tea.MouseActionPress, Button: tea.MouseButtonWheelDown,
	}))
	m = next.(FlagshipModel)

	if m.help.Scroll == 0 {
		t.Error("the wheel did not scroll the help panel")
	}
}
