// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package sessions is the /sessions panel: past and open sessions with their
// title, age and status, sessions waiting on the user first. Picking one asks
// the host to reopen it; this package never launches anything itself.
package sessions

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/sessionstore"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// SelectedMsg asks the host to reopen Session.
type SelectedMsg struct{ Session sessionstore.Session }

// ClosedMsg reports the user left the panel without picking anything.
type ClosedMsg struct{}

type loadedMsg struct {
	gen     int
	listing sessionstore.Listing
	err     error
}

// Model lists the sessions in one history directory.
type Model struct {
	dir      string
	current  string
	allKinds bool
	held     sessionstore.Held
	now      func() time.Time

	listing  sessionstore.Listing
	loadErr  string
	loading  bool
	gen      int
	selected int

	width, height int
}

// New lists the sessions in dir. current is the id of the session on screen
// (marked, and never offered as somewhere to go); allKinds also lists eval
// and scheduled runs. held tells an open session from a finished one.
func New(dir, current string, allKinds bool, held sessionstore.Held, width, height int) Model {
	return Model{
		dir: dir, current: current, allKinds: allKinds, held: held,
		now: time.Now, loading: true, width: width, height: height,
	}
}

// WithClock fixes the time ages are measured from; tests use it for stable frames.
func (m Model) WithClock(now func() time.Time) Model {
	m.now = now
	return m
}

func (m Model) Init() tea.Cmd { return m.load() }

func (m Model) load() tea.Cmd {
	dir, all, held, gen := m.dir, m.allKinds, m.held, m.gen
	return func() tea.Msg {
		l, err := sessionstore.List(dir, sessionstore.ListOptions{AllKinds: all, Held: held})
		return loadedMsg{gen: gen, listing: l, err: err}
	}
}

func (m Model) reload() (Model, tea.Cmd) {
	m.gen++
	m.loading = true
	return m, m.load()
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch v := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = v.Width, v.Height
	case loadedMsg:
		if v.gen != m.gen {
			return m, nil
		}
		m.loading = false
		m.listing = v.listing
		m.loadErr = ""
		if v.err != nil {
			m.loadErr = "Could not list sessions: " + v.err.Error()
		}
		if m.selected >= len(m.listing.Sessions) {
			m.selected = 0
		}
	case tea.KeyMsg:
		n := len(m.listing.Sessions)
		switch v.String() {
		case "ctrl+c":
			return m, tea.Quit
		case "esc", "q":
			return m, func() tea.Msg { return ClosedMsg{} }
		case "up", "k":
			if n > 0 {
				m.selected = (m.selected - 1 + n) % n
			}
		case "down", "j", "tab":
			if n > 0 {
				m.selected = (m.selected + 1) % n
			}
		case "a":
			m.allKinds = !m.allKinds
			m.selected = 0
			return m.reload()
		case "r":
			return m.reload()
		case "enter":
			if m.loading || m.selected >= n {
				return m, nil
			}
			s := m.listing.Sessions[m.selected]
			return m, func() tea.Msg { return SelectedMsg{Session: s} }
		}
	}
	return m, nil
}

// statusLabel is the words for a status. Colour only repeats what they say,
// so NO_COLOR loses nothing.
func statusLabel(s sessionstore.Session) (glyph, label string, color lipgloss.TerminalColor) {
	switch s.Status {
	case sessionstore.StatusWaiting:
		return "!", "waiting on you", theme.Warning
	case sessionstore.StatusRunning:
		return "●", "running", theme.Info
	case sessionstore.StatusFailed:
		if s.Interrupted {
			return "✗", "stopped mid-turn", theme.Danger
		}
		return "✗", "failed", theme.Danger
	}
	return "✓", "done", theme.Dim
}

// statusWidth fits the longest label, so titles line up.
const statusWidth = len("stopped mid-turn")

// wideRows is the width from which a session fits on one row.
const wideRows = 64

func (m Model) note(s sessionstore.Session) string {
	var parts []string
	if s.ID == m.current {
		parts = append(parts, "this session")
	} else if s.Live {
		parts = append(parts, "open in another window")
	}
	if m.allKinds && s.Kind != sessionstore.KindChat {
		parts = append(parts, s.Kind)
	}
	return strings.Join(parts, " · ")
}

func (m Model) rowLines(i int, s sessionstore.Session, w int) []string {
	glyph, label, color := statusLabel(s)
	status := lipgloss.NewStyle().Foreground(color)
	dim := lipgloss.NewStyle().Foreground(theme.Dim)
	marker := "  "
	if i == m.selected {
		marker = "› "
	}
	title := s.Title
	if title == "" {
		title = "(untitled)"
	}
	age := sessionstore.Age(m.now(), s.UpdatedAt)
	if note := m.note(s); note != "" {
		age = note + " · " + age
	}
	titleStyle := lipgloss.NewStyle()
	if i == m.selected {
		titleStyle = titleStyle.Bold(true).Foreground(theme.AccentBright)
		marker = titleStyle.Render(marker)
	}

	if w >= wideRows {
		head := status.Render(fmt.Sprintf("%s %-*s", glyph, statusWidth, label))
		room := w - 2 - (2 + statusWidth) - 2 - ansi.StringWidth(age) - 2
		t := ansi.Truncate(title, max(room, 8), "…")
		pad := max(1, w-2-(2+statusWidth)-2-ansi.StringWidth(t)-ansi.StringWidth(age))
		return []string{marker + head + "  " + titleStyle.Render(t) + strings.Repeat(" ", pad) + dim.Render(age)}
	}
	return []string{
		marker + titleStyle.Render(ansi.Truncate(title, max(w-2, 4), "…")),
		"  " + status.Render(glyph+" "+label) + dim.Render(ansi.Truncate(" · "+age, max(w-4-len(label), 1), "…")),
	}
}

func (m Model) View() string {
	w := max(1, m.width-4)
	accent := lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	dim := lipgloss.NewStyle().Foreground(theme.Dim)
	lines := []string{accent.Render("Sessions")}
	if m.allKinds {
		lines = append(lines, dim.Render("Every kind, eval and scheduled runs included."))
	} else {
		lines = append(lines, dim.Render("Chats only. Press a to include eval and scheduled runs."))
	}
	lines = append(lines, "")

	n := len(m.listing.Sessions)
	switch {
	case m.loading:
		lines = append(lines, "Loading…")
	case m.loadErr != "":
		lines = append(lines, m.loadErr)
	case n == 0:
		lines = append(lines, "No sessions yet. Each conversation you have is kept here to reopen.")
	default:
		rows := make([][]string, n)
		for i, s := range m.listing.Sessions {
			rows[i] = m.rowLines(i, s, w)
		}
		// Keep the selection on screen: grow a window around it.
		avail := max(1, m.height-9-len(m.listing.Problems))
		start, end := m.selected, m.selected+1
		used := len(rows[m.selected])
		for start > 0 && used+len(rows[start-1]) <= avail {
			start--
			used += len(rows[start])
		}
		for end < n && used+len(rows[end]) <= avail {
			used += len(rows[end])
			end++
		}
		for _, r := range rows[start:end] {
			lines = append(lines, r...)
		}
	}
	for _, p := range m.listing.Problems {
		lines = append(lines, "", lipgloss.NewStyle().Foreground(theme.Danger).Render(p))
	}

	hint := "↑/↓ choose · enter reopen · a all kinds · r refresh · esc close"
	if w < 64 {
		hint = "↑/↓ · enter reopen · a all · esc"
	}
	if w < 34 {
		hint = "↑/↓ · enter · esc"
	}
	var wrapped []string
	for _, line := range lines {
		wrapped = append(wrapped, strings.Split(ansi.Wrap(line, w, ""), "\n")...)
	}
	wrapped = append(wrapped, "", dim.Render(ansi.Truncate(hint, w, "…")))
	return lipgloss.NewStyle().Padding(1, 2).Render(strings.Join(wrapped, "\n"))
}
