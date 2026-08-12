package chat

import (
	"context"
	"errors"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
)

// cancelingClient implements client.AgentCanceler on top of nullClient, so
// requestCancel's AgentCanceler branch (#2901) can be exercised without a
// real daemon. Mirrors confirmingClient's shape in confirmation_test.go.
type cancelingClient struct {
	nullClient
	calls int
	err   error
}

func (c *cancelingClient) Cancel(context.Context) error {
	c.calls++
	return c.err
}

// A cancelled turn must not free the composer until the daemon has actually
// settled it. cancelFn only tears down THIS client's own read of the SSE
// stream; the daemon's session lock is released by the sidecar's worker
// thread on its own schedule (cooperative cancellation, checked once per
// agent-loop step — see hub/agents/email/python/gaia_agent_email/query_routes.py).
// Flipping m.streaming synchronously in handleKey, before that settles, is
// exactly the window that let a same-session resend race the lock into a 409
// (#2901). doneMsg — the run's event channel actually closing — is the one
// locally-observable settlement signal available without a new server call.
func TestEscDoesNotReenableSendBeforeSettlement(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	cancelled := false
	m.cancelFn = func() { cancelled = true }

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if !cancelled {
		t.Fatal("Esc must still cancel the turn locally")
	}
	if !m.streaming {
		t.Fatal("streaming must stay true immediately after Esc — the daemon has not " +
			"confirmed the cancel settled yet, so Enter must stay blocked")
	}

	// Enter while the cancel is still pending must be a no-op — it must NOT
	// start a new query on the same session.
	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("Enter must not send a new query before the previous turn has settled")
	}

	// The channel closing (doneMsg) is what settlement looks like.
	updated, _ = m.Update(doneMsg{ch: m.events})
	m = updated.(ChatModel)

	if m.streaming {
		t.Error("streaming must clear once the run's channel has actually closed")
	}
}

// Same reconciliation on Ctrl+C.
func TestCtrlCDoesNotReenableSendBeforeSettlement(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.cancelFn = func() {}

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC})
	m = updated.(ChatModel)

	if !m.streaming {
		t.Fatal("streaming must stay true immediately after Ctrl+C, pending settlement")
	}

	updated, _ = m.Update(doneMsg{ch: m.events})
	m = updated.(ChatModel)

	if m.streaming {
		t.Error("streaming must clear once the run's channel has actually closed")
	}

	var sawCancelled bool
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && msg.Content == "cancelled" {
			sawCancelled = true
		}
	}
	if !sawCancelled {
		t.Error("the transcript should record the confirmed cancellation, not just the request")
	}
}

// A doneMsg for a channel that is no longer the active one (a stale delivery
// from an already-superseded turn) must not resurrect state.
func TestStaleDoneMsgAfterCancelIsDropped(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.cancelFn = func() {}

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	staleCh := make(chan interface{})
	close(staleCh)
	updated, _ = m.Update(doneMsg{ch: staleCh})
	m = updated.(ChatModel)

	if !m.streaming {
		t.Error("a doneMsg from an unrelated channel must not settle THIS turn")
	}
}

// TestEscUsesAgentCancelerInsteadOfLocalAbort is the real fix for #2901 AC#1.
// A prior version of this fix (see the doneMsg-gating tests above) still had
// Esc call cancelFn — the caller's own context.CancelFunc — to produce that
// doneMsg. Live evidence against the real daemon found that this local abort
// IS what raced the daemon's session lock: 5 of 5 cancel-then-resend attempts
// still hit a bare 409, because the local read closing has nothing to do with
// when the sidecar's worker thread actually releases run_lock.
//
// Against a transport that implements client.AgentCanceler (the daemon
// relay — see SSEClient.Cancel's doc comment for why its channel close is a
// TRUE settlement signal), Esc must ask the SERVER to stop the run instead,
// and must leave cancelFn / m.events untouched so the existing read keeps
// running to that real close.
func TestEscUsesAgentCancelerInsteadOfLocalAbort(t *testing.T) {
	c := &cancelingClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30
	m.streaming = true
	localAborted := false
	m.cancelFn = func() { localAborted = true }
	ch := make(chan interface{})
	m.events = ch

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if localAborted {
		t.Error("Esc must not tear down the local read when the transport can ask the server instead")
	}
	if m.cancelFn == nil {
		t.Error("cancelFn must stay set -- the local read is still the live one, waiting on the run's own doneMsg")
	}
	if !m.streaming {
		t.Fatal("streaming must stay true immediately after Esc, pending settlement")
	}
	if m.events != ch {
		t.Error("the run's event channel must not be replaced or cleared by requestCancel")
	}
	if cmd == nil {
		t.Fatal("Esc must return a command that delivers the Cancel() call")
	}

	// Run the returned command -- this is what actually calls Cancel().
	msg := cmd()
	if c.calls != 1 {
		t.Fatalf("Cancel() must be called exactly once, got %d", c.calls)
	}
	if failed, isFailure := msg.(cancelRequestFailedMsg); isFailure {
		t.Fatalf("Cancel() succeeded in the fake but produced a failure message: %v", failed.err)
	}

	// Only the run's OWN doneMsg -- the server's real close, not this call --
	// may settle the turn.
	updated, _ = m.Update(doneMsg{ch: m.events})
	m = updated.(ChatModel)
	if m.streaming {
		t.Error("streaming must clear once the run's channel has actually closed")
	}
}

// A failed Cancel() REQUEST (the ask to the server could not even be
// delivered) must not itself free the composer -- the run may still be live,
// and only its own settlement (doneMsg/errMsg) gets to decide that. Silently
// re-enabling Enter here would reopen the exact race this fix closes.
func TestFailedCancelRequestDoesNotReenableSend(t *testing.T) {
	c := &cancelingClient{err: errors.New("daemon unreachable")}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30
	m.streaming = true
	m.cancelFn = func() {}

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("Esc must still return a command even when Cancel() is about to fail")
	}
	msg := cmd()

	failed, ok := msg.(cancelRequestFailedMsg)
	if !ok {
		t.Fatalf("expected cancelRequestFailedMsg, got %T", msg)
	}
	if failed.err == nil {
		t.Error("the failure must carry the underlying error")
	}

	updated2, _ := m.Update(msg)
	m = updated2.(ChatModel)
	if !m.streaming {
		t.Error("a failed cancel REQUEST must not itself flip streaming off -- the run might still be live")
	}
	if !m.cancelPending {
		t.Error("cancelPending must stay true -- Esc was still requested, only the ask to the server failed")
	}
}

// TestSecondCancelThenResendDoesNotQuitTheApp is the live-evidence regression
// for #2901: on the real daemon, a cancelled turn NEVER settles via doneMsg —
// it settles via its own CanonicalFinalEvent (the cooperative server-side
// cancel just breaks the agent loop with an ordinary "stopped" answer, which
// query_routes.py's _terminal_from_run_result reports as a plain `final`, not
// an `error`). CanonicalFinalEvent's handler stops rescheduling waitForEvent
// once it fires (correctly -- no more events are expected), so doneMsg for
// that channel can never arrive.
//
// Before the fix, cancelPending was cleared only by doneMsg, so it got stuck
// true for the rest of the session. A SECOND cancel-then-resend cycle then
// hit requestCancel's `!m.cancelPending` guard failing on the very first
// Esc/Ctrl+C of the new turn -- which falls through to tea.Quit instead of
// cancelling. Reproduced live 3/3 against the real daemon (clean quit, no
// panic).
func TestSecondCancelThenResendDoesNotQuitTheApp(t *testing.T) {
	c := &cancelingClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30

	// --- Cycle 1: send, cancel, and settle the way the real daemon does --
	// via the run's OWN terminal event, never doneMsg.
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	ch1 := make(chan interface{})
	updated, _ = m.Update(channelReadyMsg{ch: ch1})
	m = updated.(ChatModel)

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("test setup: first Esc must return the Cancel() command")
	}
	updated, _ = m.Update(cmd()) // deliver the (successful) Cancel() result
	m = updated.(ChatModel)
	if !m.cancelPending {
		t.Fatal("test setup: cancelPending must be true after the first Esc")
	}

	updated, _ = m.Update(eventMsg{ch: ch1, event: event.CanonicalFinalEvent{
		Type:   "final",
		Answer: "The request was stopped because it exceeded the allowed time before completing.",
	}})
	m = updated.(ChatModel)
	if m.streaming {
		t.Fatal("test setup: the terminal event must end the first turn")
	}

	// --- Cycle 2: resend, then cancel again.
	updated, _ = m.Update(sendQueryMsg{query: "try again"})
	m = updated.(ChatModel)
	if !m.streaming {
		t.Fatal("test setup: the resend must start a new turn")
	}

	updated, cmd = m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("second Esc must still return a command")
	}
	if _, quit := cmd().(tea.QuitMsg); quit {
		t.Fatal("second Esc quit the app instead of cancelling the second turn -- " +
			"cancelPending was left stuck true by the first turn's settlement (#2901)")
	}
	if !m.streaming {
		t.Error("the second turn must still be marked streaming -- Esc must cancel it, not fall through to quit")
	}
}

// Same reproduction on Ctrl+C, which hits the identical guard in handleKey.
func TestSecondCtrlCThenResendDoesNotQuitTheApp(t *testing.T) {
	c := &cancelingClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30

	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	ch1 := make(chan interface{})
	updated, _ = m.Update(channelReadyMsg{ch: ch1})
	m = updated.(ChatModel)

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("test setup: first Ctrl+C must return the Cancel() command")
	}
	updated, _ = m.Update(cmd())
	m = updated.(ChatModel)

	updated, _ = m.Update(eventMsg{ch: ch1, event: event.CanonicalFinalEvent{
		Type:   "final",
		Answer: "The request was stopped because it exceeded the allowed time before completing.",
	}})
	m = updated.(ChatModel)

	updated, _ = m.Update(sendQueryMsg{query: "try again"})
	m = updated.(ChatModel)

	updated, cmd = m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("second Ctrl+C must still return a command")
	}
	if _, quit := cmd().(tea.QuitMsg); quit {
		t.Fatal("second Ctrl+C quit the app instead of cancelling the second turn (#2901)")
	}
}
