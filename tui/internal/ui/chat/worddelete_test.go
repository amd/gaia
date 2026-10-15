// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func pressKey(t *testing.T, m ChatModel, k tea.KeyMsg) ChatModel {
	t.Helper()
	updated, _ := m.Update(k)
	return updated.(ChatModel)
}

// #5084: every word-delete chord the composer accepts, as Bubble Tea delivers
// it. Ctrl+Backspace is ctrl+h on a VT terminal and from the Windows console
// reader; Ctrl+Delete is the patched ctrl+delete key type.
func TestComposerDeletesAWordToTheLeft(t *testing.T) {
	for name, k := range map[string]tea.KeyMsg{
		"ctrl+backspace": {Type: tea.KeyCtrlH},
		"alt+backspace":  {Type: tea.KeyBackspace, Alt: true},
		"ctrl+w":         {Type: tea.KeyCtrlW},
	} {
		t.Run(name, func(t *testing.T) {
			m := typeInto(t, newTestChat(t), "summarize the report")
			m = pressKey(t, m, k)
			if got := m.input.Value(); got != "summarize the " {
				t.Errorf("composer = %q, want the last word gone", got)
			}
		})
	}
}

func TestComposerDeletesAWordToTheRight(t *testing.T) {
	for name, k := range map[string]tea.KeyMsg{
		"ctrl+delete": {Type: tea.KeyCtrlDelete},
		"alt+delete":  {Type: tea.KeyDelete, Alt: true},
		"alt+d":       {Type: tea.KeyRunes, Runes: []rune{'d'}, Alt: true},
	} {
		t.Run(name, func(t *testing.T) {
			m := typeInto(t, newTestChat(t), "summarize the report")
			m.input.CursorStart()
			m = pressKey(t, m, k)
			if got := m.input.Value(); got != " the report" {
				t.Errorf("composer = %q, want the first word gone", got)
			}
		})
	}
}

// Plain Backspace and Delete still remove one character — the chords above
// must not have swallowed them.
func TestPlainBackspaceAndDeleteStillRemoveOneCharacter(t *testing.T) {
	m := typeInto(t, newTestChat(t), "report")
	m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyBackspace})
	if got := m.input.Value(); got != "repor" {
		t.Errorf("backspace left %q, want one character gone", got)
	}
	m.input.CursorStart()
	m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyDelete})
	if got := m.input.Value(); got != "epor" {
		t.Errorf("delete left %q, want one character gone", got)
	}
}

// The Windows console reader reports Ctrl+Delete as its own key; it has to
// name itself the way the composer's binding expects.
func TestCtrlDeleteKeyName(t *testing.T) {
	if got := (tea.KeyMsg{Type: tea.KeyCtrlDelete}).String(); got != "ctrl+delete" {
		t.Errorf("KeyCtrlDelete reads as %q", got)
	}
}
