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
// The terminal's selection can only cover what is on screen, though: the
// transcript is an app-drawn viewport, and dragging past its edge has nothing
// to scroll. Ctrl+T hands the mouse to the app instead (APP MOUSE): a drag
// selects inside the app, scrolls the transcript when it passes the top or
// bottom edge, and copies on release (selection.go); a printed link opens on
// click and a double-click copies a whole message. An open overlay takes the
// mouse either way — see overlayOpen.

// toggleAppMouse gives the mouse to the app or back to the terminal (Ctrl+T).
//
// It only flips the user's OWN wish (appMouse); applyMouseCapture reconciles
// that against whatever an overlay separately wants and issues the real escape
// sequence, so toggling while an overlay is open can never fight it.
func (m ChatModel) toggleAppMouse() (tea.Model, tea.Cmd) {
	m.appMouse = !m.appMouse
	m.sel = textSelection{}
	cmd := m.applyMouseCapture()
	content := "Mouse back to your terminal — drag to select, and copy and " +
		"paste as usual. The wheel still scrolls."
	if m.appMouse {
		content = "Mouse to GAIA — drag to select and copy; dragging past the " +
			"top or bottom edge scrolls. Click a link to open it, double-click " +
			"a message to copy it. Ctrl+T gives the mouse back."
	}
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: content})
	m.updateViewport()
	return m, cmd
}
