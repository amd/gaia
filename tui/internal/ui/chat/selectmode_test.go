// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// The terminal owns the mouse by default, so drag-to-select and the terminal's
// own copy and paste just work; the wheel scrolls through alternate scroll mode
// (ui/app.go). Ctrl+T gives the mouse to the app for clickable links.

func selectModel() ChatModel {
	return ChatModel{width: 100}
}

func pressCtrlT(m ChatModel) (ChatModel, tea.Cmd) {
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlT})
	return next.(ChatModel), cmd
}

// The report this default exists for: "I can't select text in the TUI". A
// program that captures the mouse takes plain drag-select away from the
// terminal, so nothing may capture it before the user asks.
func TestTheTerminalOwnsTheMouseByDefault(t *testing.T) {
	m, _ := newTestModel(t)
	if m.appMouse || m.mouseCaptured {
		t.Fatalf("the app holds the mouse on launch (appMouse=%v captured=%v), so drag-select is dead",
			m.appMouse, m.mouseCaptured)
	}
	if containsMouseEnable(m.Init()) {
		t.Error("Init asks the terminal for the mouse, so drag-select is dead from the first frame")
	}
	if cmd := m.applyMouseCapture(); cmd != nil {
		t.Error("the first update captures the mouse the user never asked to give up")
	}
}

// containsMouseEnable runs cmd — flattening Bubble Tea's batches — and reports
// whether any message it produced is a mouse-enable. Matched on the message
// type's NAME because bubbletea keeps those types unexported, so there is
// nothing to compare against directly.
func containsMouseEnable(cmd tea.Cmd) bool {
	if cmd == nil {
		return false
	}
	switch msg := cmd().(type) {
	case tea.BatchMsg:
		for _, sub := range msg {
			if containsMouseEnable(sub) {
				return true
			}
		}
		return false
	case tea.Msg:
		return strings.Contains(strings.ToLower(fmt.Sprintf("%T", msg)), "enablemouse")
	}
	return false
}

func TestCtrlTGivesTheMouseToTheApp(t *testing.T) {
	m, cmd := pressCtrlT(selectModel())
	if !m.appMouse || !m.mouseCaptured || m.mouseCaptureAllMotion {
		t.Fatalf("want Cell-Motion capture after Ctrl+T, got appMouse=%v captured=%v allMotion=%v",
			m.appMouse, m.mouseCaptured, m.mouseCaptureAllMotion)
	}
	if !containsMouseEnable(cmd) {
		t.Error("no enable sequence issued, so links still are not clickable")
	}
}

func TestCtrlTAgainGivesTheMouseBack(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	off, cmd := pressCtrlT(on)
	if off.appMouse || off.mouseCaptured {
		t.Error("a second Ctrl+T left the app holding the mouse, so drag-select stays broken")
	}
	if cmd == nil || cmd() == nil {
		t.Fatal("no command issued, so the terminal never gets the mouse back")
	}
}

// Esc means what it always meant — cancel the turn, or clear the composer —
// whoever holds the mouse.
func TestEscStillCancelsATurn(t *testing.T) {
	for _, appMouse := range []bool{false, true} {
		m := selectModel()
		m.appMouse = appMouse
		m.streaming = true
		called := false
		m.cancelFn = func() { called = true }
		if _, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc}); cmd != nil {
			cmd()
		}
		if !called {
			t.Errorf("appMouse=%v: Esc no longer cancels a running turn", appMouse)
		}
	}
}

// The status bar says how to get drag-select back only when it is gone.
func TestTheHintNamesDragSelectOnlyWhenTheAppHasTheMouse(t *testing.T) {
	has := func(m ChatModel) bool {
		for _, h := range m.statusHints() {
			if strings.Contains(h.text, "drag-select") {
				return true
			}
		}
		return false
	}
	if has(selectModel()) {
		t.Error("the default already selects; a hint about it is noise")
	}
	on, _ := pressCtrlT(selectModel())
	if !has(on) {
		t.Error("with the app holding the mouse, the bar must say how to select again")
	}
}
