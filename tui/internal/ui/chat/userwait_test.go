package chat

import (
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

// An answer's timing leaves out the time a permission prompt sat waiting on the
// user, so their pause is not reported as the agent being slow.
func TestTurnElapsedExcludesTimeWaitingOnTheUser(t *testing.T) {
	m := newTestChat(t)
	start := time.Now()
	m.queryStart = start

	cm := components.NewConfirmationModel("run", "write_file", "Write?", "")
	m.confirmation = &cm
	m.trackUserWait(start.Add(2 * time.Second))
	if got := m.turnElapsed(start.Add(60 * time.Second)); got != 2*time.Second {
		t.Fatalf("while the prompt is open, want 2s, got %v", got)
	}

	m.confirmation = nil
	m.trackUserWait(start.Add(100 * time.Second))
	if got := m.turnElapsed(start.Add(105 * time.Second)); got != 7*time.Second {
		t.Fatalf("after approval, want 7s, got %v", got)
	}
}

// The wait ends when the user answers, not when the answered prompt is cleared.
func TestAnAnsweredPromptStopsTheWaitClock(t *testing.T) {
	m := newTestChat(t)
	start := time.Now()
	m.queryStart = start
	cm := components.NewConfirmationModel("run", "write_file", "Write?", "")
	m.confirmation = &cm
	m.trackUserWait(start)
	answered, _ := cm.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	m.confirmation = &answered
	m.trackUserWait(start.Add(10 * time.Second))
	if got := m.turnElapsed(start.Add(15 * time.Second)); got != 5*time.Second {
		t.Fatalf("want 5s of agent time after the answer, got %v", got)
	}
}

// A prompt clipped to fit a short terminal says how much of it is off-screen,
// so a long edit cannot pass for the whole edit.
func TestAClippedPromptSaysHowManyLinesItHid(t *testing.T) {
	view := strings.Join([]string{"title", "a", "b", "c", "d", "e", "keys" + strings.Repeat(" ", 72)}, "\n")
	got := strings.Split(clipConfirmation(view, 4), "\n")
	if len(got) != 4 || got[0] != "title" || got[1] != "a" || !strings.HasPrefix(got[3], "keys") {
		t.Fatalf("want head, notice, keys; got %q", got)
	}
	if !strings.Contains(got[2], "4 more lines hidden") {
		t.Errorf("the clip does not say what it hid: %q", got[2])
	}
}

// The ledger's active time matches the answer's footnote, not the wall clock.
func TestTheSessionLedgerExcludesTheUsersWait(t *testing.T) {
	m := newTestChat(t)
	m.queryStart = time.Now().Add(-time.Minute)
	m.userWait = 50 * time.Second
	m.cost = sessionCost{}
	m.streaming = true
	m, _, _ = m.handleCanonicalEvent(event.CanonicalFinalEvent{Type: event.CanonicalTypeFinal, Answer: "done"})
	if len(m.cost.turns) != 1 {
		t.Fatalf("want one ledger entry, got %d", len(m.cost.turns))
	}
	if d := m.cost.turns[0].duration; d > 20*time.Second {
		t.Errorf("ledger billed %v; the 50s wait should not be in it", d)
	}
}

// The slash palette's filter line draws the same block cursor as the composer,
// so a driver reading `cursors` sees it; a "▏" glyph was invisible to it.
func TestPaletteQueryDrawsABlockCursor(t *testing.T) {
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
	lines := paletteBodyLines("/mo", nil, 0, 40)
	if strings.Contains(lines[2], "▏") {
		t.Errorf("filter line still draws a ▏ glyph: %q", lines[2])
	}
	if cells := control.CursorCells(strings.Join(lines, "\n")); len(cells) != 1 {
		t.Fatalf("want exactly one cursor cell in the palette, got %d: %+v", len(cells), cells)
	}
}
