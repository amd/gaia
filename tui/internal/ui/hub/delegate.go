package hub

import (
	"fmt"
	"io"

	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

var (
	categoryStyle = lipgloss.NewStyle().
			Foreground(theme.Dim).
			Italic(true)

	versionStyle = lipgloss.NewStyle().
			Foreground(theme.Faint)

	selectedCursorStyle = lipgloss.NewStyle().
				Foreground(theme.Highlight).
				Bold(true)

	normalCursor = "  "
)

// Rendered per call — see the note on the dot helpers in styles.go.
func selectedCursor() string { return selectedCursorStyle.Render("▸ ") }

type agentDelegate struct{}

func newAgentDelegate() agentDelegate {
	return agentDelegate{}
}

func (d agentDelegate) Height() int  { return 3 }
func (d agentDelegate) Spacing() int { return 1 }
func (d agentDelegate) Update(_ tea.Msg, _ *list.Model) tea.Cmd {
	return nil
}

func (d agentDelegate) Render(w io.Writer, m list.Model, index int, item list.Item) {
	agent, ok := item.(catalog.Agent)
	if !ok {
		return
	}

	isSelected := index == m.Index()

	dot := statusDotFor(agent.Status)

	// Line 1: cursor + dot + icon + name + version
	ver := ""
	if agent.Version != "" {
		ver = " " + versionStyle.Render("v"+agent.Version)
	}

	name := agent.Icon + " " + agent.Name
	if isSelected {
		name = selectedItemStyle.Render(name)
	} else {
		name = normalItemStyle.Render(name)
	}

	cursor := normalCursor
	if isSelected {
		cursor = selectedCursor()
	}

	line1 := cursor + dot + " " + name + ver

	// Line 2: description
	desc := agent.Description
	if agent.Status == catalog.StatusComingSoon && agent.Votes > 0 {
		desc += voteStyle.Render(fmt.Sprintf(" ▲ %d", agent.Votes))
	}
	if isSelected {
		desc = "    " + selectedDescStyle.Render(desc)
	} else {
		desc = "    " + descriptionStyle.Render(desc)
	}

	// Line 3: category tag + what pressing a key here would do
	cat := "    " + categoryStyle.Render(agent.Category+meta(agent))

	// Truncate to the list width. lipgloss.JoinVertical pads every block up to
	// the widest one, so a single over-long row does not wrap by itself — it
	// widens the WHOLE frame past the terminal and wraps every line in it.
	width := m.Width()
	fmt.Fprintf(w, "%s\n%s\n%s",
		clip(line1, width), clip(desc, width), clip(cat, width))
}

// clip shortens an already-styled line to width display columns.
func clip(s string, width int) string {
	if width <= 0 {
		return s
	}
	return ansi.Truncate(s, width, "…")
}

// meta is the short suffix that answers "what can I do with this row" — the
// download size for something installable, the reason for something that is
// listed but cannot be installed.
func meta(agent catalog.Agent) string {
	switch {
	case agent.UpdateAvailable:
		return "  ·  update to " + agent.LatestVersion + " available (i)"
	case agent.Installable():
		return "  ·  " + catalog.FormatSize(agent.DownloadSizeBytes) + " · i to install"
	case agent.NotOfferedReason != "":
		return "  ·  not out — " + agent.NotOfferedReason
	default:
		return ""
	}
}

func statusDotFor(status catalog.AgentStatus) string {
	switch status {
	case catalog.StatusActive:
		return activeDot()
	case catalog.StatusIdle:
		return idleDot()
	case catalog.StatusInstalled:
		return installedDot()
	case catalog.StatusAvailable:
		return availableDot()
	case catalog.StatusComingSoon:
		return comingSoonDot()
	default:
		return " "
	}
}
