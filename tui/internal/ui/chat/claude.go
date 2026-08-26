// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
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
// The flags reach the AGENT through its own argv; this reads them back off
// the transport so the chip is driven by what was actually passed to the
// child, never by a second copy that could disagree with it.
func (m ChatModel) applyLaunchClaude() ChatModel {
	type launchClaude interface{ ClaudeAtLaunch() bool }
	c, ok := m.client.(launchClaude)
	if !ok || !c.ClaudeAtLaunch() {
		return m
	}
	m.claudeMode = true
	if withModel, ok := m.client.(interface{ ClaudeModelAtLaunch() string }); ok {
		m.launchClaudeModel = withModel.ClaudeModelAtLaunch()
	}
	m.messages = append(m.messages, Message{
		Role: RoleStatus,
		Content: "Launched with --use-claude: " + m.agentName + " runs on " +
			claudeLaunchName(m.launchClaudeModel) +
			", not the local backend — this conversation is sent to Anthropic.",
	})
	return m
}

// claudeLaunchName names the model in prose: "Claude Haiku 4.5" when the id
// is one this build knows, the raw id when it is not, and "Anthropic's Claude
// API" when the launch named none at all.
func claudeLaunchName(modelID string) string {
	if modelID == "" {
		return "Anthropic's Claude API"
	}
	if known, ok := client.KnownClaudeModel(modelID); ok {
		return "Claude " + known.Label
	}
	return modelID
}

// claudeChipLabel is the remote header segment's text — "claude · haiku-4.5",
// never a bare "claude" once any model id is known.
//
// Telling Haiku from Sonnet from Opus at a glance is the whole point: they
// differ in cost and capability by an order of magnitude, and a session that
// silently reverted (see handleCanonicalEvent's restart detection) looks
// identical to one that did not if the chip only ever says "claude".
func claudeChipLabel(modelID string) string {
	if modelID == "" {
		return "claude"
	}
	return "claude · " + client.ClaudeModelShortName(modelID)
}

// renderClaudeChip is the header segment for a remote session before the
// agent's first model-state ping has arrived — see renderModelChip. It names
// the launch flag's model, which is a REQUEST rather than what resolved, so
// the ping still overrides it the moment one lands.
func (m ChatModel) renderClaudeChip() string {
	if !m.claudeMode {
		return ""
	}
	return claudeChipStyle.Render(" │ " + claudeChipLabel(m.launchClaudeModel))
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
// renderClaudeChip. That fallback is not rare: the ping is the first line the
// child writes, but the transport does not scan its stdout until the first
// turn, so a session the user opens and reads before typing has no ping at
// all — which is exactly when the header was showing a bare "claude".
func (m ChatModel) renderModelChip() string {
	if m.modelDisplay == "" {
		return m.renderClaudeChip()
	}
	if m.modelRemote {
		return claudeChipStyle.Render(" │ " + claudeChipLabel(m.modelID))
	}
	return modelChipStyle.Render(" │ " + m.modelDisplay)
}
