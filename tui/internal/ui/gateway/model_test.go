package gateway

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

func sized(t *testing.T, m GatewayModel, w, h int) (GatewayModel, []string) {
	t.Helper()
	next, _ := m.Update(tea.WindowSizeMsg{Width: w, Height: h})
	g := next.(GatewayModel)
	return g, strings.Split(ansi.Strip(g.View()), "\n")
}

// The screen ignored the window size: a 76-model list ran off the bottom, the
// terminal kept only its tail, and the cursor row at the top was not on screen.
func TestGatewayModelListFitsTheWindowAndKeepsTheCursorVisible(t *testing.T) {
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(t.TempDir(), "gateway.json"))
	m := New(&Client{}, nil)
	m.haveInit = true
	m.stage = stageModels
	for i := 0; i < 76; i++ {
		m.models = append(m.models, Model{ID: fmt.Sprintf("amd.model-%02d", i), Labels: []string{"tool-calling"}, CtxSize: 131072})
	}
	for _, cursor := range []int{0, 40, 75} {
		m.cursor = cursor
		for _, h := range []int{15, 24, 40} {
			_, lines := sized(t, m, 80, h)
			if len(lines) > h {
				t.Fatalf("cursor %d: %d lines in a %d-line window", cursor, len(lines), h)
			}
			want := fmt.Sprintf("> [ ] amd.model-%02d", cursor)
			if !strings.Contains(strings.Join(lines, "\n"), want) {
				t.Fatalf("cursor %d off screen at height %d:\n%s", cursor, h, strings.Join(lines, "\n"))
			}
			if last := lines[len(lines)-1]; !strings.Contains(last, "esc back") {
				t.Fatalf("key hints pushed off screen at height %d: %q", h, last)
			}
		}
	}
}

// Fixed-width intro text, a 60-cell input and a one-row key list overflowed
// narrow terminals, and resizing back to wide must lay the screen out again.
func TestGatewayScreenWrapsToTheWindowWidth(t *testing.T) {
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(t.TempDir(), "gateway.json"))
	m := New(&Client{}, nil)
	m.haveInit = true
	for _, st := range []stage{stageURL, stageToken, stageModels} {
		m.stage = st
		for _, w := range []int{120, 32, 120} {
			_, lines := sized(t, m, w, 40)
			for _, l := range lines {
				if ansi.StringWidth(l) > w {
					t.Fatalf("stage %d: line wider than %d: %q", st, w, l)
				}
			}
			if st == stageModels && w == 32 && !strings.Contains(strings.Join(lines, "\n"), "enter set active") {
				t.Fatalf("a key hint was split across lines:\n%s", strings.Join(lines, "\n"))
			}
		}
	}
}
