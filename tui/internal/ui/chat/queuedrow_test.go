// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

// The row said "Esc to take it back", which is true and incomplete: Esc there
// takes the same cancel path as everywhere else, so the turn the user is
// waiting on stops too. Naming only the harmless half of a key's effect is how
// a user presses it expecting to tidy up their queue and loses the answer.
func TestTheQueuedRowNamesWhatEscActuallyCosts(t *testing.T) {
	m := newTestChat(t)
	m.queued = []string{"and check the calendar"}

	row := ansi.Strip(m.renderQueuedRow())
	if !strings.Contains(row, "and check the calendar") {
		t.Fatalf("the queued line is not echoed back: %q", row)
	}
	if !strings.Contains(row, "stops the turn") {
		t.Errorf("the row still promises a free take-back: %q", row)
	}
}

// Esc's behaviour has to match what the row now claims: it cancels the turn AND
// hands the line back. A test on the words alone would pass just as happily if
// the code underneath changed.
func TestEscOnAQueuedLineDoesBothThingsTheRowPromises(t *testing.T) {
	c := &cancelingClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30
	m.resize()
	m.streaming = true
	m.cancelFn = func() {}
	m.queued = []string{"and check the calendar"}

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if !m.cancelPending {
		t.Error("Esc did not stop the turn, which the row says it does")
	}
	if len(m.queued) != 0 {
		t.Errorf("the line stayed queued behind a turn that is stopping: %q", m.queued)
	}
	if got := m.input.Value(); got != "and check the calendar" {
		t.Errorf("the line was not put back in the composer: %q", got)
	}
}

// The echo was truncated to the answer column and the prefix and hint appended
// after, so a long queued line ran past the last column, wrapped, and sheared
// the status bar onto the row below.
func TestTheQueuedRowFitsTheTerminal(t *testing.T) {
	for _, width := range []int{60, 80, 100, 120} {
		m := newTestChat(t)
		m.width, m.height = width, 30
		m.resize()
		m.queued = []string{strings.Repeat("a very long follow-up ", 20)}

		row := m.renderQueuedRow()
		if got := lipgloss.Width(row); got > width {
			t.Errorf("at %d columns the queued row is %d wide and wraps:\n%s",
				width, got, ansi.Strip(row))
		}
	}
}

// When both cannot fit, the echo wins. What was accepted is the row's reason to
// exist; the key is on the one row that is always on screen anyway.
func TestANarrowQueuedRowKeepsTheLineAndDropsTheHint(t *testing.T) {
	m := newTestChat(t)
	m.width, m.height = 40, 30
	m.resize()
	m.queued = []string{"check the calendar too"}

	row := ansi.Strip(m.renderQueuedRow())
	if strings.Contains(row, "stops the turn") {
		t.Errorf("at 40 columns the hint crowded out the line it describes: %q", row)
	}
	if !strings.Contains(row, "check the") {
		t.Errorf("the queued line itself was dropped: %q", row)
	}
}

// queuedIs reports whether the queue holds exactly the given lines, in order.
// The queue became a slice when a single slot turned out to discard everything
// a user typed after their first follow-up.
func queuedIs(queued []string, want ...string) bool {
	if len(queued) != len(want) {
		return false
	}
	for i := range want {
		if queued[i] != want[i] {
			return false
		}
	}
	return true
}
