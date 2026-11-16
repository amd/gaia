// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
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

func readHybridRow() hybridRow {
	h, err := readHybridModels()
	if err != nil {
		return hybridRow{label: "Hybrid", reason: strings.TrimSuffix(err.Error(), ".") + ". Fix it in /hybrid"}
	}
	row := hybridRow{label: lemonade.HybridDisplayName(h) + " " + lemonade.HybridWhere(h)}
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
