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
// By DEFAULT the terminal does, so drag-select and the platform's own copy and
// paste work exactly as they do everywhere else — no mode to discover, no
// shortcut to learn, on Windows Terminal, iTerm2, GNOME Terminal and the rest.
//
// Capturing it (mode 1002) buys one thing: the wheel scrolling the transcript,
// which an alt-screen app cannot get from the terminal's scrollback because it
// has none. It costs selection entirely. That trade used to be made for every
// user on every launch, and the report it produced was "I still can't drag my
// mouse pointer over terminal text and copy it".
//
// So capture is opt-in, via Ctrl+T. While it is on, selection is broken, and
// that is stated in a band that cannot be scrolled away — the rule the bypass
// banner follows. A mode that silently breaks selection reads as a bug.
const (
	wheelBannerText = "MOUSE WHEEL MODE — the wheel scrolls, but drag-select is " +
		"off. Ctrl+T or Esc to select text again."
	// For a terminal too narrow for the sentence. Still names the mode and the
	// thing the user will notice is missing.
	wheelBannerShort = "WHEEL MODE — selection off"
)

var wheelBannerStyle = lipgloss.NewStyle().Foreground(theme.Dim)

// renderSelectBanner draws the always-visible band while WHEEL MODE is on,
// or "" otherwise. Keyed on mouseWheelOn rather than mouseCaptured on
// purpose: an overlay (the palette, a question) also captures the mouse
// while it is open, but that capture is scoped and silent — the overlay
// itself is the visible signal there, and a banner claiming "the wheel
// scrolls, drag-select is off" would be actively wrong while it is up (a
// click there selects a row, not text).
func (m ChatModel) renderSelectBanner() string {
	if !m.mouseWheelOn {
		return ""
	}
	text := wheelBannerText
	if lipgloss.Width(text) > m.width {
		text = wheelBannerShort
	}
	return wheelBannerStyle.Width(m.width).Render(text)
}

// toggleSelectMode hands the mouse to the app or back to the terminal, for
// wheel scrolling — Ctrl+T, or Esc while it is on (see handleKey).
//
// It only flips the user's OWN wish (mouseWheelOn); applyMouseCapture is what
// actually reconciles that against whatever an overlay separately wants and
// issues the real escape sequence, so toggling this while an overlay happens
// to be open can never fight it — the mouse stays captured either way, and
// releases only once neither reason still wants it.
func (m ChatModel) toggleSelectMode() (tea.Model, tea.Cmd) {
	m.mouseWheelOn = !m.mouseWheelOn
	cmd := m.applyMouseCapture()
	if m.mouseWheelOn {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "Mouse wheel scrolling on — the wheel now scrolls the " +
				"transcript, but you cannot drag to select text while it is. " +
				"Ctrl+T or Esc gives selection back.",
		})
	} else {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "Selection back — drag to select and use your terminal's own " +
				"copy and paste. The arrow keys and PgUp/PgDn still scroll.",
		})
	}
	m.updateViewport()
	return m, cmd
}
