// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/ui/modelsetup"
)

// The model step of first-run setup opens "choose your models" instead of
// downloading straight away: the plan, the user's choices, the GPU memory
// step, then the download with per-model progress. All of it goes through the
// daemon's setup routes, which are the Agent UI's own.

// modelChooser is a runner whose model step lets the user choose.
type modelChooser interface {
	// ModelSetup is the backend to choose through, and whether chat runs in
	// the cloud (so only the embedding model is offered).
	ModelSetup() (setupapi.API, bool)
}

// newSetupAPI is the backend the local runner chooses through. A var so tests
// script it instead of starting a daemon.
var newSetupAPI = func() setupapi.API { return setupapi.NewDaemon() }

// setupMotion is how much the setup screens animate. A var for tests.
var setupMotion = modelsetup.EnvMotion

func (l localRunner) ModelSetup() (setupapi.API, bool) {
	cloud := l.opts.ClaudeMode || lemonade.IsCloudID(l.opts.Model) || lemonade.IsCloudProvider(l.opts.ChatProvider)
	return newSetupAPI(), cloud
}

// opensModelSetup reports whether the focused row's fix is the model step a
// user should choose for: a first run, on a runner that can ask.
func (m Model) opensModelSetup() bool {
	row, ok := m.focusedRow()
	if !ok || row.Key != KeyModel || !row.FirstRun || row.Fix != FixRunSetup {
		return false
	}
	_, ok = m.r.(modelChooser)
	return ok
}

func (m Model) openModelSetup() (tea.Model, tea.Cmd) {
	api, cloud := m.r.(modelChooser).ModelSetup()
	// The model step carries on the checklist's numbering.
	before := 0
	if n := m.rep.StepNumber(m.focus); n > 1 {
		before = n - 1
	}
	s := modelsetup.New(api, modelsetup.Options{
		Mode:        modelsetup.ModeFirstRun,
		Cloud:       cloud,
		Motion:      setupMotion(),
		StepsBefore: before,
	})
	s, _ = s.Update(tea.WindowSizeMsg{Width: m.width, Height: m.height})
	m.setup = &s
	m.note = ""
	m.details = false
	return m, s.Init()
}

// SetupScreen names the setup screen on view, "" when the checklist is.
func (m Model) SetupScreen() string {
	if m.setup == nil {
		return ""
	}
	return m.setup.Screen()
}

// updateSetup routes a message while the setup screens are open.
func (m Model) updateSetup(msg tea.Msg) (tea.Model, tea.Cmd, bool) {
	switch msg := msg.(type) {
	case modelsetup.FinishedMsg:
		m.setup = nil
		// The ready summary was the review, so a passing re-check hands off
		// without holding for another enter.
		m.fixApplied = false
		m.note = "Setup finished. Checking that everything loads…"
		m.phase = phaseChecking
		m.rep.Rows = checkingRows(m.r, m.cfg)
		return m, tea.Batch(m.checkCmd(), m.spin.Tick), true
	case modelsetup.BackMsg:
		m.setup = nil
		return m, nil, true
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		s, cmd := m.setup.Update(msg)
		m.setup = &s
		return m, cmd, true
	case tea.KeyMsg:
		if msg.String() == "ctrl+c" {
			return m, nil, false
		}
		s, cmd := m.setup.Update(msg)
		m.setup = &s
		return m, cmd, true
	}
	if modelsetup.IsMsg(msg) {
		s, cmd := m.setup.Update(msg)
		m.setup = &s
		return m, cmd, true
	}
	return m, nil, false
}

// abandonSetup stops a first-run download whose screen is going away.
func (m Model) abandonSetup() {
	if m.setup == nil {
		return
	}
	if err := m.setup.Abandon(); err != nil {
		m.opts.Logf("preflight: could not stop the model download on exit: %v", err)
	}
}

// spinPrefix is the spinner and its space in a running row, or nothing under
// reduced motion: the row's "[..]" marker and its words already say it runs.
func (m Model) spinPrefix() string {
	if m.motion.Reduced {
		return ""
	}
	return m.spin.View() + " "
}
