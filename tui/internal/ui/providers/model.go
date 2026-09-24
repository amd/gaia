// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package providers owns the provider setup screen shared by startup and chat.
package providers

import (
	"context"
	"fmt"
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
	source   *lemonade.Client
	entries  []lemonade.Entry
	capacity string
	err      error
}
type pullProgressMsg struct {
	source *lemonade.Client
	p      lemonade.PullProgress
}
type pullDoneMsg struct {
	source *lemonade.Client
	id     string
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
	entries       []lemonade.Entry
	capacity      string
	pulling       *lemonade.Entry
	pullCh        chan tea.Msg
	pullCancel    context.CancelFunc
	pullLine      string
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

// keyStatus reports whether the chosen provider already has a credential,
// read live from m.providers rather than a value snapshotted at setup() time
// — so it stays correct across a key clear (Ctrl+D) or a provider chosen
// before the initial Providers() fetch has landed.
func (m Model) keyStatus() (env, runtime bool) {
	for _, p := range m.providers {
		if p.Name == m.chosen() {
			return p.EnvKey, p.RuntimeKey
		}
	}
	return false, false
}
func (m Model) fetchModels() tea.Cmd {
	c, p, ctx := m.client, m.chosen(), m.ctx
	return func() tea.Msg { return loadCatalog(ctx, c, p) }
}

// loadCatalog lists provider's models. For local ones it first reads what this
// PC can hold; when that fails every download is refused rather than guessed.
func loadCatalog(ctx context.Context, c *lemonade.Client, provider string) modelsMsg {
	var capacity lemonade.Capacity
	var capErr error
	line := ""
	if provider == "local" {
		capacity, capErr = c.Capacity(ctx)
		if capErr != nil {
			line = "Downloads disabled: could not read this PC's memory from Lemonade (" + capErr.Error() + ")"
		} else {
			line = fmt.Sprintf("This PC: %.0f GB for models (%s)", capacity.MemoryGB, capacity.MemorySource)
			if capacity.DiskFreeGB >= 0 {
				line += fmt.Sprintf(" · %.0f GB disk free", capacity.DiskFreeGB)
			}
		}
	}
	entries, err := c.Catalog(ctx, provider, capacity, capErr)
	return modelsMsg{source: c, entries: entries, capacity: line, err: err}
}

func waitPull(ch chan tea.Msg) tea.Cmd {
	return func() tea.Msg {
		msg, ok := <-ch
		if !ok {
			return nil
		}
		return msg
	}
}

// startPull downloads e in the background and reports progress on m.pullCh.
func (m Model) startPull(e lemonade.Entry) (Model, tea.Cmd) {
	ctx, cancel := context.WithCancel(m.ctx)
	ch := make(chan tea.Msg, 16)
	c := m.client
	go func() {
		err := c.Pull(ctx, e, func(p lemonade.PullProgress) {
			// Drop progress rather than stall the download, and keep the last
			// slot free so the final message below never blocks once the
			// panel stops reading (the user pressed esc).
			if len(ch) < cap(ch)-1 {
				ch <- pullProgressMsg{source: c, p: p}
			}
		})
		ch <- pullDoneMsg{source: c, id: e.Model.ID, err: err}
		close(ch)
	}()
	m.stage = "download"
	m.pulling = &e
	m.pullCh = ch
	m.pullCancel = cancel
	m.pullLine = "Starting download"
	m.note = ""
	return m, tea.Batch(m.spin.Tick, waitPull(ch))
}

func entryName(e lemonade.Entry, provider string) string {
	if e.Recommended != nil && e.Recommended.Label != "" {
		return e.Recommended.Label
	}
	return strings.TrimPrefix(e.Model.ID, provider+".")
}

func entryStatus(e lemonade.Entry) string {
	switch {
	case e.Unavailable():
		return "unavailable"
	case e.Model.Cloud():
		return ""
	case e.Model.Downloaded:
		return "downloaded"
	case e.FitUnknown:
		return "fit unknown"
	case e.Fits:
		return fmt.Sprintf("download %.1f GB", e.SizeGB())
	case e.SizeGB() > 0:
		return fmt.Sprintf("won't fit · %.0f GB", e.SizeGB())
	default:
		return "won't fit"
	}
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
	switch env, runtime := m.keyStatus(); {
	case env:
		m.fields[3].Placeholder = "Blank uses the environment key"
	case runtime:
		m.fields[3].Placeholder = "Blank keeps the saved key"
	default:
		m.fields[3].Placeholder = "Paste API key"
	}
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
		if m.busy || m.stage == "download" {
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
		m.capacity = v.capacity
		if len(v.entries) == 0 {
			m.entries = nil
			if m.stage == "models" {
				if m.chosen() == "local" {
					m.stage = "providers"
				} else {
					m = m.setup()
				}
			}
			m.note = "No chat models discovered. Check the key, model access, and gateway URL; then retry."
			if m.chosen() == "local" {
				m.note = "Lemonade lists no local chat models. Update Lemonade, then retry."
			}
			return m, nil
		}
		m.entries = v.entries
		m.stage = "models"
		m.search = ""
		m.focus = 0
		for i, e := range m.entries {
			if e.Selectable() {
				m.focus = i
				break
			}
		}
		m.note = ""
		return m, m.Init()
	case pullProgressMsg:
		if v.source != m.client || m.stage != "download" {
			return m, nil
		}
		m.pullLine = pullLine(v.p)
		return m, waitPull(m.pullCh)
	case pullDoneMsg:
		if v.source != m.client || m.stage != "download" {
			return m, nil
		}
		if m.pullCancel != nil {
			m.pullCancel()
			m.pullCancel = nil
		}
		m.pulling = nil
		if v.err != nil {
			m.stage = "models"
			m.note = v.err.Error()
			return m, nil
		}
		id := v.id
		m.cancel()
		return m, func() tea.Msg { return SelectedMsg{ID: id} }
	case clearedMsg:
		if v.source != nil && v.source != m.client {
			return m, nil
		}
		m.busy = false
		if v.err != nil {
			m.note = v.err.Error()
		} else {
			m.note = "Runtime key cleared. An environment key, if set, remains active."
			// Reflect the clear immediately rather than waiting on the
			// m.Init() refresh below to land — otherwise the "key already
			// configured" notice keeps claiming a key that was just removed
			// for however long that request takes.
			for i := range m.providers {
				if m.providers[i].Name == m.chosen() {
					m.providers[i].RuntimeKey = false
				}
			}
		}
		return m, m.Init()
	case tea.KeyMsg:
		if v.String() == "ctrl+c" {
			m.fields = nil
			m.stage = "closing"
			m.cancel()
			return m, tea.Quit
		}
		if m.stage == "download" {
			if v.String() == "esc" && m.pullCancel != nil {
				m.pullCancel()
				m.pullCancel = nil
				m.pulling = nil
				m.stage = "models"
				m.note = "Download stopped."
			}
			return m, nil
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
			entries := m.filteredEntries()
			switch v.String() {
			case "up":
				m.focus = max(0, m.focus-1)
			case "down", "tab":
				m.focus = max(0, min(len(entries)-1, m.focus+1))
			case "enter":
				if m.focus < 0 || m.focus >= len(entries) {
					return m, nil
				}
				e := entries[m.focus]
				if !e.Selectable() {
					reason := e.Reason
					if reason == "" && e.Recommended != nil {
						reason = e.Recommended.Note
					}
					m.note = entryName(e, m.chosen()) + " can't be used here: " + reason
					return m, nil
				}
				if e.NeedsDownload() {
					return m.startPull(e)
				}
				id := e.Model.ID
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
					m.note = ""
				}
			default:
				if v.Type == tea.KeyRunes {
					m.search += string(v.Runes)
					m.focus = 0
					m.note = ""
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
					return loadCatalog(m.ctx, c, p.Name)
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
func (m Model) filteredEntries() []lemonade.Entry {
	var out []lemonade.Entry
	needle := strings.ToLower(m.search)
	for _, e := range m.entries {
		if strings.Contains(strings.ToLower(e.Model.ID), needle) || strings.Contains(strings.ToLower(entryName(e, m.chosen())), needle) {
			out = append(out, e)
		}
	}
	return out
}

func pullLine(p lemonade.PullProgress) string {
	parts := []string{}
	if p.TotalFiles > 1 {
		parts = append(parts, fmt.Sprintf("file %d of %d", p.FileIndex, p.TotalFiles))
	}
	if p.Total > 0 {
		parts = append(parts, fmt.Sprintf("%.1f of %.1f GB", float64(p.Downloaded)/1e9, float64(p.Total)/1e9))
	}
	parts = append(parts, fmt.Sprintf("%.0f%%", p.Percent))
	return strings.Join(parts, " · ")
}

func (m Model) View() string {
	w := max(1, m.width-4)
	title := lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	lines := []string{title.Render("AI provider"), ""}
	switch m.stage {
	case "providers":
		lines = append(lines, "Choose where GAIA runs chat inference.", "")
		for i, name := range names {
			desc := "On this machine · models that fit this PC"
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
		success := lipgloss.NewStyle().Foreground(theme.Success)
		switch env, runtime := m.keyStatus(); {
		case env && m.height < 22:
			lines = append(lines, success.Render("Environment key active — blank keeps it."))
		case runtime && m.height < 22:
			lines = append(lines, success.Render("Key already saved — blank keeps it."))
		case env:
			lines = append(lines, success.Render(
				"An environment key is already active for "+lemonade.Label(m.chosen())+" and takes precedence over any key entered below — leave API key blank to keep using it."))
		case runtime:
			lines = append(lines, success.Render(
				"A key is already configured for "+lemonade.Label(m.chosen())+" — leave API key blank to keep using it, or paste a new one to replace it."))
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
	case "download":
		e := m.pulling
		name := ""
		if e != nil {
			name = entryName(*e, m.chosen())
		}
		lines = append(lines, title.Render("Downloading "+name), "Lemonade is downloading the model to this PC. It is selected when the download finishes.", "", m.spin.View()+" "+m.pullLine)
	case "models":
		entries := m.filteredEntries()
		dim := lipgloss.NewStyle().Foreground(theme.Dim)
		lines = append(lines, title.Render(lemonade.Label(m.chosen())+" models"), "Enter selects a model. Existing conversation is preserved.")
		if m.capacity != "" {
			lines = append(lines, dim.Render(m.capacity))
		}
		lines = append(lines, "Search: "+m.search, "")
		count := max(1, m.height-18) // two rows go to the group headers
		start := max(0, m.focus-count+1)
		for i := start; i < min(len(entries), start+count); i++ {
			e := entries[i]
			if e.Recommended != nil && (i == start || entries[i-1].Recommended == nil) {
				lines = append(lines, title.Render("Recommended"))
			}
			if e.Recommended == nil && (i == start || entries[i-1].Recommended != nil) {
				lines = append(lines, title.Render("All models"))
			}
			marker := "  "
			if i == m.focus {
				marker = "› "
			}
			label := marker + entryName(e, m.chosen())
			if e.Recommended != nil {
				label = marker + "★ " + entryName(e, m.chosen())
			}
			if status := entryStatus(e); status != "" {
				label += " · " + status
			}
			switch {
			case i == m.focus:
				lines = append(lines, title.Render(label))
			case !e.Selectable():
				lines = append(lines, dim.Render(label))
			default:
				lines = append(lines, label)
			}
		}
		if len(entries) == 0 {
			lines = append(lines, "No matching models. Backspace to change the search.")
		} else {
			lines = append(lines, fmt.Sprintf("%d of %d", m.focus+1, len(entries)))
			selected := entries[m.focus]
			details := []string{}
			if name := entryName(selected, m.chosen()); name != selected.Model.ID {
				details = append(details, selected.Model.ID)
			}
			if selected.Model.ContextLength > 0 {
				details = append(details, fmt.Sprintf("Context: %s tokens", formatTokens(selected.Model.ContextLength)))
			}
			for _, label := range selected.Model.Labels {
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
			if selected.Recommended != nil && selected.Recommended.Note != "" && selected.Selectable() {
				lines = append(lines, dim.Render(selected.Recommended.Note))
			}
			if !selected.Selectable() && selected.Reason != "" {
				lines = append(lines, dim.Render("Can't use: "+selected.Reason))
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
	if m.stage == "download" {
		hint = "esc stop download"
	}
	if w < 65 {
		switch m.stage {
		case "models":
			hint = "type to search · ↑/↓ · enter · esc back"
		case "setup":
			hint = "tab field · enter connect · esc back"
		case "download":
			hint = "esc stop download"
		default:
			hint = "↑/↓ choose · enter · esc close"
		}
		if w < 40 {
			hint = "↑/↓ · enter · esc"
			switch m.stage {
			case "setup":
				hint = "tab · enter · esc"
			case "download":
				hint = "esc stop"
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
