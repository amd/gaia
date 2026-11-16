// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/hybrid"
)

// routeLine is a hybrid turn's footnote, "→ DeepSeek (writing, d1)", or "".
func routeLine(r *event.CanonicalRoute) string {
	if r == nil {
		return ""
	}
	return hybrid.RouteOf(*r).Line()
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
	panel := hybrid.New(m.lemonadeBaseURL, m.width, m.height)
	m.hybridPanel = &panel
	m.palette.open = false
	return m, panel.Init()
}

// updateHybridPanel routes a message to an open /hybrid screen.
func (m ChatModel) updateHybridPanel(msg tea.Msg) (tea.Model, tea.Cmd) {
	if _, ok := msg.(hybrid.ClosedMsg); ok {
		m.hybridPanel = nil
		m.updateViewport()
		return m, nil
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

// hybridPaletteDesc names hybrid by its models and where they run, as the
// provider picker does. Broken settings are reported by the switch itself.
func hybridPaletteDesc() string {
	h, err := lemonade.ReadHybridModels()
	if err != nil {
		return "Hybrid · AMD LLM Gateway · settings need fixing in /hybrid"
	}
	return lemonade.HybridDisplayName(h) + " " + lemonade.HybridWhere(h) + " · AMD LLM Gateway"
}
