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
	// A DOWN server is worth saying on any backend, because embeddings still
	// need it — a Claude session answers normally and then fails at the first
	// document question.
	if !m.lemonadeUp {
		return lemonadeDownStyle.Render(" │ lemonade down")
	}
	// A healthy one is only worth a chip when it is doing the thinking. Beside
	// a remote model it read as the backend — "GAIA │ dev │ Sonnet 5 │ lemonade
	// 10.10.0" was reported as "you're still running Lemonade, not Sonnet",
	// which is exactly the thing the model chip exists to make unambiguous.
	if m.modelRemote {
		return ""
	}
	if m.lemonadeVersion == "" {
		// Reachable but unversioned — say reachable rather than invent a number.
		return lemonadeChipStyle.Render(" │ lemonade up")
	}
	return lemonadeChipStyle.Render(" │ lemonade " + m.lemonadeVersion)
}
