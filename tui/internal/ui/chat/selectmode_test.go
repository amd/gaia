// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// Mouse reporting is what lets the wheel scroll the transcript, and it is also
// what takes click-drag selection away from the terminal. Ctrl+T trades one for
// the other so arbitrary text can be selected, copied and pasted with whatever
// the user's own terminal already does — the only mechanism that behaves the
// same on Windows Terminal, iTerm2 and a Linux terminal.

func selectModel() ChatModel {
	m := ChatModel{width: 100}
	return m
}

func pressCtrlT(m ChatModel) (ChatModel, tea.Cmd) {
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlT})
	return next.(ChatModel), cmd
}

func TestCtrlTTurnsMouseReportingOff(t *testing.T) {
	m, cmd := pressCtrlT(selectModel())

	if !m.selectMode {
		t.Fatal("Ctrl+T did not enter selection mode")
	}
	if cmd == nil {
		t.Fatal("no command issued; mouse reporting is still on and the " +
			"terminal still cannot select")
	}
	if got := cmd(); got == nil {
		t.Fatal("the mouse command produced no message")
	}
}

func TestCtrlTAgainRestoresScrolling(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	off, cmd := pressCtrlT(on)

	if off.selectMode {
		t.Error("a second Ctrl+T did not leave selection mode")
	}
	if cmd == nil {
		t.Fatal("leaving selection mode issued no command, so the wheel stays dead")
	}
}

func TestEscLeavesSelectionModeBeforeAnythingElse(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	next, _ := on.handleKey(tea.KeyMsg{Type: tea.KeyEsc})

	if next.(ChatModel).selectMode {
		t.Error("Esc did not leave selection mode")
	}
}

// Esc is the documented way out of selection mode, so it must not also quit —
// the same guarantee idleesc_test.go makes for an idle session.
func TestEscInSelectionModeDoesNotQuit(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	_, cmd := on.handleKey(tea.KeyMsg{Type: tea.KeyEsc})

	if cmd == nil {
		return
	}
	if msg := cmd(); msg != nil {
		if _, quit := msg.(tea.QuitMsg); quit {
			t.Fatal("Esc in selection mode quit the session")
		}
	}
}

func TestEscStillCancelsATurnWhenNotSelecting(t *testing.T) {
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

// The wheel dying is the whole cost of this mode, so the UI has to say it is on
// somewhere that cannot be scrolled away — the rule the bypass banner follows.
func TestTheBannerStatesTheModeAndItsCost(t *testing.T) {
	if got := selectModel().renderSelectBanner(); got != "" {
		t.Errorf("a banner appeared with selection mode off: %q", got)
	}

	on, _ := pressCtrlT(selectModel())
	banner := on.renderSelectBanner()
	if banner == "" {
		t.Fatal("selection mode is on with no banner; the dead wheel reads as a freeze")
	}
	lower := strings.ToLower(banner)
	for _, must := range []string{"select", "esc"} {
		if !strings.Contains(lower, must) {
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
