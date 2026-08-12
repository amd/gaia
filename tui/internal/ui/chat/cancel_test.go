package chat

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

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
