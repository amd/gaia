// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
)

// skillsCommandPrefix must match gaiapy.stdio.SKILLS_COMMAND_PREFIX: the
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

// Over the daemon relay the agent has no route that adds a skill, so `/skills`
// only lists there: the palette says so, and the other forms answer with what
// does work.
const (
	skillsListOnlyDesc  = "List the skills this chat can run"
	skillsListOnlyUsage = "/skills lists the skills this chat can run. Type /<skill-name> to run one."
	skillsAddElsewhere  = "Adding a skill from a link isn't available in this chat yet. " +
		"Add it in the Agent UI under Settings › Skills, then type /skills here to see it."
)

// skillsListOnly reports a chat where `/skills` lists and cannot add.
func (m ChatModel) skillsListOnly() bool {
	if _, relayed := m.relayedSwitcher(); !relayed {
		return false
	}
	_, lists := m.client.(client.SkillLister)
	return lists
}

// skillsCommandListedMsg answers a bare `/skills` over the daemon relay.
type skillsCommandListedMsg struct {
	skills []event.SkillCommand
	err    error
}

// submitSkillsCommand sends `/skills …` to an agent that understands it, or
// refuses visibly so the line never ships as a literal question.
func (m ChatModel) submitSkillsCommand(query string) (tea.Model, tea.Cmd) {
	line := strings.TrimSpace(query)
	refuse := ""
	if !m.supportsModelCommand() {
		refuse = m.agentName + " does not manage skills (/skills) — only the gaia flagship agent does."
	} else if _, relayed := m.relayedSwitcher(); relayed {
		// The relay's /query runs the agent loop, not the stdio command
		// dispatch, so the list is read from the agent's skills route instead.
		if lister, ok := m.client.(client.SkillLister); ok && line == skillsCommandPrefix {
			m.messages = append(m.messages, Message{Role: RoleUser, Content: line})
			m.updateViewport()
			return m, func() tea.Msg {
				ctx, cancel := context.WithTimeout(context.Background(), skillListTimeout)
				defer cancel()
				skills, err := lister.ListSkills(ctx)
				return skillsCommandListedMsg{skills: skills, err: err}
			}
		}
		refuse = skillsListOnlyUsage
		if args := strings.Fields(line[len(skillsCommandPrefix):]); len(args) > 0 && args[0] == "add" {
			refuse = skillsAddElsewhere
		}
	}
	if refuse != "" {
		m.messages = append(m.messages, Message{Role: RoleError, Content: refuse})
		m.updateViewport()
		return m, nil
	}
	m.messages = append(m.messages, Message{Role: RoleUser, Content: line})
	return m.startTurn(line)
}

// showListedSkills writes a relayed `/skills` answer into the transcript, in
// the same words the flagship's own `/skills` uses.
func (m ChatModel) showListedSkills(msg skillsCommandListedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: fmt.Sprintf("Could not list the agent's skills: %v", msg.err),
		})
		m.updateViewport()
		return m, nil
	}
	m.setReportedSkills(msg.skills)
	names := make([]string, 0, len(msg.skills))
	for _, s := range msg.skills {
		names = append(names, "`"+s.Name+"`")
	}
	listing := strings.Join(names, ", ")
	if listing == "" {
		listing = "none"
	}
	m.messages = append(m.messages, Message{
		Role: RoleAssistant,
		Content: fmt.Sprintf("**Installed skills (%d):** %s\n\n", len(msg.skills), listing) +
			"Type `/<name>` to run one.",
	})
	m.updateViewport()
	return m, nil
}
