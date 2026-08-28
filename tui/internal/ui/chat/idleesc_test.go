// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// quits reports whether a returned command would end the program. A nil command
// cannot, and a command that returns anything else is not a quit — the cancel
// paths legitimately return commands, so the assertion has to be about tea.Quit
// specifically rather than about a command existing.
func quits(cmd tea.Cmd) bool {
	if cmd == nil {
		return false
	}
	_, ok := cmd().(tea.QuitMsg)
	return ok
}

// Esc pressed with no turn running used to quit outright. The cancel paths above
// it in handleKey are careful never to cost the user their session (#2901,
// #2912); this fall-through undid all of that the moment the turn settled, and
// it is reachable by exactly the reflex those fixes were written for — a second
// Esc after a cancel that has already landed. Nothing on screen advertised it:
// the status bar says "Ctrl+C quit", and only that.
func TestIdleEscDoesNotQuitTheSession(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m.messages = []Message{{Role: RoleUser, Content: "an answer worth keeping"}}

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if quits(cmd) {
		t.Fatal("Esc with no turn running quit the app — an unadvertised keystroke " +
			"that destroys the transcript; Ctrl+C is the documented way out")
	}
	if len(m.messages) != 1 {
		t.Errorf("the transcript did not survive an idle Esc: %+v", m.messages)
	}
}

// Esc has to still DO something, or it reads as a dead key and gets pressed
// again. Discarding a half-typed line is what it means in every other composer.
func TestIdleEscClearsTheComposer(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m = typeInto(t, m, "never mind")
	if m.input.Value() != "never mind" {
		t.Fatalf("test setup: the composer did not take the text: %q", m.input.Value())
	}

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if quits(cmd) {
		t.Fatal("Esc quit the app instead of clearing the composer")
	}
	if got := m.input.Value(); got != "" {
		t.Errorf("Esc left the abandoned line in the composer: %q", got)
	}
}

// The way out has to be on screen in the state where the user most needs it —
// idle, having just pressed the key that no longer quits.
func TestTheIdleFooterStillNamesTheWayOut(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	out := ansi.Strip(m.View())
	if !strings.Contains(out, "Ctrl+C quit") {
		t.Errorf("nothing on an idle screen says how to leave:\n%s", out)
	}
}

// Ctrl+C is now the ONLY way out, so it had better still be one.
func TestCtrlCStillQuitsWhenIdle(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	if _, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC}); !quits(cmd) {
		t.Fatal("Ctrl+C no longer quits — the status bar promises it does, and " +
			"Esc no longer does it either")
	}
}
