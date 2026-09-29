// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	tea "github.com/charmbracelet/bubbletea"
)

// Who owns the mouse.
//
// By DEFAULT the terminal does, so drag-to-select and the terminal's own copy
// and paste work the way they do everywhere else. Scrolling does not need the
// mouse: the program turns on alternate scroll mode (ui/app.go), under which
// the terminal sends each wheel tick as ↑/↓ — and those keys already scroll the
// transcript.
//
// Ctrl+T hands the mouse to the app instead (APP MOUSE): a printed link opens
// on click and a double-click copies a whole message, at the cost of plain
// drag-select (most terminals still select with Shift held — Option in
// iTerm2). An open overlay takes the mouse either way — see overlayOpen.

// toggleAppMouse gives the mouse to the app or back to the terminal (Ctrl+T).
//
// It only flips the user's OWN wish (appMouse); applyMouseCapture reconciles
// that against whatever an overlay separately wants and issues the real escape
// sequence, so toggling while an overlay is open can never fight it.
func (m ChatModel) toggleAppMouse() (tea.Model, tea.Cmd) {
	m.appMouse = !m.appMouse
	cmd := m.applyMouseCapture()
	content := "Mouse back to your terminal — drag to select, and copy and " +
		"paste as usual. The wheel still scrolls."
	if m.appMouse {
		content = "Mouse to GAIA — click a link to open it, double-click a " +
			"message to copy it. Hold Shift (Option in iTerm2) to drag-select; " +
			"Ctrl+T gives the mouse back."
	}
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: content})
	m.updateViewport()
	return m, cmd
}
