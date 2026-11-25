// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// The first screen suggests up to four things to try, each one a skill the
// agent has and that needs no further setup, picked with Tab and Enter. The list
// is the agent's own report of its skills, which includes the ones built into
// it; the skills folder is read only until that report arrives. The model is
// never asked: a local model call costs seconds.

// suggestionsMax is how many the first screen offers.
const suggestionsMax = 4

const starterHint = "Try one: Tab to select, Enter to run"

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

// skillsDir is where the user's own skills live: skills/ under GAIA's home,
// which is GAIA_HOME, then GAIA_CONFIG_DIR, then ~/.gaia (gaia.home.gaia_home).
// The skills built into the agent are not here. A variable so tests can point
// it at a fixture.
var skillsDir = userSkillsDir

func userSkillsDir() string {
	for _, name := range []string{"GAIA_HOME", "GAIA_CONFIG_DIR"} {
		if dir := strings.TrimSpace(os.Getenv(name)); dir != "" {
			return filepath.Join(os.ExpandEnv(expandHome(dir)), "skills")
		}
	}
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return ""
	}
	return filepath.Join(home, ".gaia", "skills")
}

// expandHome replaces a leading ~ with the user's home directory.
func expandHome(dir string) string {
	if dir != "~" && !strings.HasPrefix(dir, "~/") && !strings.HasPrefix(dir, `~\`) {
		return dir
	}
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return dir
	}
	return filepath.Join(home, dir[1:])
}

// startersWhere is the starters whose skill has() reports, at most four.
func startersWhere(has func(skill string) bool) []starter {
	var out []starter
	for _, s := range starters {
		if !has(s.skill) {
			continue
		}
		out = append(out, s)
		if len(out) == suggestionsMax {
			break
		}
	}
	return out
}

// installedStarters is the starters whose skill is in the folder.
func installedStarters(dir string) []starter {
	if dir == "" {
		return nil
	}
	return startersWhere(func(skill string) bool {
		_, err := os.Stat(filepath.Join(dir, skill, "SKILL.md"))
		return err == nil
	})
}

// reportedStarters is the starters whose skill the agent says it has.
func reportedStarters(skills []event.SkillCommand) []starter {
	return startersWhere(func(skill string) bool {
		for _, s := range skills {
			if s.Name == skill {
				return true
			}
		}
		return false
	})
}

// skillListTimeout bounds the startup skill list: asking may start the agent.
const skillListTimeout = 60 * time.Second

// skillsListedMsg is the agent's answer to listSkillsCmd.
type skillsListedMsg struct {
	skills []event.SkillCommand
	err    error
}

// listSkillsCmd asks the agent which skills it has, on a transport whose agent
// does not say so by itself (the daemon relay).
func (m ChatModel) listSkillsCmd() tea.Cmd {
	lister, ok := m.client.(client.SkillLister)
	if !ok {
		return nil
	}
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), skillListTimeout)
		defer cancel()
		skills, err := lister.ListSkills(ctx)
		return skillsListedMsg{skills: skills, err: err}
	}
}

// setReportedSkills takes the agent's own list: the "/" palette's skill rows
// and the first screen's suggestions. Replaced whole, so a skill removed since
// the last report does not linger.
func (m *ChatModel) setReportedSkills(skills []event.SkillCommand) {
	m.skillCommands = skills
	if m.suggests {
		reported := reportedStarters(skills)
		if !slices.Equal(m.starters, reported) {
			m.starterFocus = 0
		}
		m.starters = reported
		m.refreshReadyLine()
	}
}

// readyTail is what the line that says a local model is ready ends with: the
// suggestions, or the plain hint when there are none.
func readyTail(ss []starter) string {
	if list := suggestionsText(ss); list != "" {
		return "\n\n" + list
	}
	return "\n" + firstPromptHint
}

// refreshReadyLine rewrites the Ready line's ending when the suggestions
// change after it was written: the agent's skill report can arrive after the
// warm-up ends, and the line must show the list the user can pick from.
func (m *ChatModel) refreshReadyLine() {
	if m.readyTail == "" || m.hasUserMessage() {
		return
	}
	tail := readyTail(m.starters)
	if tail == m.readyTail {
		return
	}
	for i := len(m.messages) - 1; i >= 0; i-- {
		msg := &m.messages[i]
		if msg.Role == RoleStatus && strings.HasSuffix(msg.Content, m.readyTail) {
			msg.Content = strings.TrimSuffix(msg.Content, m.readyTail) + tail
			m.readyTail = tail
			return
		}
	}
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
	rows := []string{activityStyle.Render(starterHint)}
	for i, s := range m.starters {
		prefix := "  "
		if m.starterFocus == i+1 {
			prefix = "> "
		}
		rows = append(rows, prefix+suggestKeyStyle.Render(fmt.Sprintf("%d", i+1))+"  "+
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
	rows := []string{starterHint}
	for i, s := range ss {
		rows = append(rows, fmt.Sprintf("  %d  %s", i+1, s.label))
	}
	return strings.Join(rows, "\n")
}

// suggestionsShown reports whether the list is on screen: on the welcome
// screen, or in the line that says a local model is ready. A notice that
// replaces the welcome screen hides the list, which then cannot be focused.
func (m ChatModel) suggestionsShown() bool {
	if len(m.starters) == 0 {
		return false
	}
	if len(m.messages) == 0 {
		return true
	}
	list := suggestionsText(m.starters)
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, list) {
			return true
		}
	}
	return false
}

// canFocusStarter holds until the first message, while the list is visible
// and neither the composer nor another control owns the input.
func (m ChatModel) canFocusStarter() bool {
	return m.starterFocus >= 0 && m.starterFocus <= len(m.starters) &&
		m.suggestionsShown() && !m.hasUserMessage() && !m.streaming && !m.memoryLoading &&
		m.memoryView == nil && !(m.warming && !m.warmHidden) && !m.setupChecking && !m.setupRunning &&
		m.input.Value() == "" && !m.help.Open && !m.pick.open && !m.palette.open &&
		m.question == nil && m.confirmation == nil && !m.sel.active
}

// renderReadyStarterFocus marks the focused row without changing the stored
// Ready line: its original text is also how we know the list is still shown.
func (m ChatModel) renderReadyStarterFocus(body string) string {
	if m.starterFocus <= 0 || m.starterFocus > len(m.starters) ||
		m.readyTail == "" || !strings.HasSuffix(body, m.readyTail) {
		return body
	}
	n := m.starterFocus
	label := m.starters[n-1].label
	line := fmt.Sprintf("%d  %s", n, label)
	return strings.TrimSuffix(body, m.readyTail) + strings.Replace(m.readyTail, "\n  "+line, "\n> "+line, 1)
}

func starterNavigationKey(msg tea.KeyMsg) bool {
	return !msg.Alt && !msg.Paste && (msg.Type == tea.KeyTab || msg.Type == tea.KeyEnter || msg.Type == tea.KeyEsc)
}

func (m *ChatModel) clearStarterFocus() {
	if m.starterFocus != 0 {
		m.starterFocus = 0
		m.updateViewport()
	}
}

// handleStarterKey makes sending a starter an explicit two-step action. Tab
// returns to the composer after the last row, and Escape leaves it directly.
func (m ChatModel) handleStarterKey(msg tea.KeyMsg) (tea.Model, tea.Cmd, bool) {
	if starterNavigationKey(msg) && m.canFocusStarter() {
		switch msg.Type {
		case tea.KeyTab:
			m.starterFocus = (m.starterFocus + 1) % (len(m.starters) + 1)
			m.updateViewport()
			return m, nil, true
		case tea.KeyEsc:
			if m.starterFocus != 0 {
				m.clearStarterFocus()
				return m, nil, true
			}
		case tea.KeyEnter:
			if m.starterFocus != 0 {
				m.input.SetValue(m.starters[m.starterFocus-1].prompt)
				m.starterFocus = 0
				next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
				return next, cmd, true
			}
		}
	}
	return m, nil, false
}
