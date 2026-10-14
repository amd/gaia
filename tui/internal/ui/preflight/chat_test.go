// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// --- the runner: which steps a first run has, in which order ----------------

// agentFixture is a gaia-agent the binary row finds.
func agentFixture(t *testing.T) string {
	t.Helper()
	name := "fixture-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// newMachine is a PC with the agent and nothing else: no Lemonade at all.
func newMachine(t *testing.T) string {
	t.Helper()
	downLemonadeOn(t, fakeHostFor("linux", nil, nil, map[string]string{}))
	restore := lemonadeNotInstalled
	t.Cleanup(func() { lemonadeNotInstalled = restore })
	lemonadeNotInstalled = func() bool { return true }
	stubGaiaInit(t, func() (string, error) {
		t.Error("the model row was probed while Lemonade is not installed")
		return "", errors.New("must not be called")
	})
	return agentFixture(t)
}

// lemonadeUp makes the model server answer, and this PC report capacity.
func lemonadeUp(t *testing.T, capacity lemonade.Capacity, capErr error) {
	t.Helper()
	restoreProbe, restoreCap := probeLemonade, readCapacity
	t.Cleanup(func() { probeLemonade, readCapacity = restoreProbe, restoreCap })
	probeLemonade = func(context.Context) (string, bool, string) {
		return "http://localhost:13305/api/v1", true, "stub: answering"
	}
	readCapacity = func(context.Context) (lemonade.Capacity, error) { return capacity, capErr }
}

func keys(rep Report) []string {
	var out []string
	for _, r := range rep.Rows {
		out = append(out, r.Key)
	}
	return out
}

func stepLines(rep Report) []string {
	var out []string
	for i, r := range rep.Rows {
		if n := rep.StepNumber(i); n > 0 {
			out = append(out, stepText(n, rep.StepCount(), r.Step))
		}
	}
	return out
}

func TestANewMachineAsksWhereChatRunsBeforeAnything(t *testing.T) {
	bin := newMachine(t)
	rep := localRunner{opts: LocalOptions{Binary: bin, AskWhereChatRuns: true}}.
		Check(context.Background(), localCfg())

	want := []string{
		"Step 1 of 3 — choose where chat runs",
		"Step 2 of 3 — install the local model server (~1 min)",
		"Step 3 of 3 — download what your choice needs",
	}
	if got := stepLines(rep); strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("steps =\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	if idx := rep.FirstAttention(); idx < 0 || rep.Rows[idx].Key != KeyChat {
		t.Errorf("the cursor does not start on the choice: rows %v, focus %d", keys(rep), idx)
	}
	var providers []string
	for _, o := range rep.ChatOptions {
		providers = append(providers, o.Provider)
	}
	if strings.Join(providers, ",") != strings.Join(lemonade.ChatProviders, ",") {
		t.Errorf("options = %v, want every chat provider in order %v", providers, lemonade.ChatProviders)
	}
	local := rep.ChatOptions[0]
	if !strings.Contains(local.Needs, "~6 GB") || !strings.Contains(local.Needs, "fits this PC's memory") {
		t.Errorf("local option does not size its download or say how it fits: %q", local.Needs)
	}
	for _, o := range rep.ChatOptions[1:] {
		if !strings.Contains(o.TradeOff, "sent to") || !strings.Contains(o.Needs, "key") {
			t.Errorf("%s does not say chat leaves this PC and needs a key: %+v", o.Label, o)
		}
	}
}

// Anything that already says where chat runs skips the question, and a run with
// nobody watching is never asked it.
func TestAKnownChoiceSkipsTheQuestion(t *testing.T) {
	for name, opts := range map[string]LocalOptions{
		"headless":         {},
		"chose local":      {AskWhereChatRuns: true, ChatProvider: "local"},
		"saved cloud":      {AskWhereChatRuns: true, Model: "fireworks.deepseek-v4p1-flash"},
		"--use-claude":     {AskWhereChatRuns: true, ClaudeMode: true},
		"saved other kind": {AskWhereChatRuns: true, ChatProvider: "claude"},
	} {
		t.Run(name, func(t *testing.T) {
			opts.Binary = newMachine(t)
			// Claude mode walks past a down Lemonade to the embedder check.
			stubGaiaInit(t, func() (string, error) { return jsonStub(t, 1, setupJSON), nil })
			rep := localRunner{opts: opts}.Check(context.Background(), localCfg())
			if _, ok := rep.Find(KeyChat); ok || len(rep.ChatOptions) > 0 {
				t.Errorf("asked where chat runs although it is known: %v", keys(rep))
			}
		})
	}
}

func TestChoosingLocalKeepsTheFullDownload(t *testing.T) {
	bin := newMachine(t)
	r := localRunner{opts: LocalOptions{Binary: bin, AskWhereChatRuns: true}}.WithChatProvider("local")
	rep := r.Check(context.Background(), localCfg())
	want := "Step 1 of 2 — install the local model server (~1 min)\nStep 2 of 2 — download the models (~6 GB)"
	if got := strings.Join(stepLines(rep), "\n"); got != want {
		t.Errorf("steps =\n%s\nwant\n%s", got, want)
	}
}

// Cloud chat still needs Lemonade — it routes the requests and computes the
// embeddings — so it is installed first; the chat model is never downloaded.
func TestChoosingCloudInstallsLemonadeConnectsThenSkipsTheChatModel(t *testing.T) {
	for _, provider := range []string{"fireworks", "amd"} {
		t.Run(provider, func(t *testing.T) {
			bin := newMachine(t)
			r := localRunner{opts: LocalOptions{Binary: bin, AskWhereChatRuns: true, ChatProvider: provider}}
			rep := r.Check(context.Background(), localCfg())
			label := lemonade.Label(provider)
			want := "Step 1 of 2 — install the local model server (~1 min)\n" +
				"Step 2 of 2 — connect " + label + ", then a <1 GB download"
			if got := strings.Join(stepLines(rep), "\n"); got != want {
				t.Fatalf("steps =\n%s\nwant\n%s", got, want)
			}
			if row, _ := rep.Find(KeyLemonade); row.Fix != FixInstallServer {
				t.Errorf("Lemonade step fix = %v, want the server-only install", row.Fix)
			}
			if row, _ := rep.Find(KeyModel); row.Label != label {
				t.Errorf("model row label = %q, want %q", row.Label, label)
			}
			if !r.skipChatModel() {
				t.Error("a cloud choice would still download the local chat model")
			}

			// Step 1 done: the connect step hands off to the provider panel without
			// asking setup anything, since no model is chosen to check yet.
			lemonadeUp(t, lemonade.Capacity{}, nil)
			stubGaiaInit(t, func() (string, error) {
				t.Error("setup was asked about models before a cloud model was chosen")
				return "", errors.New("must not be called")
			})
			rep = r.Check(context.Background(), localCfg())
			row, _ := rep.Find(KeyModel)
			if !row.FirstRun || row.Fix != FixConnectProvider || row.Provider != provider {
				t.Fatalf("connect step = %+v", row)
			}

			// Connected, a model picked: what is left is the embedder alone.
			stubGaiaInit(t, func() (string, error) { return jsonStub(t, 1, setupJSON), nil })
			picked := localRunner{opts: LocalOptions{Binary: bin, AskWhereChatRuns: true,
				ChatProvider: provider, Model: provider + ".some-model"}}
			row = modelRow(picked)
			if row.Fix != FixRunSetup || !strings.Contains(row.Remedy.Command, "--skip-chat-model") {
				t.Errorf("after connecting, setup would fetch the chat model: %+v", row)
			}
		})
	}
}

func TestTheLocalOptionSaysWhetherItFitsThisPC(t *testing.T) {
	for name, tc := range map[string]struct {
		capacity lemonade.Capacity
		capErr   error
		want     string
	}{
		"fits":    {lemonade.Capacity{MemoryGB: 16, MemorySource: "System RAM", DiskFreeGB: -1}, nil, "Gemma-4-E4B-it-GGUF fits: this PC has 16 GB for models (System RAM)."},
		"too big": {lemonade.Capacity{MemoryGB: 2, MemorySource: "System RAM", DiskFreeGB: -1}, nil, "Gemma-4-E4B-it-GGUF will not fit: needs ~4 GB of memory; this PC has 2 GB (System RAM)."},
		"unknown": {lemonade.Capacity{}, errors.New("no system-info"), "Setup picks the model that fits this PC's memory."},
	} {
		t.Run(name, func(t *testing.T) {
			bin := agentFixture(t)
			lemonadeUp(t, tc.capacity, tc.capErr)
			stubGaiaInit(t, func() (string, error) { return jsonStub(t, 1, setupJSON), nil })
			rep := localRunner{opts: LocalOptions{Binary: bin, AskWhereChatRuns: true}}.
				Check(context.Background(), localCfg())
			if got := stepLines(rep); len(got) != 2 || !strings.HasPrefix(got[0], "Step 1 of 2 — choose") {
				t.Fatalf("steps = %v", got)
			}
			local := rep.ChatOptions[0]
			if want := "Downloads 3.5 GB of models. " + tc.want; local.Needs != want {
				t.Errorf("local needs = %q\nwant          %q", local.Needs, want)
			}
		})
	}
}

// --- the screen -------------------------------------------------------------

// choosingRunner is a scripted runner that can be told where chat runs, and
// answers the re-check with the report for that choice.
type choosingRunner struct {
	scriptedRunner
	after  map[string]Report
	chosen *string
}

func (c choosingRunner) WithChatProvider(p string) Runner {
	*c.chosen = p
	return scriptedRunner{rep: c.after[p], fixes: c.fixes}
}

func chooseFirstRunReport() Report {
	rep := firstRunReport()
	rep.Rows[2].Step = "download what your choice needs"
	return withChatStep(rep, chatOptions("", "Setup picks the model that fits this PC's memory."))
}

func afterChoosing(provider string) Report {
	rep := firstRunReport()
	switch provider {
	case "local":
	default:
		rep.Rows[2].Label = lemonade.Label(provider)
		rep.Rows[2].Step = connectStep(provider)
	}
	return rep
}

func choosingGate(t *testing.T, w, h int) (Model, *string, *[]FixKind) {
	t.Helper()
	fixes, chosen := &[]FixKind{}, new(string)
	after := map[string]Report{}
	for _, p := range lemonade.ChatProviders {
		after[p] = afterChoosing(p)
	}
	rep := chooseFirstRunReport()
	r := choosingRunner{scriptedRunner{rep: rep, fixes: fixes}, after, chosen}
	m := newWith(r, Config{AgentID: "gaia", AgentName: "GAIA"}, Options{})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: w, Height: h})
	updated, _ = updated.(Model).Update(reportMsg{rep: rep})
	return updated.(Model), chosen, fixes
}

func press(m Model, keys ...string) (Model, tea.Cmd) {
	var cmd tea.Cmd
	for _, k := range keys {
		var msg tea.KeyMsg
		switch k {
		case "enter":
			msg = tea.KeyMsg{Type: tea.KeyEnter}
		case "esc":
			msg = tea.KeyMsg{Type: tea.KeyEsc}
		case "down":
			msg = tea.KeyMsg{Type: tea.KeyDown}
		case "up":
			msg = tea.KeyMsg{Type: tea.KeyUp}
		default:
			msg = tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
		}
		var updated tea.Model
		updated, cmd = m.Update(msg)
		m = updated.(Model)
	}
	return m, cmd
}

// collect runs cmd and every command it batches, returning their messages.
// Spinner ticks are skipped: they only sleep.
func collect(cmd tea.Cmd) []tea.Msg {
	if cmd == nil {
		return nil
	}
	msg := cmd()
	if batch, ok := msg.(tea.BatchMsg); ok {
		var out []tea.Msg
		for _, c := range batch {
			if c == nil {
				continue
			}
			out = append(out, collect(c)...)
		}
		return out
	}
	return []tea.Msg{msg}
}

const chooserGolden = `  Setting up GAIA                                                                      step 1 of 3
  ────────────────────────────────────────────────────────────────────────────────────────────────
  Step 1 of 3 — where should chat run?
  Change it any time: p on this screen, /provider in chat.

  > 1. Local
       Private and free: nothing leaves this PC. Speed depends on this PC.
       Downloads ~6 GB of models. Setup picks the model that fits this PC's memory.

    2. Fireworks AI
       Fast on any PC. Conversations are sent to Fireworks AI, and usage is billed to your key.
       Needs a Fireworks AI API key. Downloads under 1 GB.

    3. AMD LLM Gateway
       Fast on any PC. Conversations are sent to your organisation's AMD LLM Gateway.
       Needs your gateway key (and its URL). Downloads under 1 GB.

  ────────────────────────────────────────────────────────────────────────────────────────────────
  ↑/↓ move · p all providers · enter choose · esc back to the steps`

func trimRight(s string) string {
	lines := strings.Split(s, "\n")
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " ")
	}
	return strings.Join(lines, "\n")
}

// The first frame of a first run IS the question, before any download.
func TestTheFirstRunScreenAsksWhereChatRuns(t *testing.T) {
	m, _, _ := choosingGate(t, 100, 24)
	screen := trimRight(plain(m))
	t.Logf("\n%s", screen)
	if !m.Choosing() {
		t.Fatal("a first run did not open on the choice")
	}
	if screen != chooserGolden {
		t.Errorf("chooser screen drifted from the golden:\n%s\n--- want ---\n%s", screen, chooserGolden)
	}
	if strings.Contains(screen, "[!]") {
		t.Error("the choice renders as a failure")
	}
}

func TestTheChoiceFitsSmallTerminals(t *testing.T) {
	for _, size := range [][2]int{{80, 24}, {60, 20}, {44, 14}} {
		m, _, _ := choosingGate(t, size[0], size[1])
		m, _ = press(m, "down")
		screen := plain(m)
		t.Logf("%dx%d\n%s", size[0], size[1], screen)
		assertFits(t, splitLines(screen), size[0], size[1])
		for _, want := range []string{"1. Local", "> 2. Fireworks AI", "3. AMD LLM Gateway", "Downloads under 1 GB"} {
			if !strings.Contains(oneLine(screen), want) {
				t.Errorf("%dx%d: %q is not on screen:\n%s", size[0], size[1], want, screen)
			}
		}
		if !strings.Contains(screen, "esc") {
			t.Errorf("%dx%d: the way out is gone:\n%s", size[0], size[1], screen)
		}
	}
}

func TestPickingLocalGoesOnToTheFullDownload(t *testing.T) {
	m, chosen, _ := choosingGate(t, 100, 24)
	m, cmd := press(m, "enter")
	if *chosen != "local" || m.Choosing() || m.phase != phaseChecking {
		t.Fatalf("enter did not choose Local and re-check: chosen %q, choosing %v", *chosen, m.Choosing())
	}
	var told *ChatChosenMsg
	var after Report
	for _, msg := range collect(cmd) {
		switch v := msg.(type) {
		case ChatChosenMsg:
			told = &v
		case reportMsg:
			after = v.rep
		}
	}
	if told == nil || told.Provider != "local" || told.AgentID != "gaia" {
		t.Fatalf("the host was not told the choice: %+v", told)
	}
	updated, _ := m.Update(reportMsg{rep: after})
	screen := plain(updated.(Model))
	t.Logf("\n%s", screen)
	for _, want := range []string{
		"Step 2 of 3 — install the local model server (~1 min)",
		"Step 3 of 3 — download the models (~6 GB)",
		"enter start step 2",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("after choosing Local the screen is missing %q", want)
		}
	}
}

func TestPickingACloudProviderSkipsTheChatModelAndConnectsAfterLemonade(t *testing.T) {
	m, chosen, fixes := choosingGate(t, 100, 24)
	m, cmd := press(m, "down", "enter")
	if *chosen != "fireworks" {
		t.Fatalf("chosen = %q, want fireworks", *chosen)
	}
	var after Report
	for _, msg := range collect(cmd) {
		if v, ok := msg.(reportMsg); ok {
			after = v.rep
		}
	}
	updated, _ := m.Update(reportMsg{rep: after})
	m = updated.(Model)
	screen := plain(m)
	t.Logf("\n%s", screen)
	if !strings.Contains(screen, "Step 3 of 3 — connect Fireworks AI, then a <1 GB download") ||
		strings.Contains(screen, "~6 GB") {
		t.Errorf("a cloud choice still reads as the local download:\n%s", screen)
	}
	// Lemonade comes first: enter runs the server-only install.
	m, _ = press(m, "enter")
	drainUntilDone(t, m)
	if len(*fixes) != 1 || (*fixes)[0] != FixInstallServer {
		t.Errorf("fixes = %v, want the Lemonade install first", *fixes)
	}

	// Lemonade installed: enter on the connect step hands off to the provider panel.
	connect := after
	connect.Rows[1] = Row{Key: KeyLemonade, Label: lemonadeRowLabel, State: StateOK, Line: "running"}
	connect.Rows[2] = connectProviderRow("fireworks")
	connect.Rows[2].Label = "Fireworks AI"
	m.phase = phaseIdle
	updated, _ = m.Update(reportMsg{rep: connect})
	m, cmd = press(updated.(Model), "enter")
	msgs := collect(cmd)
	if len(msgs) != 1 {
		t.Fatalf("enter on the connect step sent %v", msgs)
	}
	if got, ok := msgs[0].(ConnectProviderMsg); !ok || got.Provider != "fireworks" {
		t.Errorf("enter on the connect step sent %#v, want ConnectProviderMsg for fireworks", msgs[0])
	}
}

func TestNumbersAndArrowsMoveTheChoiceAndWrap(t *testing.T) {
	m, _, _ := choosingGate(t, 100, 24)
	m, _ = press(m, "3")
	if m.choice != 2 {
		t.Errorf("3 moved to option %d", m.choice+1)
	}
	m, _ = press(m, "down")
	if m.choice != 0 {
		t.Errorf("down from the last option went to %d, want the first", m.choice+1)
	}
	m, _ = press(m, "up")
	if m.choice != 2 {
		t.Errorf("up from the first option went to %d, want the last", m.choice+1)
	}
	m, _ = press(m, "9")
	if m.choice != 2 {
		t.Errorf("a number with no option moved the choice to %d", m.choice+1)
	}
}

// esc steps back to the checklist, where the choice is still step 1 and enter
// reopens it; a second esc leaves, as it does on every gate.
func TestEscLeavesTheChoiceThenTheScreen(t *testing.T) {
	m, chosen, _ := choosingGate(t, 100, 24)
	m, _ = press(m, "esc")
	if m.Choosing() {
		t.Fatal("esc did not close the choice")
	}
	screen := plain(m)
	if !strings.Contains(screen, "Step 1 of 3 — choose where chat runs") || !strings.Contains(screen, "enter start step 1") {
		t.Errorf("the checklist does not name the choice as step 1:\n%s", screen)
	}
	m, _ = press(m, "enter")
	if !m.Choosing() || *chosen != "" {
		t.Fatalf("enter on step 1 did not reopen the choice (chosen %q)", *chosen)
	}
	m, _ = press(m, "esc")
	_, cmd := press(m, "esc")
	if msgs := collect(cmd); len(msgs) != 1 {
		t.Fatalf("second esc sent %v", msgs)
	} else if _, ok := msgs[0].(CancelMsg); !ok {
		t.Errorf("second esc sent %#v, want CancelMsg", msgs[0])
	}
}

// A runner with no notion of a chat choice says so instead of pretending.
func TestARunnerThatCannotRecordTheChoiceSaysSo(t *testing.T) {
	m, _ := settledOn(t, chooseFirstRunReport(), 100, 24)
	m, cmd := press(m, "enter")
	if cmd != nil || m.Choosing() || !strings.Contains(m.note, "cannot record where chat runs") {
		t.Errorf("note = %q, cmd = %v", m.note, cmd)
	}
}

// A gate the host rebuilt mid-setup — after the provider panel — keeps counting.
func TestARebuiltGateKeepsTheStepCount(t *testing.T) {
	rep := Report{AgentID: "gaia", AgentName: "GAIA", Rows: []Row{
		{Key: KeyBinary, Label: "GAIA agent", State: StateOK, Line: "gaia-agent"},
		{Key: KeyLemonade, Label: lemonadeRowLabel, State: StateOK, Line: "running"},
		{Key: KeyModel, Label: "Embeddings", State: StateFailed, FirstRun: true,
			Disposition: status.DispositionHalt, Fix: FixRunSetup, Line: "embedding models not downloaded",
			Step: embeddingStep},
	}}
	m := newWith(scriptedRunner{rep: rep, fixes: &[]FixKind{}}, Config{AgentID: "gaia", AgentName: "GAIA"},
		Options{StepsDone: 2})
	updated, _ := m.Update(reportMsg{rep: rep})
	if got := lineWith(plain(updated.(Model)), "Embeddings"); !strings.Contains(got, "Step 3 of 3") {
		t.Errorf("rebuilt gate restarted the count: %q", got)
	}
}
