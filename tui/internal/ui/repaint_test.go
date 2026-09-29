// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"reflect"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/control"
)

type sizeModel struct{ updates int }

func (s sizeModel) Init() tea.Cmd                       { return nil }
func (s sizeModel) Update(tea.Msg) (tea.Model, tea.Cmd) { s.updates++; return s, nil }
func (s sizeModel) View() string                        { return "frame" }
func (s sizeModel) ControlSnapshot() control.Snapshot {
	return control.Snapshot{View: control.ViewChat}
}

// clears reports whether cmd (or any command it batches) is tea.ClearScreen.
func clears(cmd tea.Cmd) bool {
	if cmd == nil {
		return false
	}
	msg := cmd()
	if batch, ok := msg.(tea.BatchMsg); ok {
		for _, c := range batch {
			if clears(c) {
				return true
			}
		}
		return false
	}
	return reflect.TypeOf(msg) == reflect.TypeOf(tea.ClearScreen())
}

// A narrowed Windows Terminal left the old frame's status bar under the new
// one: a real size change has to repaint the whole screen, and only that.
func TestAResizeRepaintsTheWholeScreen(t *testing.T) {
	var m tea.Model = repaintOnResize{inner: sizeModel{}}
	m, cmd := m.Update(tea.WindowSizeMsg{Width: 120, Height: 30})
	if clears(cmd) {
		t.Error("the first size is the initial layout; there is nothing stale to clear")
	}
	m, cmd = m.Update(tea.WindowSizeMsg{Width: 120, Height: 30})
	if clears(cmd) {
		t.Error("an unchanged size must not flash the screen")
	}
	m, cmd = m.Update(tea.WindowSizeMsg{Width: 80, Height: 30})
	if !clears(cmd) {
		t.Error("a narrower window must repaint from a clear screen")
	}
	_, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if clears(cmd) {
		t.Error("only a resize may clear the screen")
	}
	if got := m.(repaintOnResize).ControlSnapshot().View; got != control.ViewChat {
		t.Errorf("the control API must still see the wrapped screen, got %q", got)
	}
}
