// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/control"
)

// repaintOnResize forces a full repaint whenever the terminal changes size.
//
// Bubble Tea redraws only the rows it believes changed. A terminal that
// reflows its buffer on resize (Windows Terminal does) moves the previous
// frame's rows before that diff runs, so a row the renderer skipped is left
// behind — a narrowed window showed the status bar twice, the old wider copy
// under the new one. Clearing on a real size change is what makes the next
// frame the only thing on screen.
type repaintOnResize struct {
	inner         tea.Model
	width, height int
}

func (r repaintOnResize) Init() tea.Cmd { return r.inner.Init() }

func (r repaintOnResize) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	next, cmd := r.inner.Update(msg)
	r.inner = next
	size, ok := msg.(tea.WindowSizeMsg)
	if !ok {
		return r, cmd
	}
	// The first size is the initial layout, not a change: there is no old
	// frame on screen to leave behind.
	changed := r.width != 0 && (size.Width != r.width || size.Height != r.height)
	r.width, r.height = size.Width, size.Height
	if !changed {
		return r, cmd
	}
	return r, tea.Batch(cmd, tea.ClearScreen)
}

func (r repaintOnResize) View() string { return r.inner.View() }

// ControlSnapshot keeps the control API's diagnostics reading the wrapped
// screen rather than reporting an unknown view.
func (r repaintOnResize) ControlSnapshot() control.Snapshot {
	if sp, ok := r.inner.(control.SnapshotProvider); ok {
		return sp.ControlSnapshot()
	}
	return control.Snapshot{View: control.ViewUnknown}
}
