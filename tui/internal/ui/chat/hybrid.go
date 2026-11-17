// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/ui/hybrid"
)

// routeLine is a hybrid turn's footnote, "→ DeepSeek (writing, d1)", or "".
func routeLine(r *event.CanonicalRoute) string {
	if r == nil {
		return ""
	}
	return hybrid.RouteLine(*r)
}

// noteHybridDegraded says once that the classifier keeps failing, so every
// turn is running on the strong model, and what fixes it.
func (m *ChatModel) noteHybridDegraded(r *event.CanonicalRoute) {
	if r == nil || !r.Degraded || r.Notice == "" {
		m.hybridNotice = ""
		return
	}
	if r.Notice == m.hybridNotice {
		return
	}
	m.hybridNotice = r.Notice
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: r.Notice})
}

// openHybridSettings shows /hybrid in place of the transcript.
func (m ChatModel) openHybridSettings() (tea.Model, tea.Cmd) {
	panel := hybrid.New(m.width, m.height)
	m.hybridPanel = &panel
	m.palette.open = false
	return m, panel.Init()
}

// updateHybridPanel routes a message to an open /hybrid screen.
func (m ChatModel) updateHybridPanel(msg tea.Msg) (tea.Model, tea.Cmd) {
	if _, ok := msg.(hybrid.ClosedMsg); ok {
		m.hybridPanel = nil
		m.updateViewport()
		// The pair may have changed, and the palette names hybrid by it.
		return m, fetchHybridEntryCmd()
	}
	if size, ok := msg.(tea.WindowSizeMsg); ok {
		m.width = size.Width
		m.height = size.Height
		m.resize()
	}
	updated, cmd := m.hybridPanel.Update(msg)
	panel := updated.(hybrid.Model)
	m.hybridPanel = &panel
	return m, cmd
}

// hybridEntryMsg is hybrid's picker entry from the backend, or why there is none.
type hybridEntryMsg struct {
	entry hybridapi.Entry
	err   error
}

// newHybridEntryAPI reads the entry without starting GAIA's background
// service. A var so tests reach no daemon.
var newHybridEntryAPI = func() hybridapi.API { return hybridapi.NewAttached() }

// fetchHybridEntryCmd reads hybrid's entry once, for the /model palette row.
func fetchHybridEntryCmd() tea.Cmd {
	return func() tea.Msg {
		e, err := newHybridEntryAPI().Entry(context.Background())
		return hybridEntryMsg{entry: e, err: err}
	}
}

// refreshHybridEntry re-reads hybrid's entry once each time the /model
// palette opens, since /provider, /gateway or another app may have changed
// what it can use.
func (m *ChatModel) refreshHybridEntry() tea.Cmd {
	open := m.palette.open && strings.HasPrefix(strings.ToLower(m.input.Value()), modelPalettePrefix)
	if !open {
		m.hybridEntryAsked = false
		return nil
	}
	if m.hybridEntryAsked {
		return nil
	}
	m.hybridEntryAsked = true
	return fetchHybridEntryCmd()
}

// hybridPaletteDesc is the /model palette's hybrid row, from the entry last
// read: its pair and where it runs, or why it can't be used. "" when the
// backend says no hybrid pairing can run here, so the row is left out.
func hybridPaletteDesc(e *hybridapi.Entry, err error) string {
	switch {
	case e != nil && !e.Shown:
		return ""
	case err != nil:
		return "Hybrid · its settings couldn't be read; /hybrid says why"
	case e == nil:
		return "Hybrid · routes each turn to a cheap or a strong model"
	case e.Why() != "":
		return e.Label() + " · can't use yet; /hybrid says why"
	}
	return e.Label() + " · routes each turn"
}
