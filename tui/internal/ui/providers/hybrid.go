// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"context"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/hybrid"
)

// Hybrid config reads, vars so tests need no config file.
var (
	readHybridModels  = lemonade.ReadHybridModels
	hybridLocalRefuse = lemonade.HybridLocalOnlyRefusal
)

// hybridRow is hybrid's entry beside the providers: its name from the
// configured pair, where it runs, and why it can't be chosen when it can't.
type hybridRow struct {
	label  string
	reason string
}

// fetchHybridEntry is the Agent UI's GET /api/hybrid/entry. A var so tests
// reach no server.
var fetchHybridEntry = hybrid.FetchEntry

// hybridEntryMsg is the server's hybrid entry, or why it has none.
type hybridEntryMsg struct {
	entry hybrid.Entry
	err   error
}

func (m Model) fetchEntry() hybridEntryMsg {
	c, cancel := context.WithTimeout(m.ctx, 5*time.Second)
	defer cancel()
	e, err := fetchHybridEntry(c)
	return hybridEntryMsg{entry: e, err: err}
}

// withEntry takes the server's entry; with no server the row stays as read
// from config here.
func (m Model) withEntry(v hybridEntryMsg) Model {
	switch {
	case v.err == nil:
		row := hybridRow{label: v.entry.Label()}
		if v.entry.Reason != nil {
			row.reason = *v.entry.Reason
		}
		if !v.entry.Available && row.reason == "" {
			row.reason = "no hybrid pairing can be used here yet; set one up in /hybrid"
		}
		m.hybrid = row
	case !hybrid.IsServerAbsent(v.err):
		m.hybrid.reason = "the Agent UI server couldn't name it: " + v.err.Error()
	}
	return m
}

func readHybridRow() hybridRow {
	h, err := readHybridModels()
	if err != nil {
		return hybridRow{label: "Hybrid", reason: strings.TrimSuffix(err.Error(), ".") + ". Fix it in /hybrid"}
	}
	row := hybridRow{label: lemonade.HybridEntryLabel(h) + " " + lemonade.HybridWhere(h)}
	if err := hybridLocalRefuse(); err != nil {
		row.reason = err.Error()
	}
	return row
}

func (r hybridRow) desc() string {
	if r.reason != "" {
		return "Can't use: " + r.reason
	}
	return "Each turn on the cheap or the strong model · /hybrid changes the models and policy"
}

// chooseHybrid is enter on the hybrid row: its members are checked when the
// session switches, as for any model.
func (m Model) chooseHybrid() (Model, tea.Cmd) {
	if m.hybrid.reason != "" {
		m.note = m.hybrid.label + " can't be used: " + m.hybrid.reason
		return m, nil
	}
	m.cancel()
	return m, func() tea.Msg { return SelectedMsg{ID: lemonade.HybridModelID} }
}
