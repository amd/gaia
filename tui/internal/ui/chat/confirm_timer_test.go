// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// One run asks many times. A timer armed for an earlier prompt must never
// resolve a later one: rc15 denied two fresh prompts within a second of them
// appearing, exactly ten minutes after two earlier prompts in the same run.

func shellPrompt(runID, confirmID, cmd string) event.CanonicalNeedsConfirmationEvent {
	return event.CanonicalNeedsConfirmationEvent{
		Type: "needs_confirmation", RunID: runID, Action: "run_shell_command",
		Summary: cmd, ConfirmID: confirmID,
	}
}

// answerPrompt presses key on the pending prompt and applies the decision.
func answerPrompt(t *testing.T, m ChatModel, key string) ChatModel {
	t.Helper()
	if m.confirmation == nil {
		t.Fatal("no prompt is up to answer")
	}
	c, cmd := m.confirmation.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(key)})
	m.confirmation = &c
	if cmd == nil {
		t.Fatalf("key %q did not resolve the prompt", key)
	}
	updated, delivery := m.Update(cmd())
	return applyDelivery(t, updated.(ChatModel), delivery)
}

// fire delivers a timer tick and, if it decided anything, applies the decision.
func fire(t *testing.T, m ChatModel, tick components.ConfirmationTimeoutMsg) (ChatModel, bool) {
	t.Helper()
	updated, cmd := m.Update(tick)
	m = updated.(ChatModel)
	if cmd == nil {
		return m, false
	}
	updated, delivery := m.Update(cmd())
	return applyDelivery(t, updated.(ChatModel), delivery), true
}

func assertPending(t *testing.T, m ChatModel, wantConfirmID string, c *permissionClient) {
	t.Helper()
	if m.confirmation == nil || !m.confirmation.Pending() {
		t.Fatalf("prompt %s was resolved by a timer that was not its own", wantConfirmID)
	}
	for _, id := range c.confirmIDs {
		if id == wantConfirmID {
			t.Fatalf("a decision for %s was sent to the agent before the user answered", wantConfirmID)
		}
	}
}

func lastDecision(t *testing.T, c *permissionClient) (string, client.PermissionDecision) {
	t.Helper()
	if len(c.decisions) == 0 {
		t.Fatal("no decision reached the agent")
	}
	n := len(c.decisions) - 1
	return c.confirmIDs[n], c.decisions[n]
}

// applyDelivery applies the delivery command a decision returns, so the
// permission client records what the agent was told.
func applyDelivery(t *testing.T, m ChatModel, cmd tea.Cmd) ChatModel {
	t.Helper()
	if cmd == nil {
		return m
	}
	updated, _ := m.Update(cmd())
	return updated.(ChatModel)
}

// The rc15 defect: A answered, B shows up, A's old timer expires — B stays up.
// The flagship sends no run id at all, so the empty case is the real one.
func TestAnAnsweredPromptsTimerDoesNotDenyTheNextOne(t *testing.T) {
	for _, runID := range []string{"run-1", ""} {
		m, c := liveModel(t)
		m.streaming = true
		m = feed(t, m, shellPrompt(runID, "cid-A", "ls"))
		timerA := m.confirmation.ExpiryMsg()
		m = answerPrompt(t, m, "y")

		m = feed(t, m, shellPrompt(runID, "cid-B", "pytest"))
		m, decided := fire(t, m, timerA)
		if decided {
			t.Fatalf("run %q: A's timer produced a decision while B was pending", runID)
		}
		assertPending(t, m, "cid-B", c)
	}
}

// B's OWN timer still bounds B, and the timeout names B, not A.
func TestAPromptStillTimesOutOnItsOwnTimer(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, shellPrompt("run-1", "cid-A", "ls"))
	m = answerPrompt(t, m, "y")
	m = feed(t, m, shellPrompt("run-1", "cid-B", "pytest"))

	updated, cmd := m.Update(m.confirmation.ExpiryMsg())
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("B's own timer did not resolve B")
	}
	decidedMsg := cmd().(components.ConfirmationDecidedMsg)
	if !decidedMsg.TimedOut || decidedMsg.Approved {
		t.Fatalf("decision = %+v, want a timed-out non-approval", decidedMsg)
	}
	updated, delivery := m.Update(decidedMsg)
	m = applyDelivery(t, updated.(ChatModel), delivery)
	if m.confirmation != nil {
		t.Error("the timed-out prompt is still up")
	}
	id, d := lastDecision(t, c)
	if id != "cid-B" || d != client.PermissionTimeout {
		t.Errorf("agent was told %v for %q, want a timeout for cid-B", d, id)
	}
}

// Many prompts in quick succession: none of the earlier timers touches the
// one that is up now, and the current one's own timer still does.
func TestRapidPromptsKeepTheirOwnTimers(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	var stale []components.ConfirmationTimeoutMsg
	for _, id := range []string{"cid-1", "cid-2", "cid-3", "cid-4"} {
		m = feed(t, m, shellPrompt("run-1", id, "echo "+id))
		stale = append(stale, m.confirmation.ExpiryMsg())
		m = answerPrompt(t, m, "n")
	}
	m = feed(t, m, shellPrompt("run-1", "cid-5", "echo 5"))
	for _, tick := range stale {
		var decided bool
		m, decided = fire(t, m, tick)
		if decided {
			t.Fatalf("a stale timer (seq %d) resolved cid-5", tick.Seq)
		}
	}
	assertPending(t, m, "cid-5", c)

	m, decided := fire(t, m, m.confirmation.ExpiryMsg())
	if !decided || m.confirmation != nil {
		t.Fatal("cid-5's own timer did not resolve it")
	}
}

// A prompt replaced while still pending: neither its timer nor a decision it
// had already emitted may close the prompt that replaced it.
func TestAReplacedPromptCannotResolveItsReplacement(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, shellPrompt("run-1", "cid-A", "ls"))
	timerA := m.confirmation.ExpiryMsg()
	_, cmdA := m.confirmation.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	lateDecisionA := cmdA().(components.ConfirmationDecidedMsg)

	m = feed(t, m, shellPrompt("run-1", "cid-B", "pytest"))

	m, decided := fire(t, m, timerA)
	if decided {
		t.Fatal("the replaced prompt's timer resolved its replacement")
	}
	updated, _ := m.Update(lateDecisionA)
	m = updated.(ChatModel)
	assertPending(t, m, "cid-B", c)
}

// A timer outliving its turn is dropped, including when the next turn reuses
// the run id and has its own prompt up.
func TestATimerFromAnEndedTurnIsDropped(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, shellPrompt("run-1", "cid-A", "ls"))
	timerA := m.confirmation.ExpiryMsg()
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "done"})

	m, decided := fire(t, m, timerA)
	if decided {
		t.Fatal("a timer from an ended turn produced a decision")
	}

	m.streaming = true
	m = feed(t, m, shellPrompt("run-1", "cid-B", "pytest"))
	m, decided = fire(t, m, timerA)
	if decided {
		t.Fatal("a timer from an ended turn resolved the next turn's prompt")
	}
	assertPending(t, m, "cid-B", c)
}

// Two runs with timers in flight: each timer only ever touches its own run's
// prompt, and a tick carrying the right prompt number but the wrong run does
// nothing either.
func TestOverlappingRunsKeepTheirTimersApart(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, shellPrompt("run-1", "cid-A", "ls"))
	timerA := m.confirmation.ExpiryMsg()

	m = feed(t, m, shellPrompt("run-2", "cid-B", "pytest"))
	timerB := m.confirmation.ExpiryMsg()

	m, decided := fire(t, m, timerA)
	if decided {
		t.Fatal("run-1's timer resolved run-2's prompt")
	}
	forged := components.ConfirmationTimeoutMsg{RunID: "run-1", Seq: timerB.Seq}
	m, decided = fire(t, m, forged)
	if decided {
		t.Fatal("a tick for the wrong run resolved the prompt")
	}
	assertPending(t, m, "cid-B", c)

	updated, cmd := m.Update(timerB)
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("run-2's own timer did not resolve its prompt")
	}
	updated, delivery := m.Update(cmd())
	applyDelivery(t, updated.(ChatModel), delivery)
	if id, d := lastDecision(t, c); id != "cid-B" || d != client.PermissionTimeout {
		t.Errorf("agent was told %v for %q, want a timeout for cid-B", d, id)
	}
}
