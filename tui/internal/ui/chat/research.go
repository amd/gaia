// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Research mode. `/research <topic>` sends one research turn: the agent loads
// its research-report skill and writes a report citing every page it read
// (gaia_agent.research). A bare `/research` switches research mode on or off,
// the TUI's form of the Agent UI's Research switch: while it is on, every
// message is sent as `/research <message>`.

const researchCommand = "/research"

var researchChipStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Accent)

// isResearchCommand reports whether a line starts with the /research command,
// bare or with a topic. "/researcher" is not it.
func isResearchCommand(query string) bool {
	trimmed := strings.TrimLeftFunc(query, unicode.IsSpace)
	if !strings.HasPrefix(strings.ToLower(trimmed), researchCommand) {
		return false
	}
	rest := trimmed[len(researchCommand):]
	return rest == "" || unicode.IsSpace(rune(rest[0]))
}

// researchTopic is what follows /research, "" for a bare one.
func researchTopic(query string) string {
	trimmed := strings.TrimLeftFunc(query, unicode.IsSpace)
	return strings.TrimSpace(trimmed[len(researchCommand):])
}

// withResearch is the line to send: prefixed while research mode is on.
func (m ChatModel) withResearch(query string) string {
	if !m.researchMode || strings.TrimSpace(query) == "" || isResearchCommand(query) {
		return query
	}
	return researchCommand + " " + query
}

// runResearchCommand handles a line that starts with /research.
func (m ChatModel) runResearchCommand(query string) (tea.Model, tea.Cmd) {
	if !m.supportsModelCommand() {
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: "Research is available with the GAIA flagship agent. " +
				m.agentName + " would read /research as an ordinary message.",
		})
		m.updateViewport()
		return m, nil
	}
	if researchTopic(query) != "" {
		return m.sendQuery(query)
	}
	m.researchMode = !m.researchMode
	if m.researchMode {
		return m.statusNote("Research mode on: each message is researched on the web " +
			"and answered with a report citing the pages GAIA read. Type /research again to turn it off."), nil
	}
	return m.statusNote("Research mode off."), nil
}

// renderResearchChip is the header segment while research mode is on.
func (m ChatModel) renderResearchChip() string {
	if !m.researchMode {
		return ""
	}
	return researchChipStyle.Render(" │ research")
}
