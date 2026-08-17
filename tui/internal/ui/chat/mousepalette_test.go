// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// paletteRowCoord scans the rendered geometry for a screen coordinate that
// paletteHitTest maps onto row — the actual layout math (border, padding,
// lipgloss.Place's centering), not a hand-computed guess that could drift out
// of sync with it and pass for the wrong reason.
func paletteRowCoord(t *testing.T, m ChatModel, row int) (x, y int) {
	t.Helper()
	items := m.paletteFiltered()
	for y := 0; y < m.height; y++ {
		for x := 0; x < m.width; x++ {
			if got, inside := paletteHitTest(m.input.Value(), items, m.palette.selected, m.width, m.height, x, y); inside && got == row {
				return x, y
			}
		}
	}
	t.Fatalf("no on-screen coordinate maps to palette row %d at %dx%d", row, m.width, m.height)
	return 0, 0
}

func click(t *testing.T, m ChatModel, x, y int) (ChatModel, tea.Cmd) {
	t.Helper()
	updated, cmd := m.Update(tea.MouseMsg{X: x, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress})
	return updated.(ChatModel), cmd
}

func hover(t *testing.T, m ChatModel, x, y int) ChatModel {
	t.Helper()
	updated, _ := m.Update(tea.MouseMsg{X: x, Y: y, Action: tea.MouseActionMotion})
	return updated.(ChatModel)
}

// Hovering a row selects it — the same effect as ↑/↓, without running it.
func TestHoveringAPaletteRowSelectsIt(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/")
	if len(m.paletteFiltered()) < 2 {
		t.Fatal("test setup: need at least two rows")
	}

	x, y := paletteRowCoord(t, m, 1)
	m = hover(t, m, x, y)

	if m.palette.selected != 1 {
		t.Errorf("hover did not move the selection, got row %d", m.palette.selected)
	}
	if !m.palette.open {
		t.Error("hovering must not close the palette")
	}
}

// Clicking a row that is not yet selected only selects it — mirroring how a
// real click always lands after a hover already moved the selection there,
// so the FIRST click on a fresh row is "select", not "run".
func TestClickingAnUnselectedPaletteRowOnlySelectsIt(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/h") // matches /help then /hub
	if len(m.paletteFiltered()) != 2 {
		t.Fatal("test setup: expected exactly /help and /hub")
	}

	x, y := paletteRowCoord(t, m, 1)
	m, cmd := click(t, m, x, y)

	if m.palette.selected != 1 {
		t.Errorf("click did not select row 1, got %d", m.palette.selected)
	}
	if !m.palette.open {
		t.Error("selecting a row must not close the palette")
	}
	if cmd != nil {
		t.Error("selecting (not yet running) a row must not produce a command")
	}
}

// Clicking the row that IS already selected runs it — same as Enter — and,
// critically, never as a literal chat message (mirrors
// TestPaletteEnterRunsTheSelectedCommandNotTheTypedText and
// TestSetupCommandIsNeverSentAsAQuery's own guarantee for /setup).
func TestClickingTheSelectedPaletteRowRunsIt(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/h") // matches /help then /hub, /hub selected via a second click
	x, y := paletteRowCoord(t, m, 1)
	m, _ = click(t, m, x, y) // first click: select row 1 (/hub)
	if m.palette.selected != 1 {
		t.Fatal("test setup: expected row 1 selected after the first click")
	}

	m, _ = click(t, m, x, y) // second click, same spot: now selected -> runs

	if m.palette.open {
		t.Error("clicking the selected row must close the palette")
	}
	if m.input.Value() != "" {
		t.Errorf("clicking the selected row must clear the composer, got %q", m.input.Value())
	}
	for _, msg := range m.messages {
		if msg.Role == RoleUser {
			t.Errorf("the command must never be posted as a chat message, found: %q", msg.Content)
		}
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus {
		t.Errorf("expected /hub's decline status note, got: %+v", last)
	}
}

// Clicking outside the box closes the palette without touching the composer —
// same contract as Esc (TestPaletteEscClosesWithoutQuittingOrCancelling).
func TestClickingOutsideThePaletteClosesIt(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/mo")
	if !m.palette.open {
		t.Fatal("test setup: palette should be open")
	}

	// The box is centred with margins on a 100x30 test terminal — the very
	// top-left corner is always outside it.
	m, cmd := click(t, m, 0, 0)

	if m.palette.open {
		t.Error("a click outside the box must close the palette")
	}
	if m.input.Value() != "/mo" {
		t.Errorf("a click outside must not touch the composer, got %q", m.input.Value())
	}
	// Not "no Cmd at all": the palette closing legitimately releases the
	// mouse it captured for its own clicks (see TestClosingThePaletteReleasesTheMouse).
	if quits(cmd) {
		t.Error("a click outside the box must not quit")
	}
}

// The wheel is left for the transcript underneath rather than swallowed —
// the palette itself never scrolls (paletteBodyLines' own doc comment).
func TestPaletteWheelStillScrollsTheTranscriptUnderneath(t *testing.T) {
	m, _ := newTestModel(t)
	m.messages = []Message{{Role: RoleAssistant, Content: "line one\nline two\nline three"}}
	m.updateViewport()
	m = typeInto(t, m, "/")

	updated, _ := m.Update(tea.MouseMsg{Button: tea.MouseButtonWheelUp, Action: tea.MouseActionPress})
	m = updated.(ChatModel)

	if !m.palette.open {
		t.Error("a wheel tick must not close the palette")
	}
	if m.palette.selected != 0 {
		t.Error("a wheel tick must not move the palette's own selection")
	}
}
