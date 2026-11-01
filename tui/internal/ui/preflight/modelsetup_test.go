// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/setupapi/setupapitest"
	"github.com/amd/gaia/tui/internal/ui/modelsetup"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// modelChoosingRunner is a scripted runner whose model step lets the user choose,
// the way the local runner's does.
type modelChoosingRunner struct {
	scriptedRunner
	api setupapi.API
}

func (c modelChoosingRunner) ModelSetup() (setupapi.API, bool) { return c.api, false }

func modelStepReport() Report {
	rep := firstRunReport()
	rep.Rows[1] = Row{Key: KeyLemonade, Label: lemonadeRowLabel, State: StateOK, Line: "running"}
	rep.Rows[2] = Row{Key: KeyModel, Label: modelRowLabel, State: StateFailed, FirstRun: true,
		Disposition: status.DispositionHalt, Fix: FixRunSetup, Line: "not downloaded yet",
		Step: "choose and download the models (~6 GB)"}
	return rep
}

func plannedFake() *setupapitest.Fake {
	url := "http://127.0.0.1:13305/api/v1"
	free := 400.0
	return &setupapitest.Fake{
		P: setupapi.Plan{
			Lemonade:  setupapi.LemonadeInfo{Mode: "gaia", URL: &url, Reachable: true},
			ChatModel: setupapi.ChatModel{ID: "Gemma-4-E4B-it-GGUF"},
			Disk:      setupapi.Disk{FreeGB: &free},
			Catalog: []setupapi.CatalogEntry{
				setupapitest.Entry("Gemma-4-E4B-it-GGUF", []string{"chat", "vision"}, 5.97, false, true, true, setupapitest.Bool(true), 8, 32),
				setupapitest.Entry("user.embeddinggemma-300m-GGUF", []string{"embedding"}, 0.33, false, true, true, setupapitest.Bool(true), 1, 32),
			},
		},
		GPUErr: errNoFix,
	}
}

// run executes a command and feeds its messages back, a few levels deep.
func run(t *testing.T, m Model, cmd tea.Cmd) Model {
	t.Helper()
	for depth := 0; cmd != nil && depth < 6; depth++ {
		msg := cmd()
		var next []tea.Cmd
		msgs := []tea.Msg{msg}
		if b, ok := msg.(tea.BatchMsg); ok {
			msgs = nil
			for _, c := range b {
				if c != nil {
					if inner := c(); inner != nil {
						msgs = append(msgs, inner)
					}
				}
			}
		}
		for _, mm := range msgs {
			if mm == nil || !modelsetup.IsMsg(mm) {
				continue
			}
			updated, c := m.Update(mm)
			m = updated.(Model)
			next = append(next, c)
		}
		cmd = tea.Batch(next...)
	}
	return m
}

// The model step opens "choose your models" — it no longer runs `gaia init`
// straight away.
func TestModelStepOpensTheChoiceInsteadOfDownloading(t *testing.T) {
	fixes := &[]FixKind{}
	api := plannedFake()
	m := newWith(modelChoosingRunner{scriptedRunner{rep: modelStepReport(), fixes: fixes}, api},
		Config{AgentID: "gaia", AgentName: "GAIA"}, Options{})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	m = updated.(Model)
	updated, _ = m.Update(reportMsg{rep: modelStepReport()})
	m = updated.(Model)

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	if m.SetupScreen() == "" {
		t.Fatal("enter on the model step did not open the model choice")
	}
	if len(*fixes) != 0 {
		t.Fatalf("a fix ran instead: %v", *fixes)
	}
	m = run(t, m, cmd)
	if m.SetupScreen() != "models" {
		t.Fatalf("setup screen = %q, want models", m.SetupScreen())
	}
	if s := plain(m); !strings.Contains(s, "Choose your models") {
		t.Fatalf("the choice is not on screen:\n%s", s)
	}
	if api.Called("run") || api.Called("download") {
		t.Fatalf("something downloaded before Continue: %v", api.CallList())
	}

	// esc on the choice goes back to the checklist.
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(Model)
	if msg := cmd(); msg != nil {
		updated, _ = m.Update(msg)
		m = updated.(Model)
	}
	if m.SetupScreen() != "" {
		t.Fatal("esc did not return to the checklist")
	}
}

// Finishing re-checks and hands off without another enter.
func TestFinishingSetupRechecks(t *testing.T) {
	api := plannedFake()
	m := newWith(modelChoosingRunner{scriptedRunner{rep: modelStepReport(), fixes: &[]FixKind{}}, api},
		Config{AgentID: "gaia", AgentName: "GAIA"}, Options{})
	updated, _ := m.Update(reportMsg{rep: modelStepReport()})
	m = updated.(Model)
	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	updated, _ = m.Update(modelsetup.FinishedMsg{})
	m = updated.(Model)
	if m.SetupScreen() != "" || m.phase != phaseChecking {
		t.Fatalf("finish should close the choice and re-check: screen=%q phase=%v", m.SetupScreen(), m.phase)
	}
	if m.fixApplied {
		t.Error("the ready summary was the review; the re-check must not hold for enter")
	}
}

// A runner that cannot ask (the daemon runner) keeps its own download.
func TestRunnersThatCannotAskKeepTheirDownload(t *testing.T) {
	m, fixes := settledOn(t, modelStepReport(), 80, 24)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	if m.SetupScreen() != "" {
		t.Fatal("a runner without a model choice opened one")
	}
	drainUntilDone(t, m)
	scriptedFixesMu.Lock()
	defer scriptedFixesMu.Unlock()
	if len(*fixes) != 1 || (*fixes)[0] != FixRunSetup {
		t.Fatalf("fixes = %v, want the runner's own setup run", *fixes)
	}
}

// Under reduced motion the gate's running rows keep their "[..]" and words
// and lose the spinner frames.
func TestReducedMotionStopsTheGateSpinner(t *testing.T) {
	m, _ := settledOn(t, modelStepReport(), 80, 24)
	m.phase = phaseChecking
	m.rep.Rows[0].State = StateChecking
	if !spinnerFrame(plain(m)) {
		t.Fatal("full motion should draw the spinner")
	}
	m.motion = modelsetup.Motion{Reduced: true}
	if spinnerFrame(plain(m)) {
		t.Fatalf("reduced motion still drew a spinner frame:\n%s", plain(m))
	}
	if !strings.Contains(plain(m), "[..]") {
		t.Fatal("the running marker is gone")
	}
}

func spinnerFrame(s string) bool { return strings.ContainsAny(s, "⣾⣽⣻⢿⡿⣟⣯⣷") }
