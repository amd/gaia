// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"context"
	"errors"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// newHybridAPI is the backend that names hybrid's row. A var so tests reach
// no daemon.
var newHybridAPI = func() hybridapi.API { return hybridapi.New() }

// hybridRow is hybrid's entry beside the providers: its name from the
// configured pair, where it runs, and why it can't be chosen when it can't.
type hybridRow struct {
	label   string
	reason  string
	loading bool
}

// hybridEntryMsg is the backend's hybrid entry, or why there is none.
type hybridEntryMsg struct {
	entry hybridapi.Entry
	err   error
}

// fetchEntry reads hybrid's entry on its own, so the providers list never
// waits on it.
func (m Model) fetchEntry() tea.Cmd {
	ctx := m.ctx
	return func() tea.Msg {
		e, err := newHybridAPI().Entry(ctx)
		return hybridEntryMsg{entry: e, err: err}
	}
}

func (m Model) withEntry(v hybridEntryMsg) Model {
	switch {
	case errors.Is(v.err, context.Canceled):
	case v.err != nil:
		m.hybrid = hybridRow{label: "Hybrid", reason: v.err.Error()}
	default:
		m.hybrid = hybridRow{label: v.entry.Label(), reason: v.entry.Why()}
	}
	return m
}

func (r hybridRow) desc() string {
	switch {
	case r.loading:
		return "Checking hybrid's settings…"
	case r.reason != "":
		return "Can't use: " + r.reason
	}
	return "Each turn on the cheap or the strong model · /hybrid changes the models and policy"
}

// chooseHybrid is enter on the hybrid row: its members are checked when the
// session switches, as for any model.
func (m Model) chooseHybrid() (Model, tea.Cmd) {
	switch {
	case m.hybrid.loading:
		m.note = "Still checking hybrid's settings; try again in a moment."
		return m, nil
	case m.hybrid.reason != "":
		m.note = m.hybrid.label + " can't be used: " + m.hybrid.reason
		return m, nil
	}
	m.cancel()
	return m, func() tea.Msg { return SelectedMsg{ID: lemonade.HybridModelID} }
}
