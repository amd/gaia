// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Claude mode: the agent's inference runs against Anthropic's Claude API
// instead of the local Lemonade backend, so the conversation leaves the
// machine. That is a mode the user chose at launch, not a danger — it gets a
// persistent header chip, not bypass's warning band.

var claudeChipStyle = lipgloss.NewStyle().
	Bold(true).
	Foreground(theme.Warning)

// applyLaunchClaude reflects a --use-claude launch into the model.
//
// The flag reaches the AGENT through its own argv; this reads it back off the
// transport so the chip is driven by what was actually passed to the child,
// never by a second bool that could disagree with it.
func (m ChatModel) applyLaunchClaude() ChatModel {
	type launchClaude interface{ ClaudeAtLaunch() bool }
	if c, ok := m.client.(launchClaude); ok && c.ClaudeAtLaunch() {
		m.claudeMode = true
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "Launched with --use-claude: " + m.agentName +
				" runs on Anthropic's Claude API, not the local backend — this " +
				"conversation is sent to Anthropic.",
		})
	}
	return m
}

// renderClaudeChip is the header segment saying inference is remote, or ""
// when the session runs locally. This is the PRE-PING fallback only — see
// renderModelChip — because it can only ever say "claude" (the launch flag
// carries no specific model id), never which one.
func (m ChatModel) renderClaudeChip() string {
	if !m.claudeMode {
		return ""
	}
	return claudeChipStyle.Render(" │ claude")
}

// modelChipStyle is the local-model header segment: the same slot
// renderClaudeChip fills for a remote session, styled as ordinary text
// because running locally is the default, not something to flag.
var modelChipStyle = lipgloss.NewStyle().Foreground(theme.Text)

// renderModelChip names the specific model actually running, colored to
// match where inference happens: remote (Claude) gets the same warning color
// as renderClaudeChip, local gets ordinary text — never a bare "claude" with
// no model name attached.
//
// Before the agent's first model-state ping arrives (see
// handleCanonicalEvent) modelDisplay is still empty, so this falls back to
// renderClaudeChip: the launch flag is the only fact known that early, and it
// can only say a launch REQUESTED Claude, not which model resolved.
func (m ChatModel) renderModelChip() string {
	if m.modelDisplay == "" {
		return m.renderClaudeChip()
	}
	style := modelChipStyle
	if m.modelRemote {
		style = claudeChipStyle
	}
	return style.Render(" │ " + m.modelDisplay)
}
