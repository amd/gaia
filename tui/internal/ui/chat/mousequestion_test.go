// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/components"
)

// questionRowCoord scans the rendered screen for a coordinate that
// questionRowAt maps onto row, the same way paletteRowCoord does for the
// palette — real layout math, not a hand-computed guess.
func questionRowCoord(t *testing.T, m ChatModel, row int) (x, y int) {
	t.Helper()
	for y := 0; y < m.height; y++ {
		for x := 0; x < m.width; x++ {
			if m.questionRowAt(x, y) == row {
				return x, y
			}
		}
	}
	t.Fatalf("no on-screen coordinate maps to question row %d", row)
	return 0, 0
}

// Hovering an option moves the cursor to it, same as ↑/↓.
func TestHoveringAQuestionOptionSelectsIt(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())
	if m.question.Cursor() != 0 {
		t.Fatal("test setup: expected the first option selected")
	}

	x, y := questionRowCoord(t, m, 1)
	updated, _ := m.Update(tea.MouseMsg{X: x, Y: y, Action: tea.MouseActionMotion})
	m = updated.(ChatModel)

	if m.question.Cursor() != 1 {
		t.Errorf("hover did not move the cursor, got %d", m.question.Cursor())
	}
	if len(c.answers) != 0 {
		t.Error("hovering must never answer")
	}
}

// Clicking an option that is not yet the cursor only selects it.
func TestClickingAnUnselectedQuestionOptionOnlySelectsIt(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())

	x, y := questionRowCoord(t, m, 1)
	updated, _ := m.Update(tea.MouseMsg{X: x, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress})
	m = updated.(ChatModel)

	if m.question == nil {
		t.Fatal("the question must still be open")
	}
	if m.question.Cursor() != 1 {
		t.Errorf("click did not select option 1, got %d", m.question.Cursor())
	}
	if len(c.answers) != 0 {
		t.Error("selecting (not yet answering) an option must not answer it")
	}
}

// Clicking the option that IS already the cursor answers it — same as Enter —
// and delivers the SAME value Enter would (never a click-position guess).
func TestClickingTheSelectedQuestionOptionAnswersIt(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())

	x, y := questionRowCoord(t, m, 1)
	updated, _ := m.Update(tea.MouseMsg{X: x, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress})
	m = updated.(ChatModel) // select option 1

	updated, cmd := m.Update(tea.MouseMsg{X: x, Y: y, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress})
	m = updated.(ChatModel) // click again: now selected -> answers

	if cmd == nil {
		t.Fatal("clicking the selected option produced no command")
	}
	msg := cmd()
	answered, ok := msg.(components.QuestionAnsweredMsg)
	if !ok {
		t.Fatalf("expected QuestionAnsweredMsg, got %T", msg)
	}
	if answered.Value != "no" { // option 1 in needsInput() is {Value: "no", Label: "Not now"}
		t.Errorf("answered value = %q, want %q", answered.Value, "no")
	}
}

// The wheel is left for the transcript underneath — a long question is
// exactly when scrolling back to reread earlier context matters most.
func TestQuestionWheelStillScrollsTheTranscriptUnderneath(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())
	cursorBefore := m.question.Cursor()

	updated, _ := m.Update(tea.MouseMsg{Button: tea.MouseButtonWheelUp, Action: tea.MouseActionPress})
	m = updated.(ChatModel)

	if m.question == nil {
		t.Fatal("a wheel tick must not close the question")
	}
	if m.question.Cursor() != cursorBefore {
		t.Error("a wheel tick must not move the question's own cursor")
	}
}
