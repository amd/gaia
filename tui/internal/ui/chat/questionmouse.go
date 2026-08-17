// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	tea "github.com/charmbracelet/bubbletea"
)

// questionRowAt maps an absolute screen (x, y) — a tea.MouseMsg's own
// coordinates — to the QuestionModel row it landed on, or -1 for anything
// that isn't one: outside the question's rendered block entirely (including
// scrolled out of view — the question is drawn INLINE in the transcript, not
// as a full-window overlay like the palette, so it can be off-screen while
// still "open"), or on one of its own chrome rows (the title, a wrapped
// description line, the hint).
func (m ChatModel) questionRowAt(x, y int) int {
	if m.question == nil {
		return -1
	}

	top := m.contentHeaderRows()
	if y < top || y >= top+m.viewport.Height {
		// Outside the viewport's own screen rows altogether — the composer,
		// a banner, the status bar. Never a question row, whatever the
		// content-line math below would say.
		return -1
	}
	if x < 0 || x >= m.questionViewWidth {
		return -1
	}

	contentRow := m.viewport.YOffset + (y - top)
	rowInBlock := contentRow - m.questionViewLine
	if rowInBlock < 0 || rowInBlock >= m.questionViewLines {
		return -1
	}
	return m.question.RowAt(rowInBlock)
}

// handleQuestionMouse routes a mouse event while a question is on screen.
// Hovering (or clicking) an option moves the cursor to it, same as ↑/↓;
// clicking the option that is ALREADY the cursor answers it, same as Enter —
// mirroring handlePaletteMouse's click-the-selected-row-to-run rule. The
// wheel is left to the transcript rather than swallowed, same reasoning as
// handlePaletteMouse.
func (m ChatModel) handleQuestionMouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
	if isWheelEvent(msg) {
		var cmd tea.Cmd
		m.viewport, cmd = m.viewport.Update(msg)
		return m.afterScroll(), cmd
	}

	row := m.questionRowAt(msg.X, msg.Y)

	switch msg.Action {
	case tea.MouseActionMotion:
		// Gate on the cursor actually moving: All-Motion tracking delivers an
		// event per cell, and updateViewport re-renders the whole transcript.
		if row >= 0 && row != m.question.Cursor() {
			q := m.question.WithCursor(row)
			m.question = &q
			m.updateViewport()
		}
		return m, nil

	case tea.MouseActionPress:
		if msg.Button != tea.MouseButtonLeft || row < 0 {
			return m, nil
		}
		// Click-the-selected-row answers — except the free-text row, where a
		// click means "focus here", never "submit my half-typed answer".
		if row == m.question.Cursor() && !m.question.IsFreeTextRow(row) {
			q, cmd := m.question.Update(tea.KeyMsg{Type: tea.KeyEnter})
			m.question = &q
			m.updateViewport()
			return m, cmd
		}
		q := m.question.WithCursor(row)
		m.question = &q
		m.updateViewport()
		return m, nil
	}
	return m, nil
}
