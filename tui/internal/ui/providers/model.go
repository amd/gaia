// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package providers owns the provider setup screen shared by startup and chat.
package providers

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/theme"
	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

type SelectedMsg struct{ ID string }
type ClosedMsg struct{}
type loadedMsg struct {
	source    *lemonade.Client
	providers []lemonade.Provider
	err       error
}
type modelsMsg struct {
	source *lemonade.Client
	models []lemonade.Model
	err    error
}
type clearedMsg struct {
	source *lemonade.Client
	err    error
}

type Model struct {
	spin          spinner.Model
	ctx           context.Context
	cancel        context.CancelFunc
	search        string
	client        *lemonade.Client
	providers     []lemonade.Provider
	selected      int
	stage         string
	fields        []textinput.Model
	focus         int
	models        []lemonade.Model
	busy          bool
	activity      string
	note          string
	width, height int
}

var names = []string{"local", "fireworks", "amd"}

func New(base string, width, height int) Model {
	ctx, cancel := context.WithCancel(context.Background())
	spin := spinner.New()
	spin.Spinner = spinner.Dot
	spin.Style = lipgloss.NewStyle().Foreground(theme.AccentBright)
	return Model{spin: spin, client: lemonade.New(base), ctx: ctx, cancel: cancel, stage: "providers", width: width, height: height}
}
func (m Model) Init() tea.Cmd {
	c := m.client
	return func() tea.Msg { p, e := c.Providers(m.ctx); return loadedMsg{source: c, providers: p, err: e} }
}
func (m Model) chosen() string { return names[m.selected] }
func (m Model) fetchModels() tea.Cmd {
	c, p := m.client, m.chosen()
	return func() tea.Msg { models, e := c.Models(m.ctx, p); return modelsMsg{source: c, models: models, err: e} }
}
func (m Model) setup() Model {
	p := lemonade.Provider{Name: m.chosen(), Header: "Authorization", Prefix: "Bearer "}
	if p.Name == "fireworks" {
		p.BaseURL = lemonade.FireworksURL
	}
	for _, existing := range m.providers {
		if existing.Name == p.Name {
			p = existing
		}
	}
	// The Fireworks destination is fixed even if another client edited it.
	if p.Name == "fireworks" {
		p.BaseURL = lemonade.FireworksURL
		p.Header = "Authorization"
		p.Prefix = "Bearer "
	}
	values := []string{p.BaseURL, p.Header, p.Prefix, ""}
	m.fields = make([]textinput.Model, 4)
	for i, value := range values {
		f := textinput.New()
		f.SetValue(value)
		f.CharLimit = 2048
		f.Prompt = ""
		m.fields[i] = f
	}
	m.fields[3].EchoMode = textinput.EchoPassword
	m.fields[3].EchoCharacter = '•'
	m.fields[3].Placeholder = "Paste API key (blank uses existing key)"
	m.focus = 3
	if p.Name == "amd" && p.BaseURL == "" {
		m.focus = 0
	}
	m.fields[m.focus].Focus()
	m.stage = "setup"
	m.note = ""
	return m
}
func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.ctx.Err() != nil {
		return m, nil
	}
	switch v := msg.(type) {
	case spinner.TickMsg:
		if m.busy {
			var cmd tea.Cmd
			m.spin, cmd = m.spin.Update(msg)
			return m, cmd
		}
		return m, nil
	case tea.WindowSizeMsg:
		m.width = v.Width
		m.height = v.Height
		return m, nil
	case loadedMsg:
		if v.source != nil && v.source != m.client {
			return m, nil
		}
		m.providers = v.providers
		if v.err != nil {
			m.note = v.err.Error()
		}
		return m, nil
	case modelsMsg:
		if v.source != nil && v.source != m.client {
			return m, nil
		}
		m.busy = false
		if v.err != nil {
			m.note = v.err.Error()
			return m, nil
		}
		if len(v.models) == 0 {
			m.models = nil
			if m.stage == "models" {
				if m.chosen() == "local" {
					m.stage = "providers"
				} else {
					m = m.setup()
				}
			}
			m.note = "No chat models discovered. Check the key, model access, and gateway URL; then retry."
			if m.chosen() == "local" {
				m.note = "No local chat models downloaded. Close this panel and run setup to download one."
			}
			return m, nil
		}
		m.models = v.models
		sort.Slice(m.models, func(i, j int) bool {
			if m.models[i].ID == lemonade.FireworksModel {
				return true
			}
			if m.models[j].ID == lemonade.FireworksModel {
				return false
			}
			return m.models[i].ID < m.models[j].ID
		})
		m.stage = "models"
		m.search = ""
		m.focus = 0
		m.note = ""
		return m, m.Init()
	case clearedMsg:
		if v.source != nil && v.source != m.client {
			return m, nil
		}
		m.busy = false
		if v.err != nil {
			m.note = v.err.Error()
		} else {
			m.note = "Runtime key cleared. An environment key, if set, remains active."
		}
		return m, m.Init()
	case tea.KeyMsg:
		if v.String() == "ctrl+c" {
			m.fields = nil
			m.stage = "closing"
			m.cancel()
			return m, tea.Quit
		}
		if m.busy {
			if v.String() == "esc" {
				m.cancel()
				m.stage = "closing"
				m.fields = nil
				return m, func() tea.Msg { return ClosedMsg{} }
			}
			return m, nil
		}
		if v.String() == "esc" {
			if m.stage == "providers" {
				m.cancel()
				return m, func() tea.Msg { return ClosedMsg{} }
			}
			m.fields = nil
			m.stage = "providers"
			m.note = ""
			return m, nil
		}
		switch m.stage {
		case "providers":
			switch v.String() {
			case "up":
				m.selected = (m.selected + 2) % 3
			case "down", "tab":
				m.selected = (m.selected + 1) % 3
			case "enter":
				if m.chosen() == "local" {
					m.busy = true
					m.activity = "Loading models"
					return m, tea.Batch(m.spin.Tick, m.fetchModels())
				}
				m = m.setup()
				return m, textinput.Blink
			case "r":
				return m, m.Init()
			}
		case "models":
			models := m.filteredModels()
			switch v.String() {
			case "up":
				m.focus = max(0, m.focus-1)
			case "down", "tab":
				m.focus = max(0, min(len(models)-1, m.focus+1))
			case "enter":
				if m.focus < 0 || m.focus >= len(models) {
					return m, nil
				}
				id := models[m.focus].ID
				m.cancel()
				return m, func() tea.Msg { return SelectedMsg{ID: id} }
			case "ctrl+r":
				m.busy = true
				m.note = ""
				m.activity = "Refreshing models"
				return m, tea.Batch(m.spin.Tick, m.fetchModels())
			case "backspace":
				r := []rune(m.search)
				if len(r) > 0 {
					m.search = string(r[:len(r)-1])
					m.focus = 0
				}
			default:
				if v.Type == tea.KeyRunes {
					m.search += string(v.Runes)
					m.focus = 0
				}
			}
		case "setup":
			switch v.String() {
			case "tab", "shift+tab":
				m.fields[m.focus].Blur()
				if m.chosen() == "fireworks" {
					m.focus = 3
				} else if v.String() == "tab" {
					m.focus = (m.focus + 1) % 4
				} else {
					m.focus = (m.focus + 3) % 4
				}
				return m, m.fields[m.focus].Focus()
			case "ctrl+d":
				m.fields[3].SetValue("")
				m.busy = true
				m.note = ""
				m.activity = "Clearing key"
				c, p := m.client, m.chosen()
				return m, tea.Batch(m.spin.Tick, func() tea.Msg { return clearedMsg{source: c, err: c.Clear(m.ctx, p)} })
			case "enter":
				p := lemonade.Provider{Name: m.chosen(), BaseURL: strings.TrimSpace(m.fields[0].Value()), Header: strings.TrimSpace(m.fields[1].Value()), Prefix: m.fields[2].Value()}
				key := strings.TrimSpace(m.fields[3].Value())
				m.fields[3].SetValue("")
				m.busy = true
				m.note = ""
				m.activity = "Connecting"
				c := m.client
				return m, tea.Batch(m.spin.Tick, func() tea.Msg {
					if err := c.Configure(m.ctx, p, key); err != nil {
						return modelsMsg{source: c, err: err}
					}
					models, err := c.Models(m.ctx, p.Name)
					return modelsMsg{source: c, models: models, err: err}
				})
			}
			var cmd tea.Cmd
			m.fields[m.focus], cmd = m.fields[m.focus].Update(msg)
			return m, cmd
		}
	}
	if m.stage == "setup" {
		var cmd tea.Cmd
		m.fields[m.focus], cmd = m.fields[m.focus].Update(msg)
		return m, cmd
	}
	return m, nil
}
func (m Model) filteredModels() []lemonade.Model {
	var out []lemonade.Model
	for _, model := range m.models {
		if strings.Contains(strings.ToLower(model.ID), strings.ToLower(m.search)) {
			out = append(out, model)
		}
	}
	return out
}

func (m Model) View() string {
	w := max(1, m.width-4)
	title := lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	lines := []string{title.Render("AI provider"), ""}
	switch m.stage {
	case "providers":
		lines = append(lines, "Choose where GAIA runs chat inference.", "")
		for i, name := range names {
			desc := "On this machine · downloaded models"
			if name != "local" {
				desc = "Via Lemonade · key needed"
				for _, p := range m.providers {
					if p.Name == name && (p.EnvKey || p.RuntimeKey) {
						desc = "Via Lemonade · key configured"
					}
				}
			}
			marker := "  "
			if i == m.selected {
				marker = "› "
			}
			label := marker + lemonade.Label(name)
			if i == m.selected {
				label = title.Render(label)
			}
			lines = append(lines, label, lipgloss.NewStyle().Foreground(theme.Dim).Render("    "+desc), "")
		}
	case "setup":
		lines = append(lines, title.Render(lemonade.Label(m.chosen())))
		if m.height < 22 {
			lines = append(lines, "Remote chat · key kept until restart.")
			if m.chosen() == "fireworks" {
				lines = append(lines, "Usage may incur charges.")
			}
		} else if m.chosen() == "fireworks" {
			lines = append(lines, "Chat history is sent to Fireworks AI. Usage may incur charges.", "Suggested model: Gemma 4 31B IT", "Endpoint: "+lemonade.FireworksURL)
		} else {
			lines = append(lines, "Chat history is sent to your configured AMD gateway.")
		}
		if m.height >= 22 {
			lines = append(lines, "Keys stay in Lemonade memory until it restarts.", "Provider settings are shared by clients of this Lemonade server.")
		}
		lines = append(lines, "")
		labels := []string{"Gateway URL", "Auth header", "Prefix (include trailing space for Bearer)", "API key"}
		for i := range m.fields {
			if m.chosen() == "fireworks" && i < 3 {
				continue
			}
			f := m.fields[i]
			f.Width = max(10, w-4)
			marker := "  "
			if m.focus == i {
				marker = "› "
			}
			lines = append(lines, marker+labels[i], "  "+f.View())
		}
	case "models":
		models := m.filteredModels()
		lines = append(lines, title.Render(lemonade.Label(m.chosen())+" models"), "Enter selects a model. Existing conversation is preserved.", "Search: "+m.search, "")
		count := max(1, m.height-14)
		start := max(0, m.focus-count+1)
		for i := start; i < min(len(models), start+count); i++ {
			marker := "  "
			if i == m.focus {
				marker = "› "
			}
			label := strings.TrimPrefix(models[i].ID, m.chosen()+".")
			if models[i].ID == lemonade.FireworksModel {
				label += " · suggested"
			}
			if i == m.focus {
				lines = append(lines, title.Render(marker+label))
			} else {
				lines = append(lines, marker+label)
			}
		}
		if len(models) == 0 {
			lines = append(lines, "No matching models. Backspace to change the search.")
		} else {
			lines = append(lines, fmt.Sprintf("%d of %d", m.focus+1, len(models)))
			selected := models[m.focus]
			details := []string{}
			if selected.ContextLength > 0 {
				details = append(details, fmt.Sprintf("Context: %s tokens", formatTokens(selected.ContextLength)))
			}
			for _, label := range selected.Labels {
				switch label {
				case "tool-calling":
					details = append(details, "tool calling")
				case "vision":
					details = append(details, "images")
				case "reasoning":
					details = append(details, "reasoning")
				}
			}
			if len(details) > 0 {
				lines = append(lines, strings.Join(details, " · "))
			}
		}
	}
	if m.note != "" {
		lines = append(lines, "", m.note)
	}
	hint := "↑/↓ choose · enter continue · r refresh · esc close"
	if m.stage == "models" {
		hint = "type to search · ↑/↓ choose · enter select · ctrl+r refresh · esc back"
	}
	if m.stage == "setup" {
		hint = "tab field · enter connect · ctrl+d forget key · esc back"
	}
	if w < 65 {
		switch m.stage {
		case "models":
			hint = "type to search · ↑/↓ · enter · esc back"
		case "setup":
			hint = "tab field · enter connect · esc back"
		default:
			hint = "↑/↓ choose · enter · esc close"
		}
		if w < 40 {
			hint = "↑/↓ · enter · esc"
			if m.stage == "setup" {
				hint = "tab · enter · esc"
			}
		}
	}
	if m.busy {
		hint = m.spin.View() + " " + m.activity + "… · esc cancel"
	}
	// Keep the focused field and navigation visible on small terminals.
	var wrapped []string
	for _, line := range lines {
		wrapped = append(wrapped, strings.Split(ansi.Wrap(line, w, ""), "\n")...)
	}
	budget := max(1, m.height-4)
	if len(wrapped) > budget {
		wrapped = append(wrapped[:1], wrapped[len(wrapped)-budget+1:]...)
	}
	return lipgloss.NewStyle().Padding(1, 2).Render(strings.Join(append(wrapped, "", ansi.Truncate(hint, w, "…")), "\n"))
}

func formatTokens(n int) string {
	s := fmt.Sprint(n)
	for i := len(s) - 3; i > 0; i -= 3 {
		s = s[:i] + "," + s[i:]
	}
	return s
}
