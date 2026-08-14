// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// The local model server's state belongs on screen in --dev, because a great
// many confusing sessions are really "Lemonade is not running" or "Lemonade is
// older than this build needs". Reported even when chat runs on Claude:
// embeddings for RAG and memory still go to Lemonade, so a remote chat model
// does not make it harmless for Lemonade to be down.
var (
	lemonadeChipStyle = lipgloss.NewStyle().Foreground(theme.Dim)
	lemonadeDownStyle = lipgloss.NewStyle().Foreground(theme.Danger)
)

// renderLemonadeChip is the --dev header segment naming the local server.
//
// Empty until the agent has actually said something about it: an absent report
// (an older agent, a transport that does not send one) must not render as
// "down", which would send someone chasing a server that is fine.
func (m ChatModel) renderLemonadeChip() string {
	if !m.dev || !m.lemonadeKnown {
		return ""
	}
	if !m.lemonadeUp {
		return lemonadeDownStyle.Render(" │ lemonade down")
	}
	if m.lemonadeVersion == "" {
		// Reachable but unversioned — say reachable rather than invent a number.
		return lemonadeChipStyle.Render(" │ lemonade up")
	}
	return lemonadeChipStyle.Render(" │ lemonade " + m.lemonadeVersion)
}
