// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// A local model server that is DOWN is worth a chip on every launch: a great
// many confusing sessions are really "Lemonade is not running", and embeddings
// for RAG and memory still go to it when chat runs on a remote model. A healthy
// one says nothing — the model chip already names where chat runs.
var lemonadeDownStyle = lipgloss.NewStyle().Foreground(theme.Danger)

// renderLemonadeChip is the header segment for a local server that is down.
//
// Empty until the agent has actually said something about it: an absent report
// (an older agent, a transport that does not send one) must not render as
// "down", which would send someone chasing a server that is fine.
func (m ChatModel) renderLemonadeChip() string {
	if !m.lemonadeKnown || m.lemonadeUp {
		return ""
	}
	return lemonadeDownStyle.Render(" │ lemonade down")
}
