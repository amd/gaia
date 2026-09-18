// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// The app owns the mouse by default, so the wheel scrolls the transcript and a
// printed link can be clicked. Ctrl+T hands it back for plain drag-select.

func selectModel() ChatModel {
	return ChatModel{width: 100}
}

func pressCtrlT(m ChatModel) (ChatModel, tea.Cmd) {
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlT})
	return next.(ChatModel), cmd
}

// The regression this default exists for: scrolling up moved nothing, because
// an alt-screen app has no terminal scrollback for the wheel to fall back on.
func TestTheAppWantsTheMouseByDefault(t *testing.T) {
	m := selectModel()
	if m.mouseSelectMode {
		t.Fatal("select mode is on before the user asked for it, so the wheel " +
			"scrolls nothing on launch")
	}
	if cmd := m.applyMouseCapture(); cmd == nil {
		t.Fatal("no mouse command issued on the first update, so the wheel " +
			"still will not scroll")
	}
	if !m.mouseCaptured || m.mouseCaptureAllMotion {
		t.Errorf("want plain Cell-Motion capture, got captured=%v allMotion=%v",
			m.mouseCaptured, m.mouseCaptureAllMotion)
	}
}

// The escape sequence has to actually go out, and it has to go out at
// STARTUP: the model recording itself as captured without Init emitting
// ?1002h would leave the wheel dead while every other check passed.
func TestInitArmsMouseTracking(t *testing.T) {
	m, _ := newTestModel(t)
	if !m.mouseCaptured {
		t.Fatal("a fresh model does not consider itself captured, so nothing " +
			"will emit the enable sequence")
	}
	if !containsMouseEnable(m.Init()) {
		t.Error("Init never asks the terminal to report the wheel")
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
	default:
		return strings.Contains(
			strings.ToLower(fmt.Sprintf("%T", msg)), "enablemouse")
	}
}

func TestNoBannerInTheDefaultMode(t *testing.T) {
	if got := selectModel().renderSelectBanner(); got != "" {
		t.Errorf("the default state is announcing itself: %q", got)
	}
}

func TestCtrlTHandsTheMouseBackToTheTerminal(t *testing.T) {
	m := selectModel()
	m.applyMouseCapture()

	m, cmd := pressCtrlT(m)
	if !m.mouseSelectMode {
		t.Fatal("Ctrl+T did not turn on select mode")
	}
	if m.mouseCaptured {
		t.Error("select mode left the app holding the mouse, so drag-select stays broken")
	}
	if cmd == nil || cmd() == nil {
		t.Fatal("no mouse command issued, so the terminal never gets the mouse back")
	}
}

func TestCtrlTAgainTakesTheMouseBack(t *testing.T) {
	m := selectModel()
	m.applyMouseCapture()

	on, _ := pressCtrlT(m)
	off, cmd := pressCtrlT(on)
	if off.mouseSelectMode {
		t.Error("a second Ctrl+T did not leave select mode")
	}
	if !off.mouseCaptured {
		t.Error("leaving select mode did not re-capture the mouse, so the wheel stays dead")
	}
	if cmd == nil {
		t.Fatal("no command issued, so the terminal keeps the mouse")
	}
}

func TestEscLeavesSelectModeBeforeAnythingElse(t *testing.T) {
	m := selectModel()
	m.applyMouseCapture()
	on, _ := pressCtrlT(m)

	next, _ := on.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	if next.(ChatModel).mouseSelectMode {
		t.Error("Esc did not give scrolling back")
	}
}

func TestEscInSelectModeDoesNotQuit(t *testing.T) {
	on, _ := pressCtrlT(selectModel())
	_, cmd := on.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd == nil {
		return
	}
	if msg := cmd(); msg != nil {
		if _, quit := msg.(tea.QuitMsg); quit {
			t.Fatal("Esc quit the session instead of leaving select mode")
		}
	}
}

func TestEscStillCancelsATurnInTheDefaultMode(t *testing.T) {
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

// Select mode silently stops the wheel, so it has to say so somewhere that
// cannot be scrolled away.
func TestTheBannerStatesSelectModeAndItsCost(t *testing.T) {
	// Wide enough for the full sentence — the short form a narrow terminal
	// falls back to is covered by TestTheBannerFitsANarrowTerminal.
	on, _ := pressCtrlT(ChatModel{width: 140})
	banner := strings.ToLower(on.renderSelectBanner())

	if banner == "" {
		t.Fatal("select mode is on with no banner; a dead wheel reads as a bug")
	}
	for _, must := range []string{"select", "wheel", "esc"} {
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
