// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// `/help` did nothing whatsoever in a `gaia run <agent>` session.
//
// That path (ui.RunAgent) puts this chat model straight in front of Bubble Tea
// with no root model wrapping it, and the help panel lived entirely in root —
// so ToggleHelpMsg had no handler and was dropped on the floor. Typing /help
// printed nothing, opened nothing, and reported no error. It worked from the
// hub, which is why it looked fine.

func openHelp(t *testing.T) ChatModel {
	t.Helper()
	m := newTestChat(t)
	m.width, m.height = 100, 30
	m.resize()

	updated, _ := m.Update(ToggleHelpMsg{})
	return updated.(ChatModel)
}

func TestHelpOpensWithNoRootModelWrappingTheChat(t *testing.T) {
	m := openHelp(t)

	if !m.help.Open {
		t.Fatal("ToggleHelpMsg did not open the panel — /help is a no-op on this path")
	}
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "GAIA Chat") {
		t.Errorf("the panel is open but not drawn:\n%s", view)
	}
	for _, want := range []string{"Ctrl+T", "/model", "Enter"} {
		if !strings.Contains(view, want) {
			t.Errorf("the rendered panel never mentions %q", want)
		}
	}
}

func TestHelpTogglesClosedAgain(t *testing.T) {
	m := openHelp(t)
	updated, _ := m.Update(ToggleHelpMsg{})
	m = updated.(ChatModel)

	if m.help.Open {
		t.Fatal("a second /help did not close the panel")
	}
	if strings.Contains(ansi.Strip(m.View()), "GAIA Chat") {
		t.Error("the panel is closed but still drawn")
	}
}

// An open panel must not be a trap: any key that is not navigation closes it.
func TestAnyOtherKeyClosesTheOpenPanel(t *testing.T) {
	for _, key := range []tea.KeyType{tea.KeyEsc, tea.KeyEnter, tea.KeyRunes} {
		m := openHelp(t)
		updated, _ := m.handleKey(tea.KeyMsg{Type: key, Runes: []rune{'x'}})
		if updated.(ChatModel).help.Open {
			t.Errorf("key %v left the panel open", key)
		}
	}
}

func TestNavigationKeysScrollTheOpenPanelInsteadOfClosingIt(t *testing.T) {
	m := openHelp(t)
	m.width, m.height = 80, 14 // short enough that the panel must scroll

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyPgDown})
	next := updated.(ChatModel)

	if !next.help.Open {
		t.Fatal("PgDn closed the panel instead of scrolling it")
	}
	if next.help.Scroll == 0 {
		t.Error("PgDn did not move the panel")
	}
}

// While help is up it owns the keyboard, so a keystroke must not also reach the
// composer underneath — otherwise dismissing the panel types a stray character.
func TestKeysDoNotLeakIntoTheComposerWhileHelpIsOpen(t *testing.T) {
	m := openHelp(t)

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'x'}})
	if got := updated.(ChatModel).input.Value(); got != "" {
		t.Errorf("the dismissing keystroke landed in the composer: %q", got)
	}
}

// Under the hub, root consumes ToggleHelpMsg and draws the panel itself. This
// model's own panel must stay shut so the two never both draw.
func TestTheChatPanelStaysClosedUntilItIsToldToOpen(t *testing.T) {
	m := newTestChat(t)
	m.width, m.height = 100, 30
	m.resize()

	if m.help.Open {
		t.Fatal("the panel starts open")
	}
	if strings.Contains(ansi.Strip(m.View()), "GAIA Chat") {
		t.Error("an unopened panel is being drawn")
	}
}
