package chat

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/control"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

// The composer must draw one cursor and nothing beside it that looks like a
// second: the textarea's default "┃ " prompt did exactly that.
func TestComposerDrawsOneCursorWithNoBarBesideIt(t *testing.T) {
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
	m := newTestChat(t)
	m.streaming = false
	cells := control.CursorCells(m.input.View())
	if len(cells) != 1 {
		t.Fatalf("want exactly one cursor cell in the composer, got %d: %+v", len(cells), cells)
	}
	if strings.ContainsAny(cells[0].Left, "┃│▌|") {
		t.Errorf("a bar glyph %q sits beside the cursor and reads as a second cursor", cells[0].Left)
	}
}
