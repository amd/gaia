// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// The terminal owns the mouse by default, so drag-select and the platform's own
// copy/paste (Ctrl+Shift+C, right-click, Cmd+C) work with nothing to discover.
// Capturing it buys wheel-scrolling and costs selection, so it is opt-in.

func selectModel() ChatModel {
	return ChatModel{width: 100}
}

func pressCtrlT(m ChatModel) (ChatModel, tea.Cmd) {
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlT})
	return next.(ChatModel), cmd
}

// The regression this whole inversion exists for: "I still can't drag my mouse
// pointer over terminal text and copy it."
func TestTheTerminalOwnsTheMouseByDefault(t *testing.T) {
	if selectModel().mouseCaptured {
		t.Fatal("the app grabs the mouse on launch, so drag-select is broken " +
			"before the user does anything")
	}
}

func TestNoBannerWhenTheTerminalHasTheMouse(t *testing.T) {
	if got := selectModel().renderSelectBanner(); got != "" {
		t.Errorf("the default state is announcing itself: %q", got)
	}
}

func TestCtrlTGivesTheAppTheMouseForWheelScrolling(t *testing.T) {
	m, cmd := pressCtrlT(selectModel())

	if !m.mouseCaptured {
		t.Fatal("Ctrl+T did not enable wheel scrolling")
	}
	if cmd == nil || cmd() == nil {
		t.Fatal("no mouse command issued, so the wheel still will not scroll")
	}
}

func TestCtrlTAgainGivesSelectionBack(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	off, cmd := pressCtrlT(on)

	if off.mouseCaptured {
		t.Error("a second Ctrl+T did not release the mouse")
	}
	if cmd == nil {
		t.Fatal("releasing the mouse issued no command, so selection stays broken")
	}
}

func TestEscReleasesTheMouseBeforeAnythingElse(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	next, _ := on.handleKey(tea.KeyMsg{Type: tea.KeyEsc})

	if next.(ChatModel).mouseCaptured {
		t.Error("Esc did not give selection back")
	}
}

func TestEscWhileCapturedDoesNotQuit(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	_, cmd := on.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd == nil {
		return
	}
	if msg := cmd(); msg != nil {
		if _, quit := msg.(tea.QuitMsg); quit {
			t.Fatal("Esc quit the session instead of releasing the mouse")
		}
	}
}

func TestEscStillCancelsATurnWhenTheTerminalHasTheMouse(t *testing.T) {
	m := selectModel()
	m.streaming = true
	called := false
	m.cancelFn = func() { called = true }

	if _, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc}); cmd != nil {
		cmd()
	}
	if !called {
		t.Error("Esc no longer cancels a running turn")
	}
}

// Losing selection is the whole cost of capture, so the UI has to say it is on
// somewhere that cannot be scrolled away.
func TestTheBannerStatesTheModeAndItsCost(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	banner := strings.ToLower(on.renderSelectBanner())

	if banner == "" {
		t.Fatal("the mouse is captured with no banner; broken selection reads as a bug")
	}
	for _, must := range []string{"select", "esc"} {
		if !strings.Contains(banner, must) {
			t.Errorf("banner never mentions %q: %q", must, banner)
		}
	}
}

func TestTheBannerFitsANarrowTerminal(t *testing.T) {
	m, _ := pressCtrlT(ChatModel{width: 28})
	if got := lineWidth(m.renderSelectBanner()); got > 28 {
		t.Errorf("banner is %d columns wide in a 28-column terminal", got)
	}
}

func lineWidth(s string) int {
	widest := 0
	for _, line := range strings.Split(s, "\n") {
		if n := len([]rune(stripSGR(line))); n > widest {
			widest = n
		}
	}
	return widest
}

func stripSGR(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); {
		if s[i] == 0x1b {
			for i < len(s) && s[i] != 'm' {
				i++
			}
			i++
			continue
		}
		b.WriteByte(s[i])
		i++
	}
	return b.String()
}
