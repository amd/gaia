// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// skillsCommandPrefix must match gaia_agent.stdio.SKILLS_COMMAND_PREFIX: the
// flagship's stdio loop answers `/skills` itself (run_skills_command), so it
// rides the query channel like `/model` and never reaches the LLM.
const skillsCommandPrefix = "/skills"

// isSkillsCommand reports whether a composed line is `/skills` or `/skills
// <args>` — e.g. `/skills add https://github.com/owner/repo`.
func isSkillsCommand(query string) bool {
	trimmed := strings.TrimSpace(query)
	return trimmed == skillsCommandPrefix ||
		strings.HasPrefix(trimmed, skillsCommandPrefix+" ")
}

// submitSkillsCommand sends `/skills …` to an agent that understands it, or
// refuses visibly so the line never ships as a literal question.
func (m ChatModel) submitSkillsCommand(query string) (tea.Model, tea.Cmd) {
	refuse := ""
	if !m.supportsModelCommand() {
		refuse = m.agentName + " does not manage skills (/skills) — only the gaia flagship agent does."
	} else if _, relayed := m.relayedSwitcher(); relayed {
		refuse = "/skills is not available in this session. Add skills from a link with " +
			"`gaia skill add <link>` in a terminal, or from Skills in the Agent UI."
	}
	if refuse != "" {
		m.messages = append(m.messages, Message{Role: RoleError, Content: refuse})
		m.updateViewport()
		return m, nil
	}
	m.messages = append(m.messages, Message{Role: RoleUser, Content: strings.TrimSpace(query)})
	return m.startTurn(strings.TrimSpace(query))
}
