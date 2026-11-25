// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package agentrecovery is the screen a launch shows when the saved agent
// program is gaiacpp and it cannot run: missing, or failing its check. It
// says why and waits for the user to pick: download gaiacpp again, use the
// Python agent, or quit. Nothing is switched without that pick.
package agentrecovery

import (
	"context"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/gaiaprogram"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Choice is what the user picked.
type Choice int

const (
	// Quit leaves GAIA; it is also what closing the screen means.
	Quit Choice = iota
	// Downloaded means gaiacpp was downloaded and verified: launch on it.
	Downloaded
	// UsePython means the user asked for the Python agent instead.
	UsePython
)

// ViewName is this screen in the control API's snapshot.
const ViewName = "agent-recovery"

type stage int

const (
	stageChecking stage = iota
	stageChoosing
	stageDownloading
)

type option struct {
	choice Choice
	label  string
	note   string
	// disabled is why the row cannot be picked, or "".
	disabled string
}

// Model is the recovery screen. Result is read after the program exits.
type Model struct {
	why    string
	detail string
	result *Choice

	stage    stage
	status   gaiaprogram.Status
	cursor   int
	showMore bool
	progress gaiaprogram.Progress
	failure  string
	cancel   context.CancelFunc
	events   chan tea.Msg
	width    int

	check    func(context.Context) (gaiaprogram.Status, error)
	download func(context.Context, func(gaiaprogram.Progress)) (string, error)
}

type statusMsg struct {
	status gaiaprogram.Status
	err    error
}
type progressMsg gaiaprogram.Progress
type downloadedMsg struct{ err error }

// New builds the screen. why is one plain sentence; detail is the full error,
// shown only when asked for. result receives the pick.
func New(why, detail string, result *Choice) Model {
	*result = Quit
	return Model{why: why, detail: detail, result: result, check: gaiaprogram.Check, download: gaiaprogram.Download}
}

// WithProgram replaces the calls to the gaia CLI; tests only.
func (m Model) WithProgram(check func(context.Context) (gaiaprogram.Status, error),
	download func(context.Context, func(gaiaprogram.Progress)) (string, error)) Model {
	m.check, m.download = check, download
	return m
}

func (m Model) Init() tea.Cmd {
	check := m.check
	return func() tea.Msg {
		st, err := check(context.Background())
		return statusMsg{status: st, err: err}
	}
}

// ControlSnapshot names the screen for the control API.
func (m Model) ControlSnapshot() control.Snapshot {
	snap := control.Snapshot{View: ViewName, Agent: catalog.FlagshipID}
	if m.stage == stageDownloading {
		snap.Overlay = "downloading"
	}
	return snap
}

func (m Model) options() []option {
	download := option{choice: Downloaded, label: "Download " + catalog.NativeAgentBinary + " again"}
	switch {
	case m.stage == stageChecking:
		download.disabled = "checking this GAIA release…"
	case !m.status.CanDownload:
		download.disabled = m.status.Reason
	default:
		download.note = "from this GAIA release, checked before it is installed"
		if m.status.SizeBytes > 0 {
			download.note = gaiaprogram.HumanSize(m.status.SizeBytes) + " " + download.note
		}
	}
	return []option{
		download,
		{choice: UsePython, label: "Use the Python agent",
			note: "saved as your agent program; /agent " + catalog.NativeAgentBinary + " switches back"},
		{choice: Quit, label: "Quit"},
	}
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		return m, nil

	case statusMsg:
		if m.stage != stageChecking {
			return m, nil
		}
		m.stage = stageChoosing
		m.status = msg.status
		if msg.err != nil {
			m.status = gaiaprogram.Status{Reason: msg.err.Error()}
		}
		if !m.status.CanDownload && m.cursor == 0 {
			m.cursor = 1
		}
		return m, nil

	case progressMsg:
		m.progress = gaiaprogram.Progress(msg)
		return m, m.wait()

	case downloadedMsg:
		m.stage = stageChoosing
		if msg.err != nil {
			m.failure = fmt.Sprintf("%s was not installed: %v", catalog.NativeAgentBinary, msg.err)
			return m, nil
		}
		*m.result = Downloaded
		return m, tea.Quit

	case tea.KeyMsg:
		return m.key(msg)
	}
	return m, nil
}

func (m Model) wait() tea.Cmd {
	events := m.events
	return func() tea.Msg { return <-events }
}

func (m Model) key(key tea.KeyMsg) (tea.Model, tea.Cmd) {
	if key.String() == "ctrl+c" {
		if m.cancel != nil {
			m.cancel()
		}
		return m, tea.Quit
	}
	if m.stage == stageDownloading {
		if key.String() == "esc" {
			m.cancel()
		}
		return m, nil
	}
	opts := m.options()
	switch key.String() {
	case "up", "k", "shift+tab":
		m.cursor = (m.cursor + len(opts) - 1) % len(opts)
	case "down", "j", "tab":
		m.cursor = (m.cursor + 1) % len(opts)
	case "?":
		m.showMore = !m.showMore
	case "q", "esc":
		return m, tea.Quit
	case "enter":
		picked := opts[m.cursor]
		if picked.disabled != "" {
			return m, nil
		}
		switch picked.choice {
		case Downloaded:
			return m.startDownload()
		default:
			*m.result = picked.choice
			return m, tea.Quit
		}
	}
	return m, nil
}

func (m Model) startDownload() (tea.Model, tea.Cmd) {
	ctx, cancel := context.WithCancel(context.Background())
	events := make(chan tea.Msg, 8)
	m.stage, m.cancel, m.events, m.failure = stageDownloading, cancel, events, ""
	m.progress = gaiaprogram.Progress{Total: m.status.SizeBytes}
	download := m.download
	go func() {
		_, err := download(ctx, func(p gaiaprogram.Progress) {
			select {
			case events <- progressMsg(p):
			default:
			}
		})
		cancel()
		events <- downloadedMsg{err: err}
	}()
	return m, m.wait()
}

var (
	titleStyle  = lipgloss.NewStyle().Foreground(theme.Warning).Bold(true)
	textStyle   = lipgloss.NewStyle().Foreground(theme.Text)
	dimStyle    = lipgloss.NewStyle().Foreground(theme.Dim)
	pickedStyle = lipgloss.NewStyle().Foreground(theme.Selected).Bold(true)
	failStyle   = lipgloss.NewStyle().Foreground(theme.Danger)
)

const barCells = 30

func (m Model) View() string {
	width := m.width
	if width <= 0 || width > 88 {
		width = 88
	}
	wrap := lipgloss.NewStyle().Width(width - 4)

	var b strings.Builder
	b.WriteString(titleStyle.Render("GAIA's agent didn't start") + "\n\n")
	b.WriteString(wrap.Inherit(textStyle).Render(m.why) + "\n")
	b.WriteString(wrap.Inherit(dimStyle).Render(
		"Your saved agent program is "+catalog.NativeAgentBinary+" (Beta). Nothing has been switched; pick what to do.") + "\n\n")

	if m.stage == stageDownloading {
		b.WriteString(textStyle.Render(m.progressLine()) + "\n\n")
		b.WriteString(dimStyle.Render("esc cancel"))
		return lipgloss.NewStyle().Padding(1, 2).Render(b.String())
	}

	for i, opt := range m.options() {
		marker, style := "  ", textStyle
		if i == m.cursor {
			marker, style = "› ", pickedStyle
		}
		if opt.disabled != "" {
			style = dimStyle
		}
		b.WriteString(style.Render(marker+opt.label) + "\n")
		note := opt.note
		if opt.disabled != "" {
			note = "Not available: " + opt.disabled
		}
		if note != "" {
			b.WriteString(lipgloss.NewStyle().Width(width-8).MarginLeft(4).Inherit(dimStyle).Render(note) + "\n")
		}
	}
	if m.failure != "" {
		b.WriteString("\n" + wrap.Inherit(failStyle).Render(m.failure) + "\n")
	}
	if m.showMore {
		b.WriteString("\n" + wrap.Inherit(dimStyle).Render(m.detail) + "\n")
	}
	more := "? details"
	if m.showMore {
		more = "? hide details"
	}
	b.WriteString("\n" + dimStyle.Render("↑/↓ move · enter choose · "+more+" · q quit"))
	return lipgloss.NewStyle().Padding(1, 2).Render(b.String())
}

func (m Model) progressLine() string {
	p := m.progress
	if p.Total <= 0 {
		if p.Done > 0 {
			return fmt.Sprintf("Downloading %s… %s", catalog.NativeAgentBinary, gaiaprogram.HumanSize(p.Done))
		}
		return fmt.Sprintf("Downloading %s…", catalog.NativeAgentBinary)
	}
	filled := int(p.Done * barCells / p.Total)
	if filled > barCells {
		filled = barCells
	}
	bar := strings.Repeat("█", filled) + strings.Repeat("░", barCells-filled)
	return fmt.Sprintf("Downloading %s  %s  %s of %s (%d%%)", catalog.NativeAgentBinary, bar,
		gaiaprogram.HumanSize(p.Done), gaiaprogram.HumanSize(p.Total), p.Done*100/p.Total)
}
