// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"github.com/charmbracelet/lipgloss"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Who owns the mouse.
//
// By DEFAULT the app does (mode 1002), and it buys two things an alt-screen
// program cannot get any other way: the wheel scrolls the transcript — there
// is no terminal scrollback behind an alt screen to scroll instead — and a
// printed link can be clicked open. Leaving the mouse to the terminal meant
// the wheel moved nothing at all, which reads as "the TUI lost my history".
//
// It costs the terminal's own drag-select. Most terminals still select while
// an app tracks the mouse if you hold Shift (Option in iTerm2), and Ctrl+T
// hands the mouse back outright for the ones that do not — that is SELECT
// MODE, and because it silently stops the wheel from scrolling it says so in
// a band that cannot be scrolled away, the rule the bypass banner follows.
const (
	selectBannerText = "SELECT MODE — drag to select text, but the wheel no " +
		"longer scrolls and links are not clickable. Ctrl+T or Esc to go back."
	// For a terminal too narrow for the sentence. Still names the mode and the
	// thing the user will notice is missing.
	selectBannerShort = "SELECT MODE — wheel off"
)

var selectBannerStyle = lipgloss.NewStyle().Foreground(theme.Dim)

// renderSelectBanner draws the always-visible band while SELECT MODE is on,
// or "" otherwise — which is every ordinary frame, since the default mode
// breaks nothing and so has nothing to announce.
func (m ChatModel) renderSelectBanner() string {
	if !m.mouseSelectMode {
		return ""
	}
	text := selectBannerText
	if lipgloss.Width(text) > m.width {
		text = selectBannerShort
	}
	return selectBannerStyle.Width(m.width).Render(text)
}

// toggleSelectMode hands the mouse back to the terminal or takes it again —
// Ctrl+T, or Esc while SELECT MODE is on (see handleKey).
//
// It only flips the user's OWN wish (mouseSelectMode); applyMouseCapture is
// what reconciles that against whatever an overlay separately wants and
// issues the real escape sequence, so toggling this while an overlay happens
// to be open can never fight it — the mouse stays captured either way, and
// releases only once neither reason still wants it.
func (m ChatModel) toggleSelectMode() (tea.Model, tea.Cmd) {
	m.mouseSelectMode = !m.mouseSelectMode
	cmd := m.applyMouseCapture()
	if m.mouseSelectMode {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "Select mode on — drag to select and use your terminal's own " +
				"copy and paste. The wheel no longer scrolls and links are not " +
				"clickable; Ctrl+T or Esc gives both back. The arrow keys and " +
				"PgUp/PgDn scroll either way.",
		})
	} else {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "Select mode off — the wheel scrolls the transcript again and " +
				"links are clickable. Hold Shift (Option in iTerm2) to drag-select " +
				"without leaving this mode.",
		})
	}
	m.updateViewport()
	return m, cmd
}
