// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// `/agent` shows which agent program answers as GAIA and switches between the
// two. The host owns the switch: it relaunches the agent, which this model
// cannot do to itself.
const agentCommand = "/agent"

// AgentProgramMsg asks the host to show the agent programs (empty Arg) or
// switch to the one Arg names.
type AgentProgramMsg struct {
	Arg string
}

func isAgentCommand(query string) bool {
	trimmed := strings.TrimSpace(query)
	return trimmed == agentCommand || strings.HasPrefix(trimmed, agentCommand+" ")
}

// WithAgentPrograms marks this chat as hosted by a screen that answers
// AgentProgramMsg, so /agent has somewhere to go.
func (m ChatModel) WithAgentPrograms() ChatModel {
	m.agentProgramHost = true
	return m
}

func (m ChatModel) runAgentCommand(arg string) (tea.Model, tea.Cmd) {
	switch {
	case m.agentID != setupAgentID:
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: m.agentName + " runs on one program. Only the gaia flagship agent " +
				"has two to choose from (/agent).",
		})
	case !m.agentProgramHost:
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: "/agent cannot switch the agent program here: this chat was started on one " +
				"program directly. Start gaia-cli without --subprocess, or pick the program " +
				"at launch with --agent-program.",
		})
	default:
		return m, func() tea.Msg { return AgentProgramMsg{Arg: arg} }
	}
	m.updateViewport()
	return m, nil
}
