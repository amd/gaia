// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// unansweredReport is a fresh GAIA home on a PC where someone else's Lemonade
// already answers: the offer is open, and the model step waits behind it.
func unansweredReport(chatChosen bool) Report {
	offer := offerRow(lemonade.ProbeResult{
		URL: "http://localhost:13305/api/v1", Status: lemonade.ProbeOK, Version: "2026.44.1",
	})
	offer.Label = lemonadeRowLabel
	rep := Report{AgentID: "gaia", AgentName: "GAIA", Rows: []Row{
		{Key: KeyBinary, Label: "GAIA agent", State: StateOK, Line: `C:\gaia\gaia-agent.exe`},
		offer,
		{
			Key: KeyModel, Label: modelRowLabel, State: StateFailed, FirstRun: true,
			Disposition: status.DispositionHalt, Fix: FixRunSetup,
			Line: "not downloaded yet", Step: "download the models (~6 GB)",
		},
	}}
	if !chatChosen {
		rep = withChatStep(rep, chatOptions("6 GB", ""))
	}
	return rep
}

func pressKeys(t *testing.T, m Model, keys ...tea.KeyMsg) Model {
	t.Helper()
	for _, k := range keys {
		updated, _ := m.Update(k)
		m = updated.(Model)
	}
	return m
}

// The steps below an unanswered "which Lemonade?" run against a server nobody
// chose, so the cursor starts on the question and a later step is refused with
// the keys that answer it.
func TestAnUnansweredLemonadeQuestionComesBeforeTheSteps(t *testing.T) {
	down, enter := tea.KeyMsg{Type: tea.KeyDown}, tea.KeyMsg{Type: tea.KeyEnter}
	const want = "Answer the Lemonade row first: move up to it, then press f to use this Lemonade, " +
		"or g to use GAIA's own. The steps below depend on that choice."

	for _, tc := range []struct {
		name       string
		chatChosen bool
		step       string
		downs      int
	}{
		{name: "step 1, where chat runs", step: KeyChat, downs: 1},
		{name: "step 2, before chat is chosen", step: KeyModel, downs: 2},
		{name: "step 2, after choosing Local", chatChosen: true, step: KeyModel, downs: 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m, fixes := settledOn(t, unansweredReport(tc.chatChosen), 120, 40)
			if m.FocusKey() != KeyLemonade || m.Choosing() {
				t.Fatalf("focus = %q (choosing=%v), want the unanswered Lemonade row\n%s",
					m.FocusKey(), m.Choosing(), plain(m))
			}
			for range tc.downs {
				m = pressKeys(t, m, down)
			}
			if m.FocusKey() != tc.step {
				t.Fatalf("focus = %q, want %q", m.FocusKey(), tc.step)
			}
			for _, key := range []tea.KeyMsg{enter, {Type: tea.KeyRunes, Runes: []rune("f")}} {
				got := pressKeys(t, m, key)
				if got.Busy() || got.Choosing() || got.SetupScreen() != "" || len(*fixes) != 0 {
					t.Fatalf("%q started the step with the question open (phase %v, fixes %v)",
						key.String(), got.phase, *fixes)
				}
				if screen := oneLine(plain(got)); !strings.Contains(screen, want) {
					t.Errorf("%q: the screen does not say what to answer:\n%s", key.String(), plain(got))
				}
			}
		})
	}
}

// The question itself still takes its answers, and is never answered for the user.
func TestTheLemonadeQuestionStillTakesItsAnswer(t *testing.T) {
	for key, want := range map[string]FixKind{"f": FixUseRunningLemonade, "g": FixUseGAIALemonade} {
		m, fixes := settledOn(t, unansweredReport(false), 120, 40)
		if len(*fixes) != 0 {
			t.Fatalf("the question was answered for the user: %v", *fixes)
		}
		updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(key)})
		if !updated.(Model).Busy() || cmd == nil {
			t.Fatalf("%s did not answer the question", key)
		}
		runBatch(cmd)
		if len(*fixes) != 1 || (*fixes)[0] != want {
			t.Errorf("%s: fixes = %v, want %v", key, *fixes, want)
		}
	}
}

// Enter on the question, with steps still to do, names its keys.
func TestEnterOnTheOpenQuestionNamesItsKeys(t *testing.T) {
	m, _ := settledOn(t, unansweredReport(false), 120, 40)
	m = pressKeys(t, m, tea.KeyMsg{Type: tea.KeyEnter})
	const want = "Answer the Lemonade row first: press f to use this Lemonade, or g to use GAIA's own."
	if screen := oneLine(plain(m)); m.Busy() || !strings.Contains(screen, want) {
		t.Errorf("screen lacks %q:\n%s", want, plain(m))
	}
}

// runBatch runs a command and the commands a batch holds, skipping the spinner.
func runBatch(cmd tea.Cmd) {
	if cmd == nil {
		return
	}
	if batch, ok := cmd().(tea.BatchMsg); ok {
		for _, c := range batch[:1] {
			c()
		}
	}
}
