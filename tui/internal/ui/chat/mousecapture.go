// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	tea "github.com/charmbracelet/bubbletea"
)

// Who owns the mouse, and why — the terminal by default (drag-select works;
// the wheel scrolls through alternate scroll mode), the app only when asked.
//
// There are two independent reasons the app wants the mouse:
//
//  1. The user asked for it with Ctrl+T (APP MOUSE, selectmode.go) to click
//     links and double-click to copy.
//  2. An interactive overlay is open (the "/" palette, a mid-run question)
//     and needs clicks — see overlayOpen. This one is never a user choice: it
//     is scoped to exactly the frames the overlay is on screen, since an
//     overlay you cannot click is worse than one you cannot select text out of.
//
// mouseCaptured (model.go) is the actual, reconciled state. Deriving it fresh
// on every Update (applyMouseCapture, called from the top-level Update
// wrapper) rather than letting each caller flip it directly is what makes the
// two reasons composable: an overlay opening while the app already holds the
// mouse must not re-issue a redundant escape sequence, and an overlay closing
// must hand the mouse back to the terminal rather than silently keeping the
// capture the overlay needed.
//
// overlayOpen also decides whether motion (hover, no button held) is worth
// asking the terminal for: an overlay needs it (clicking the row you are
// already hovering is what "click" means once hover works at all), plain
// wheel mode does not, and All-Motion tracking is noticeably chattier over
// SSH than Cell-Motion — see applyMouseCapture.
func (m ChatModel) overlayOpen() bool {
	return m.palette.open || m.question != nil
}

// applyMouseCapture reconciles m.mouseCaptured (and which motion mode it is
// in) with what is currently wanted, returning the tea.Cmd that changes the
// terminal's mouse mode — or nil when nothing changed. Called once per
// Update, from the top-level ChatModel.Update wrapper, so every path that can
// open or close an overlay (a keystroke, a canonical needs_input/
// needs_confirmation event, the turn settling) is covered from one place
// rather than requiring every such call site to remember to reconcile it.
func (m *ChatModel) applyMouseCapture() tea.Cmd {
	wantOn := m.appMouse || m.overlayOpen()
	wantAll := m.overlayOpen()

	switch {
	case !wantOn && m.mouseCaptured:
		m.mouseCaptured = false
		m.mouseCaptureAllMotion = false
		return tea.DisableMouse

	case wantOn && (!m.mouseCaptured || wantAll != m.mouseCaptureAllMotion):
		steppingDown := m.mouseCaptured && m.mouseCaptureAllMotion && !wantAll
		m.mouseCaptured = true
		m.mouseCaptureAllMotion = wantAll
		if wantAll {
			return tea.EnableMouseAllMotion
		}
		if steppingDown {
			// bubbletea's EnableMouseCellMotion sends only ?1002h — it never
			// clears ?1003, and a terminal tracking the two flags
			// independently keeps streaming every pointer movement while our
			// state says Cell-Motion. Drop everything first, then re-arm.
			return tea.Sequence(tea.DisableMouse, tea.EnableMouseCellMotion)
		}
		return tea.EnableMouseCellMotion

	default:
		return nil
	}
}

// isWheelEvent reports whether msg is a scroll-wheel tick rather than a
// click or hover — tea.MouseMsg is a defined type over tea.MouseEvent, so
// MouseEvent.IsWheel isn't promoted and has to be called via a conversion.
func isWheelEvent(msg tea.MouseMsg) bool {
	return tea.MouseEvent(msg).IsWheel()
}
