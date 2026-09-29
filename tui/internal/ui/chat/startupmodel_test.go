// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/providers"
)

const flashID = "fireworks.deepseek-v4p1-flash"

type savedCall struct{ provider, model string }

// startupChat is a flagship chat armed with an opening /model turn, plus a
// record of every save it asks for.
func startupChat(t *testing.T, kind StartupModelKind, provider, model string) (ChatModel, *queryCapturingClient, *[]savedCall) {
	t.Helper()
	c := &queryCapturingClient{}
	var saves []savedCall
	m := NewChatModelForFlagship(c, "gaia", "GAIA", "", false, true).
		WithStartupModel(kind, provider, model).
		WithModelMemory(func(p, id string) error {
			saves = append(saves, savedCall{p, id})
			return nil
		})
	m.width, m.height = 120, 30
	return m, c, &saves
}

// openChat runs what Init schedules for the opening turn and returns the model
// with that turn in flight.
func openChat(t *testing.T, m ChatModel, c *queryCapturingClient) ChatModel {
	t.Helper()
	if !m.startup.pending {
		t.Fatal("no opening /model turn was armed")
	}
	updated, cmd := m.Update(startupModelMsg{})
	m = updated.(ChatModel)
	runSends(cmd)
	if !m.streaming {
		t.Fatalf("the opening /model turn did not start; sent=%v messages=%+v", c.sent, m.messages)
	}
	return m
}

// runSends executes a startTurn batch so the client records what was sent.
func runSends(cmd tea.Cmd) {
	if cmd == nil {
		return
	}
	switch msg := cmd().(type) {
	case tea.BatchMsg:
		for _, sub := range msg {
			runSends(sub)
		}
	}
}

// deliver runs events through Update, as the program does — the opening turn's
// failure is reported there, after the handler that ended it.
func deliver(t *testing.T, m ChatModel, events ...interface{}) ChatModel {
	t.Helper()
	for _, e := range events {
		updated, _ := m.Update(eventMsg{ch: m.events, event: e})
		m = updated.(ChatModel)
	}
	return m
}

func ping(id, display, backend string, remote bool) event.CanonicalStatusEvent {
	return event.CanonicalStatusEvent{Type: "status", ModelID: id, ModelDisplay: display, ModelBackend: backend, ModelRemote: remote}
}

func errorsIn(m ChatModel) []string {
	var out []string
	for _, msg := range m.messages {
		if msg.Role == RoleError {
			out = append(out, msg.Content)
		}
	}
	return out
}

func TestInitArmsTheOpeningModelTurn(t *testing.T) {
	armed, _, _ := startupChat(t, StartupRestore, "fireworks", flashID)
	plain := NewChatModelForFlagship(&queryCapturingClient{}, "gaia", "GAIA", "", false, true)
	// Counted rather than run: Init's other commands probe the machine.
	count := func(m ChatModel) int {
		batch, _ := m.Init()().(tea.BatchMsg)
		return len(batch)
	}
	if count(armed) != count(plain)+1 {
		t.Fatal("Init did not schedule the opening /model turn")
	}

	// Behind the first-boot gate it waits, and the gate's release sends it.
	armed.setupChecking = true
	if count(armed) != count(plain)+1 { // +1 is the setup check itself
		t.Fatal("the opening turn must wait for the first-boot gate")
	}
	cmd := armed.releaseAfterSetupGate()
	if cmd == nil {
		t.Fatal("releasing the first-boot gate did not send the opening turn")
	}
	if _, ok := cmd().(startupModelMsg); !ok {
		t.Fatal("releasing the first-boot gate sent something else")
	}
}

func TestRestoredModelIsSwitchedToAndNamedInTheHeader(t *testing.T) {
	m, c, saves := startupChat(t, StartupRestore, "fireworks", flashID)
	m = openChat(t, m, c)
	if len(c.sent) != 1 || c.sent[0] != "/model "+flashID {
		t.Fatalf("restore must ask the agent to switch to the saved id, sent %q", c.sent)
	}
	m = deliver(t, m,
		ping(flashID, flashID, "fireworks", true), // startup ping
		ping(flashID, flashID, "fireworks", true), // switch confirmed
		event.CanonicalFinalEvent{Type: "final", Answer: "Switched to **" + flashID + "**."},
	)
	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, "Fireworks AI · deepseek-v4p1-flash") {
		t.Fatalf("header does not name the restored model: %q", header)
	}
	last := m.messages[len(m.messages)-1]
	if !strings.HasPrefix(last.Content, "Restored your last model.") {
		t.Fatalf("the restore was not stated: %q", last.Content)
	}
	if len(*saves) != 1 || (*saves)[0] != (savedCall{"fireworks", flashID}) {
		t.Fatalf("a confirmed restore must be saved as provider+model, got %+v", *saves)
	}
	if m.providerPanel != nil {
		t.Fatal("a successful restore must not open the picker")
	}
}

func TestFailedRestoreSaysSoInOneLineAndOpensThePicker(t *testing.T) {
	m, c, saves := startupChat(t, StartupRestore, "fireworks", flashID)
	m = openChat(t, m, c)
	m = deliver(t, m,
		ping(flashID, flashID, "fireworks", true),
		event.CanonicalErrorEvent{Type: "error", Detail: "Unknown Lemonade model '" + flashID +
			"'. Downloaded local or discovered cloud Lemonade models: Gemma-4-E4B-it-GGUF."},
	)
	errs := errorsIn(m)
	if len(errs) != 1 {
		t.Fatalf("want exactly one line, got %q", errs)
	}
	want := "Couldn't restore your last model, Fireworks AI · deepseek-v4p1-flash: Fireworks AI does not list it"
	if !strings.HasPrefix(errs[0], want) || strings.Contains(errs[0], "\n") {
		t.Fatalf("failure line = %q, want prefix %q on one line", errs[0], want)
	}
	if strings.Contains(errs[0], "Gemma") {
		t.Fatalf("the line must not offer another model in the user's place: %q", errs[0])
	}
	if m.providerPanel == nil {
		t.Fatal("a failed restore must hand the choice back via the provider picker")
	}
	if len(*saves) != 0 {
		t.Fatalf("a refused model must not be saved, got %+v", *saves)
	}
	// The next pick from the panel is an ordinary switch, saved once confirmed.
	updated, _ := m.Update(providers.SelectedMsg{ID: "Gemma-4-E4B-it-GGUF"})
	m = updated.(ChatModel)
	m = deliver(t, m, ping("Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF", "lemonade", false),
		event.CanonicalFinalEvent{Type: "final", Answer: "Switched."})
	if len(*saves) != 1 || (*saves)[0] != (savedCall{"local", "Gemma-4-E4B-it-GGUF"}) {
		t.Fatalf("the user's own pick must be saved, got %+v", *saves)
	}
}

func TestAnAgentThatDiedBeforeSwitchingShowsItsErrorAndHandsBackTheChoice(t *testing.T) {
	// Worst case: a saved Claude model. The agent launches on the local
	// default, so leaving it there without a word would run the wrong model.
	m, c, saves := startupChat(t, StartupRestore, "claude", "claude-sonnet-5")
	m = openChat(t, m, c)
	m = deliver(t, m, event.CanonicalErrorEvent{Type: "error", Detail: "ImportError: no module named gaia_agent"})
	errs := errorsIn(m)
	if len(errs) != 2 || !strings.Contains(errs[0], "ImportError") {
		t.Fatalf("want the crash verbatim, then the restore line; got %q", errs)
	}
	if !strings.HasPrefix(errs[1], "Couldn't restore your last model, Claude Sonnet 5: the agent stopped before it could switch") {
		t.Fatalf("restore line = %q", errs[1])
	}
	if m.providerPanel == nil || len(*saves) != 0 {
		t.Fatal("a restore that never ran must hand the choice back and save nothing")
	}
}

func TestATransportFailureDuringTheRestoreHandsBackTheChoice(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "fireworks", flashID)
	m = openChat(t, m, c)
	updated, _ := m.Update(errMsg{err: errors.New("failed to start agent"), turnSeq: m.turnSeq})
	m = updated.(ChatModel)
	errs := errorsIn(m)
	if len(errs) != 2 || !strings.HasPrefix(errs[1], "Couldn't restore your last model") || m.providerPanel == nil {
		t.Fatalf("got %q panel=%v", errs, m.providerPanel != nil)
	}
}

func TestStoppingTheRestoreDoesNotLeakIntoTheNextTurn(t *testing.T) {
	m, c, saves := startupChat(t, StartupRestore, "fireworks", flashID)
	m = openChat(t, m, c)
	// Esc twice: cancel, then give up waiting — the path that never settles.
	for i := 0; i < 2; i++ {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
		m = updated.(ChatModel)
	}
	errs := errorsIn(m)
	if len(errs) != 1 || !strings.Contains(errs[0], "you stopped it") || m.providerPanel == nil {
		t.Fatalf("a stopped restore must say so and hand back the choice, got %q", errs)
	}
	if m.startup.inFlight || m.awaitingModelSwitch || m.switchTarget != "" {
		t.Fatal("the opening turn's state outlived it")
	}

	// The next question is an ordinary turn: shown, costed, errors verbatim.
	m.providerPanel = nil
	updated, _ := m.submit("hello")
	m = updated.(ChatModel)
	m = deliver(t, m, ping(flashID, flashID, "fireworks", true),
		event.CanonicalFinalEvent{Type: "final", Answer: "hi there"})
	last := m.messages[len(m.messages)-1]
	if last.Content != "hi there" || len(m.cost.turns) != 1 || len(*saves) != 0 {
		t.Fatalf("the next turn was treated as the restore: %q cost=%d saves=%v", last.Content, len(m.cost.turns), *saves)
	}
	updated, _ = m.submit("again")
	m = updated.(ChatModel)
	m = deliver(t, m, event.CanonicalErrorEvent{Type: "error", Detail: "model exploded"})
	if errs := errorsIn(m); errs[len(errs)-1] != "model exploded" {
		t.Fatalf("a later error must show as itself, got %q", errs)
	}
}

func TestHeaderNamesTheGateModelBeforeTheFirstPing(t *testing.T) {
	m := NewChatModelForFlagship(&queryCapturingClient{}, "gaia", "GAIA", "", false, true).
		WithExpectedModel("Gemma-4-E4B-it-GGUF")
	m.width, m.height = 120, 30
	if !strings.Contains(ansi.Strip(m.renderHeader()), "│ Gemma-4-E4B-it-GGUF") {
		t.Fatalf("header = %q", ansi.Strip(m.renderHeader()))
	}
	before := len(m.messages)
	m = deliver(t, m, ping("Qwen3-4B-GGUF", "Qwen3-4B-GGUF", "lemonade", false))
	if !strings.Contains(ansi.Strip(m.renderHeader()), "Qwen3-4B-GGUF") {
		t.Fatal("the agent's own report must replace the gate's")
	}
	for _, msg := range m.messages[before:] {
		if strings.Contains(msg.Content, "reverted") {
			t.Fatal("the gate's name is not a model the agent was switched away from")
		}
	}
	claude := NewChatModelForFlagship(&claudeLaunchStub{}, "gaia", "GAIA", "", false, true).
		WithExpectedModel("Gemma-4-E4B-it-GGUF")
	if strings.Contains(ansi.Strip(claude.renderHeader()), "Gemma") {
		t.Fatal("a Claude session must not be labelled with the local model")
	}
}

type claudeLaunchStub struct{ nullClient }

func (*claudeLaunchStub) ClaudeAtLaunch() bool { return true }

func TestPickedAtGateFailureNamesTheSwitch(t *testing.T) {
	m, c, _ := startupChat(t, StartupPicked, "amd", "amd.gpt-4.1")
	m = openChat(t, m, c)
	m = deliver(t, m, ping("amd.gpt-4.1", "amd.gpt-4.1", "amd", true),
		event.CanonicalErrorEvent{Type: "error", Detail: "Lemonade Server is not reachable at http://localhost:13305 (boom). Start it."})
	errs := errorsIn(m)
	if len(errs) != 1 || !strings.HasPrefix(errs[0], "Couldn't switch to AMD LLM Gateway · gpt-4.1: the Lemonade server is not answering") {
		t.Fatalf("got %q", errs)
	}
}

func TestUnknownSavedClaudeIDIsRefusedWithoutARoundTrip(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "claude", "claude-sonnet-3")
	updated, cmd := m.Update(startupModelMsg{})
	m = updated.(ChatModel)
	runSends(cmd)
	if len(c.sent) != 0 {
		t.Fatalf("an id this build rejects must not reach the agent, sent %q", c.sent)
	}
	errs := errorsIn(m)
	if len(errs) != 1 || !strings.HasPrefix(errs[0], "Couldn't restore your last model, claude-sonnet-3: Unknown Claude model") {
		t.Fatalf("got %q", errs)
	}
	if m.providerPanel == nil || m.startup.inFlight {
		t.Fatal("the refusal must hand the choice back and leave no turn in flight")
	}
}

func TestFailedSwitchIsNotSavedAndAFailedSaveIsSaid(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModelForFlagship(c, "gaia", "GAIA", "", false, true).
		WithModelMemory(func(string, string) error { return errors.New("disk full") })
	m.width, m.height = 100, 30

	updated, _ := m.submit("/model " + flashID)
	m = updated.(ChatModel)
	m = deliver(t, m, event.CanonicalErrorEvent{Type: "error", Detail: "Unknown Lemonade model"})
	if n := len(errorsIn(m)); n != 1 {
		t.Fatalf("a failed switch shows the agent's refusal only, got %d errors", n)
	}

	updated, _ = m.submit("/model " + flashID)
	m = updated.(ChatModel)
	m = deliver(t, m, ping(flashID, flashID, "fireworks", true), event.CanonicalFinalEvent{Type: "final", Answer: "Switched."})
	errs := errorsIn(m)
	if !strings.Contains(errs[len(errs)-1], "will not be remembered") || !strings.Contains(errs[len(errs)-1], "disk full") {
		t.Fatalf("a failed save must be visible, got %q", errs)
	}
}

func TestCancellingTheRestoreSaysSo(t *testing.T) {
	m, c, saves := startupChat(t, StartupRestore, "fireworks", flashID)
	m = openChat(t, m, c)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	updated, _ = m.Update(doneMsg{})
	m = updated.(ChatModel)
	errs := errorsIn(m)
	if len(errs) != 1 || !strings.Contains(errs[0], ": you cancelled it.") || m.providerPanel == nil {
		t.Fatalf("got %q panel=%v", errs, m.providerPanel != nil)
	}
	if len(*saves) != 0 {
		t.Fatal("a cancelled restore must not be saved")
	}
}

// Only the switch's own confirmation counts. A Claude restore starts on the
// local default, so its startup ping names another model.
func TestAFinalWithoutTheSwitchIsNotARestore(t *testing.T) {
	m, c, saves := startupChat(t, StartupRestore, "claude", "claude-sonnet-5")
	m = openChat(t, m, c)
	m = deliver(t, m,
		ping("Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF", "lemonade", false),
		event.CanonicalFinalEvent{Type: "final", Answer: "something else"},
	)
	for _, msg := range m.messages {
		if strings.Contains(msg.Content, "Restored your last model") {
			t.Fatal("claimed a restore the agent never confirmed")
		}
	}
	if errs := errorsIn(m); len(errs) != 1 || m.providerPanel == nil || len(*saves) != 0 {
		t.Fatalf("an unconfirmed restore must hand back the choice, got %q", errs)
	}
}

// A question typed while the first-boot check runs must wait for the restore:
// sent first, it would run on whatever model the agent started on.
func TestAQuestionQueuedDuringSetupWaitsForTheRestore(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "claude", "claude-sonnet-5")
	m.setupChecking = true
	m.input.SetValue("what is 2+2")
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)
	if len(m.queued) != 1 {
		t.Fatalf("the question was not held, queued=%q", m.queued)
	}

	m.setupChecking = false
	cmd := m.releaseAfterSetupGate()
	updated, _ = m.Update(noopMsg{})
	m = updated.(ChatModel)
	if m.streaming || len(m.queued) != 1 {
		t.Fatalf("the question went out before the restore: streaming=%v queued=%q", m.streaming, m.queued)
	}
	updated, sendCmd := m.Update(cmd())
	m = updated.(ChatModel)
	runSends(sendCmd)
	if len(c.sent) != 1 || c.sent[0] != "/model claude-sonnet-5" {
		t.Fatalf("the restore must be the first thing sent, got %q", c.sent)
	}
}

type noopMsg struct{}

// A --query launch still restores first; the question waits in the queue.
func TestAnInitialQueryWaitsForTheRestore(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "fireworks", flashID)
	updated, _ := m.Update(sendQueryMsg{query: "hello"})
	m = updated.(ChatModel)
	if len(c.sent) != 0 || m.streaming || len(m.queued) != 1 {
		t.Fatalf("the initial query ran before the restore: streaming=%v queued=%q", m.streaming, m.queued)
	}
	m = openChat(t, m, c)
	if c.sent[0] != "/model "+flashID {
		t.Fatalf("sent %q", c.sent)
	}
}

type warmCapturingClient struct{ queryCapturingClient }

// schedulesWarmUp reports whether cmd, unwrapped through any batches, starts
// the warm-up. Only called on commands made of zero-delay messages.
func schedulesWarmUp(cmd tea.Cmd) bool {
	if cmd == nil {
		return false
	}
	switch msg := cmd().(type) {
	case startWarmUpMsg:
		return true
	case tea.BatchMsg:
		for _, sub := range msg {
			if schedulesWarmUp(sub) {
				return true
			}
		}
	}
	return false
}

func (*warmCapturingClient) SupportsWarmUp() bool { return true }

func warmChat(t *testing.T) (ChatModel, *warmCapturingClient) {
	t.Helper()
	c := &warmCapturingClient{}
	m := NewChatModelForFlagship(c, "gaia", "GAIA", "", false, true).
		WithStartupModel(StartupRestore, "local", "Qwen3-4B-GGUF")
	m.width, m.height = 120, 30
	return m, c
}

// The warm-up loads the model the session will use, so it waits for the
// restore and runs on the restored model — never before it, on the default.
func TestWarmUpFollowsAConfirmedRestore(t *testing.T) {
	m, c := warmChat(t)
	m = openChat(t, m, &c.queryCapturingClient)
	updated, _ := m.Update(eventMsg{ch: m.events, event: ping("Qwen3-4B-GGUF", "Qwen3-4B-GGUF", "lemonade", false)})
	m = updated.(ChatModel)
	updated, cmd := m.Update(eventMsg{ch: m.events, event: event.CanonicalFinalEvent{Type: "final", Answer: "Switched."}})
	m = updated.(ChatModel)
	if !schedulesWarmUp(cmd) {
		t.Fatal("a confirmed restore must start the warm-up")
	}
	if c.sent[0] != "/model Qwen3-4B-GGUF" {
		t.Fatalf("the restore must come before the warm-up, sent %q", c.sent)
	}
}

func TestNoWarmUpBeforeTheRestoreOrAfterAFailedOne(t *testing.T) {
	m, c := warmChat(t)
	count := func(m ChatModel) int {
		batch, _ := m.Init()().(tea.BatchMsg)
		return len(batch)
	}
	plain := NewChatModelForFlagship(&warmCapturingClient{}, "gaia", "GAIA", "", false, true)
	// plain schedules the warm-up; the restoring chat schedules the restore instead.
	if count(m) != count(plain) {
		t.Fatal("Init must send the restore in place of the warm-up, not both")
	}
	m = openChat(t, m, &c.queryCapturingClient)
	updated, cmd := m.Update(eventMsg{ch: m.events, event: event.CanonicalErrorEvent{Type: "error", Detail: "Unknown Lemonade model"}})
	m = updated.(ChatModel)
	if schedulesWarmUp(cmd) {
		t.Fatal("a failed restore must hand the choice back, not warm up the default")
	}
	if m.providerPanel == nil {
		t.Fatal("the picker did not open")
	}
}
