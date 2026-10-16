// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// The first screen suggests up to four things to try, each one a skill that is
// installed and needs no further setup, picked with its number. They are read
// from disk, never asked of the model: a local model call costs seconds.

// suggestionsMax is how many the first screen offers.
const suggestionsMax = 4

// starter is one thing to try: the skill that does it, the words on screen and
// the request it sends.
type starter struct {
	skill, label, prompt string
}

// starters is in the order they are offered. Skills that need a file, a URL or
// a connected account first are left out: a first suggestion that asks for
// setup is not one that works.
var starters = []starter{
	{"daily-brief", "Today's tech news in brief",
		"Give me a short daily brief on today's top tech news."},
	{"research-report", "Research a topic, with sources",
		"Research the current state of home battery storage and write a short report with sources."},
	{"file-ops", "What fills up this folder",
		"List the largest files in this folder and suggest what I could clean up."},
	{"recommendations", "Book recommendations",
		"Recommend three science-fiction novels I would probably enjoy, and say why."},
	{"check-in", "A daily check-in",
		"Good morning. Let's do a quick check-in."},
}

// skillsDir is where installed skills live, as the agent finds them:
// GAIA_CONFIG_DIR, else ~/.gaia, then skills/. A variable so tests can point it
// at a fixture.
var skillsDir = func() string {
	base := os.Getenv("GAIA_CONFIG_DIR")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return ""
		}
		base = filepath.Join(home, ".gaia")
	}
	return filepath.Join(base, "skills")
}

// installedStarters is the starters whose skill is installed, at most four.
func installedStarters(dir string) []starter {
	if dir == "" {
		return nil
	}
	var out []starter
	for _, s := range starters {
		if _, err := os.Stat(filepath.Join(dir, s.skill, "SKILL.md")); err != nil {
			continue
		}
		out = append(out, s)
		if len(out) == suggestionsMax {
			break
		}
	}
	return out
}

var (
	suggestKeyStyle   = lipgloss.NewStyle().Foreground(theme.AccentBright).Bold(true)
	suggestLabelStyle = lipgloss.NewStyle().Foreground(theme.Text)
)

// renderSuggestions is the welcome screen's list, or nothing.
func (m ChatModel) renderSuggestions() string {
	if len(m.starters) == 0 {
		return ""
	}
	width := max(m.width-8, 8)
	rows := []string{activityStyle.Render("Try one — press its number:")}
	for i, s := range m.starters {
		rows = append(rows, "  "+suggestKeyStyle.Render(fmt.Sprintf("%d", i+1))+"  "+
			suggestLabelStyle.Render(truncateRunes(s.label, width)))
	}
	return strings.Join(rows, "\n")
}

// suggestionsText is the list as plain text, for the line that says a local
// model is ready — that line replaces the welcome screen.
func suggestionsText(ss []starter) string {
	if len(ss) == 0 {
		return ""
	}
	rows := []string{"Try one — press its number:"}
	for i, s := range ss {
		rows = append(rows, fmt.Sprintf("  %d  %s", i+1, s.label))
	}
	return strings.Join(rows, "\n")
}

// suggestionsOpen reports whether the numbers still pick a suggestion: until
// the user's first message, while nothing else has the screen.
func (m ChatModel) suggestionsOpen() bool {
	return !m.hasUserMessage() && !m.streaming && !m.memoryLoading && m.memoryView == nil &&
		!(m.warming && !m.warmHidden)
}

// pickSuggestion starts the numbered suggestion when the key is one and the
// composer is empty; ok is false for every other key.
func (m ChatModel) pickSuggestion(msg tea.KeyMsg) (tea.Model, tea.Cmd, bool) {
	if msg.Type != tea.KeyRunes || len(msg.Runes) != 1 || msg.Alt || !m.suggestionsOpen() ||
		m.input.Value() != "" || m.palette.open || m.question != nil || m.confirmation != nil {
		return m, nil, false
	}
	n := int(msg.Runes[0] - '1')
	if n < 0 || n >= len(m.starters) {
		return m, nil, false
	}
	m.input.SetValue(m.starters[n].prompt)
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	return next, cmd, true
}
