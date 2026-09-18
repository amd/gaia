// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package agents owns the in-chat "installed agents" panel: list what the
// daemon has installed, whether each is running, and let the user pick one
// to switch to without leaving chat.
package agents

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/theme"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

// SelectedMsg reports the agent id the user picked. The host is responsible
// for switching to it — this package only lists and selects.
type SelectedMsg struct{ ID string }

// ClosedMsg reports the user backed out (Esc) without picking anything.
type ClosedMsg struct{}

// HubAgentLister is the slice of catalog.HubClient this panel needs: which
// agents are installed, and which of them are currently running. Declared
// here — not the concrete *catalog.HubClient — so the panel is testable with
// a stub and the host injects the real client. This is the first
// internal/ui package to reach across into internal/catalog at all.
type HubAgentLister interface {
	Agents(ctx context.Context, start bool) ([]catalog.AgentRuntime, error)
	Catalog(ctx context.Context, start, installedOnly, refresh bool) (*catalog.HubCatalog, error)
}

// knownDisplayNames covers the agents this binary ships with, since a
// hub-installed sidecar's cached hub index may not exist yet — the daemon
// then falls back to `{"id": id, "name": id}` and every row would otherwise
// print the id twice. Never guessed for an id not in this map.
var knownDisplayNames = map[string]string{
	catalog.FlagshipID: "GAIA",
	"email":            "Email",
}

// row is one installed agent as this panel renders it.
type row struct {
	id      string
	name    string
	version string
	running bool
}

type loadedMsg struct {
	source HubAgentLister
	// ctx is the context the fetch ran under, so a result from a load the
	// panel has since abandoned can be told apart from the current one.
	ctx          context.Context
	rows         []row
	onlyFlagship bool
	err          error
}

// Model lists installed agents and lets the user select one. It never
// launches or stops anything itself — SelectedMsg/ClosedMsg tell the host
// what to do.
type Model struct {
	ctx    context.Context
	cancel context.CancelFunc
	client HubAgentLister

	rows         []row
	onlyFlagship bool
	loading      bool
	loadErr      string

	selected      int
	width, height int
}

// New builds a panel that will load its data on Init. client must not be nil.
func New(client HubAgentLister, width, height int) Model {
	ctx, cancel := context.WithCancel(context.Background())
	return Model{ctx: ctx, cancel: cancel, client: client, loading: true, width: width, height: height}
}

func (m Model) Init() tea.Cmd { return m.load() }

// abandonLoad drops whatever fetch is in flight and hands back a fresh
// context for the next one.
//
// A cancelled context must never make the panel itself unusable: a host can
// re-show the same panel after a selection it did not follow through on (the
// user backed out of the switch gate), and a panel that treated its own
// cancellation as death swallowed every key from then on — Esc included,
// leaving no way out of the list.
func (m *Model) abandonLoad() {
	m.cancel()
	m.ctx, m.cancel = context.WithCancel(context.Background())
}

// load attaches to a running daemon only (start=false), matching `gaia tui
// status` — listing installed agents must not spawn a daemon behind the
// user's back.
func (m Model) load() tea.Cmd {
	c := m.client
	ctx := m.ctx
	return func() tea.Msg {
		cat, err := c.Catalog(ctx, false, true, false)
		if err != nil {
			return loadedMsg{source: c, ctx: ctx, err: err}
		}
		// Zero agents (a fresh pip install the daemon is up for another
		// reason) has no other agent to switch to either — same message as
		// the flagship-only case.
		if len(cat.Agents) == 0 || (len(cat.Agents) == 1 && cat.Agents[0].ID == catalog.FlagshipID) {
			return loadedMsg{source: c, ctx: ctx, onlyFlagship: true}
		}

		runtimes, err := c.Agents(ctx, false)
		if err != nil {
			return loadedMsg{source: c, ctx: ctx, err: err}
		}
		running := make(map[string]bool, len(runtimes))
		for _, rt := range runtimes {
			running[rt.AgentID] = rt.State == "running"
		}

		rows := make([]row, 0, len(cat.Agents))
		for _, a := range cat.Agents {
			name := a.Name
			if name == a.ID {
				if known, ok := knownDisplayNames[a.ID]; ok {
					name = known
				}
			}
			rows = append(rows, row{id: a.ID, name: name, version: a.InstalledVersion, running: running[a.ID]})
		}
		sort.Slice(rows, func(i, j int) bool {
			if rows[i].id == catalog.FlagshipID {
				return true
			}
			if rows[j].id == catalog.FlagshipID {
				return false
			}
			return rows[i].id < rows[j].id
		})
		return loadedMsg{source: c, ctx: ctx, rows: rows}
	}
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch v := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = v.Width
		m.height = v.Height
		return m, nil
	case loadedMsg:
		// Drop a result from a load the panel has abandoned (a selection the
		// host did not follow through on, then a re-show) — it would
		// overwrite the live list with a "context canceled" error.
		if (v.source != nil && v.source != m.client) || (v.ctx != nil && v.ctx != m.ctx) {
			return m, nil
		}
		m.loading = false
		m.rows = v.rows
		m.onlyFlagship = v.onlyFlagship
		if v.err != nil {
			m.loadErr = "Could not list installed agents: " + v.err.Error()
		} else {
			m.loadErr = ""
		}
		if m.selected >= len(m.rows) {
			m.selected = 0
		}
		return m, nil
	case tea.KeyMsg:
		switch v.String() {
		case "ctrl+c":
			// Quits, matching providers.Model and the status bar's own
			// "Ctrl+C quit" on every frame — a panel where it silently meant
			// Esc would make leaving take two presses.
			m.abandonLoad()
			return m, tea.Quit
		case "esc":
			m.abandonLoad()
			return m, func() tea.Msg { return ClosedMsg{} }
		case "up":
			if len(m.rows) > 0 {
				m.selected = (m.selected - 1 + len(m.rows)) % len(m.rows)
			}
		case "down", "tab":
			if len(m.rows) > 0 {
				m.selected = (m.selected + 1) % len(m.rows)
			}
		case "enter":
			if m.selected < 0 || m.selected >= len(m.rows) {
				return m, nil
			}
			id := m.rows[m.selected].id
			m.abandonLoad()
			return m, func() tea.Msg { return SelectedMsg{ID: id} }
		case "r":
			m.loading = true
			m.loadErr = ""
			return m, m.load()
		}
	}
	return m, nil
}

func (m Model) View() string {
	w := max(1, m.width-4)
	title := lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	lines := []string{title.Render("Installed agents"), ""}

	switch {
	case m.loading:
		lines = append(lines, "Loading…")
	case m.loadErr != "":
		lines = append(lines, m.loadErr)
	case m.onlyFlagship:
		lines = append(lines, "Only the flagship is installed. Install another with: gaia hub install <id>")
	default:
		for i, r := range m.rows {
			marker := "  "
			if i == m.selected {
				marker = "› "
			}
			state := "stopped"
			if r.running {
				state = "running"
			}
			// "v" prefix matches the header's identity chip, so the same
			// agent reads the same way in both places.
			version := "-"
			if r.version != "" {
				version = "v" + r.version
			}
			label := fmt.Sprintf("%s%-10s %-16s %-8s %s", marker, r.id, r.name, version, state)
			if i == m.selected {
				label = title.Render(label)
			}
			lines = append(lines, label)
		}
	}

	hint := "↑/↓ choose · enter switch · r refresh · esc close"
	if w < 65 {
		hint = "↑/↓ · enter · r refresh · esc"
	}
	if w < 40 {
		hint = "↑/↓ · enter · esc"
	}

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
