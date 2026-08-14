// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"github.com/charmbracelet/lipgloss"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Selection mode hands the mouse back to the terminal.
//
// The transcript needs mouse reporting so the wheel can scroll it — in an
// alt-screen app the terminal's own scrollback does not exist. The same
// reporting takes click-drag away from the terminal, which is what people use to
// select text. Shift+drag overrides it in some terminals and not others, so it is
// not an answer we can promise on every OS.
//
// Ctrl+Y and Ctrl+B copy the whole answer or the last code block, which covers
// the common cases but not "that one path in the middle of a paragraph". So this
// turns mouse reporting off outright: selection, copy AND paste all become the
// terminal's own, identically on Windows Terminal, iTerm2, kitty and the rest.
//
// The trade is real and immediate — the wheel stops scrolling — so it is stated
// in a band that cannot be scrolled away, the same rule the bypass banner
// follows. A mode that silently breaks scrolling reads as a freeze.
const (
	selectBannerText = "SELECTION MODE — drag to select, your terminal's own " +
		"copy and paste. Ctrl+T or Esc to scroll again."
	// For a terminal too narrow for the sentence. Still names the mode and the
	// thing the user will notice is missing.
	selectBannerShort = "SELECTION MODE — wheel off"
)

var selectBannerStyle = lipgloss.NewStyle().
	Bold(true).
	Foreground(theme.OnFill).
	Background(theme.AccentFillBG)

// renderSelectBanner draws the always-visible band, or "" when the mode is off.
func (m ChatModel) renderSelectBanner() string {
	if !m.selectMode {
		return ""
	}
	text := selectBannerText
	if lipgloss.Width(text) > m.width {
		text = selectBannerShort
	}
	return selectBannerStyle.Width(m.width).Render(text)
}

// toggleSelectMode turns mouse reporting off (or back on) and says so.
func (m ChatModel) toggleSelectMode() (tea.Model, tea.Cmd) {
	m.selectMode = !m.selectMode
	if m.selectMode {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "Selection mode on — drag to select and use your terminal's " +
				"own copy and paste. The mouse wheel will not scroll until you " +
				"turn it off with Ctrl+T or Esc; the arrow keys and PgUp/PgDn " +
				"still work.",
		})
		m.updateViewport()
		return m, tea.DisableMouse
	}
	m.messages = append(m.messages, Message{
		Role:    RoleStatus,
		Content: "Selection mode off — the mouse wheel scrolls the transcript again.",
	})
	m.updateViewport()
	return m, tea.EnableMouseCellMotion
}
