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
// unless the line already runs research or an installed skill. A skill
// command typed in any case is sent with its name in lowercase.
func (m ChatModel) withResearch(query string) string {
	if strings.TrimSpace(query) == "" || isResearchCommand(query) {
		return query
	}
	if line, ok := m.asSkillCommand(query); ok {
		return line
	}
	if !m.researchMode {
		return query
	}
	return researchCommand + " " + query
}

// skillCommandRE is one whitespace-separated word the agent reads as a skill
// command: /<name>, or /<plugin>:<name> for a Claude Code plugin's skill, then
// any sentence punctuation. The word may start the line or come later in it
// (gaia.skills.command holds the same rule). The agent lowercases the name;
// the TUI sends it lowercased too, so the line shows what runs.
var skillCommandRE = regexp.MustCompile(`^/((?:[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*:)?[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*)[.,!?;:\x{060C}\x{061F}]*$`)

// wordRE splits a line into the words a skill command is looked for in.
var wordRE = regexp.MustCompile(`\S+`)

// asSkillCommand is query with the first installed skill it names lowercased,
// and true. A mistyped name or a path such as /tmp/notes.txt is not one.
// Before the agent has reported its skills, a line holding any word of that
// shape is sent as typed: the agent decides, as it would with research mode off.
func (m ChatModel) asSkillCommand(query string) (string, bool) {
	for _, word := range wordRE.FindAllStringIndex(query, -1) {
		match := skillCommandRE.FindStringSubmatchIndex(query[word[0]:word[1]])
		if match == nil {
			continue
		}
		if m.skillCommands == nil {
			return query, true
		}
		start, end := word[0]+match[2], word[0]+match[3]
		name := strings.ToLower(query[start:end])
		for _, s := range m.skillCommands {
			if strings.ToLower(s.Name) == name {
				return query[:start] + name + query[end:], true
			}
		}
	}
	return "", false
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
