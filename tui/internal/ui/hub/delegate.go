package hub

import (
	"fmt"
	"io"

	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/catalog"
)

var (
	categoryStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("243")).
			Italic(true)

	versionStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("238"))

	selectedCursor = lipgloss.NewStyle().
			Foreground(lipgloss.Color("212")).
			Bold(true).
			Render("▸ ")

	normalCursor = "  "
)

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
		cursor = selectedCursor
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

	// Line 3: category tag
	cat := "    " + categoryStyle.Render(agent.Category)

	fmt.Fprintf(w, "%s\n%s\n%s", line1, desc, cat)
}

func statusDotFor(status catalog.AgentStatus) string {
	switch status {
	case catalog.StatusActive:
		return activeDot
	case catalog.StatusIdle:
		return idleDot
	case catalog.StatusInstalled:
		return installedDot
	case catalog.StatusAvailable:
		return availableDot
	case catalog.StatusComingSoon:
		return comingSoonDot
	default:
		return " "
	}
}
