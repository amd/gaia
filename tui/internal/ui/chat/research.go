// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"regexp"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Research mode. `/research <topic>` sends one research turn: the agent loads
// its research-report skill and writes a report citing every page it read
// (gaiapy.research). A bare `/research` switches research mode on or off,
// the TUI's form of the Agent UI's Research switch: while it is on, every
// message is sent as `/research <message>`.

const researchCommand = "/research"

// researchCommands start a research turn: the research-report skill's own
// /research-report command is the same turn as /research.
var researchCommands = []string{"/research-report", researchCommand}

var researchChipStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Accent)

// researchCommandLen is the length of the research command a line starts
// with, after leading space, or -1 when it starts with none. "/researcher" is
// not one.
func researchCommandLen(trimmed string) int {
	lower := strings.ToLower(trimmed)
	for _, command := range researchCommands {
		if !strings.HasPrefix(lower, command) {
			continue
		}
		rest := trimmed[len(command):]
		if rest == "" || unicode.IsSpace(rune(rest[0])) {
			return len(command)
		}
	}
	return -1
}

// isResearchCommand reports whether a line starts with a research command,
// bare or with a topic.
func isResearchCommand(query string) bool {
	return researchCommandLen(strings.TrimLeftFunc(query, unicode.IsSpace)) >= 0
}

// researchTopic is what follows the research command, "" for a bare one.
func researchTopic(query string) string {
	trimmed := strings.TrimLeftFunc(query, unicode.IsSpace)
	n := researchCommandLen(trimmed)
	if n < 0 {
		return ""
	}
	return strings.TrimSpace(trimmed[n:])
}

// withResearch is the line to send: prefixed while research mode is on,
// unless the line already runs research or an installed skill.
func (m ChatModel) withResearch(query string) string {
	if !m.researchMode || strings.TrimSpace(query) == "" || isResearchCommand(query) || m.isSkillCommand(query) {
		return query
	}
	return researchCommand + " " + query
}

// skillCommandRE is the shape the agent reads as a skill command: /<name>
// followed by space or the end of the line.
var skillCommandRE = regexp.MustCompile(`^/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s|$)`)

// isSkillCommand reports whether a line starts with /<name> for an installed
// skill. A mistyped name or a path such as /tmp/notes.txt is not one.
func (m ChatModel) isSkillCommand(query string) bool {
	match := skillCommandRE.FindStringSubmatch(strings.TrimLeftFunc(query, unicode.IsSpace))
	if match == nil {
		return false
	}
	// Not reported yet: the agent decides, as it would with research mode off.
	if m.skillCommands == nil {
		return true
	}
	for _, s := range m.skillCommands {
		if s.Name == match[1] {
			return true
		}
	}
	return false
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
