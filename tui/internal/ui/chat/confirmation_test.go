package chat

import (
	"context"
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// confirmingClient records the out-of-band confirm decisions the chat model
// delivers, for the resume-model (confirm_url-present) path only.
type confirmingClient struct {
	nullClient
	calls []struct {
		runID    string
		approved bool
	}
	err error
}

func (c *confirmingClient) Confirm(_ context.Context, runID string, approved bool) error {
	c.calls = append(c.calls, struct {
		runID    string
		approved bool
	}{runID, approved})
	return c.err
}

func needsConfirmation(confirmURL string) event.CanonicalNeedsConfirmationEvent {
	return event.CanonicalNeedsConfirmationEvent{
		Type: "needs_confirmation", RunID: "run-1", Action: "send_draft",
		Summary: `Send reply to alice@example.com — subject "Re: invoice"?`, ConfirmURL: confirmURL,
	}
}

// The modal must actually go up, and it must not end the turn on its own — the
// email sidecar sends its own terminal event right after, but the client does
// not assume that; it reads whatever the stream sends next.
func TestNeedsConfirmationPutsUpTheModal(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m, needsConfirmation(""))

	if !m.streaming {
		t.Error("needs_confirmation must not end the turn on its own")
	}
	if m.confirmation == nil {
		t.Fatal("the confirmation modal was not put up")
	}
	if m.confirmation.RunID() != "run-1" || m.confirmation.Action() != "send_draft" {
		t.Errorf("modal lost the event's identity: runID=%q action=%q",
			m.confirmation.RunID(), m.confirmation.Action())
	}
	view := m.View()
	for _, want := range []string{"send_draft", "alice@example.com", "WRITE"} {
		if !strings.Contains(view, want) {
			t.Errorf("modal not rendered in chat (missing %q):\n%s", want, view)
		}
	}
}

// While the confirmation is pending, keys drive it rather than the composer —
// mirrors TestKeysRouteToThePendingQuestion for the confirmation modal.
func TestKeysRouteToThePendingConfirmation(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, needsConfirmation(""))

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("x")})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("an unrecognized key must not resolve the confirmation")
	}
	if m.confirmation == nil || !m.confirmation.Pending() {
		t.Fatal("the confirmation must still be pending")
	}
	if got := m.input.Value(); got != "" {
		t.Errorf("the keystroke leaked into the composer: %q", got)
	}
}

// Denying is always real: nothing was ever going to be sent, so there is
// nothing to deliver — the outcome is recorded and the modal clears.
func TestDenyingClearsTheModalAndRecordsOutcome(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, needsConfirmation(""))

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("n")})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("denying produced no command")
	}
	msg, ok := cmd().(components.ConfirmationDecidedMsg)
	if !ok {
		t.Fatalf("expected ConfirmationDecidedMsg, got %#v", cmd())
	}
	updatedModel, cmd2 := m.Update(msg)
	m = updatedModel.(ChatModel)

	if m.confirmation != nil {
		t.Error("a resolved confirmation must clear")
	}
	if cmd2 != nil {
		t.Error("denying with no confirm_url must not attempt delivery")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || !strings.Contains(last.Content, "denied") {
		t.Errorf("the denial was not recorded: %+v", last)
	}
	if len(m.activity) == 0 {
		t.Fatal("the activity panel must show the confirmation result")
	}
	act := m.activity[len(m.activity)-1]
	if act.Kind != "confirm" || act.Success == nil || *act.Success {
		t.Errorf("activity item wrong: %+v", act)
	}
}

// Esc means DENY for this modal — unlike a plain question, where Esc cancels
// the whole turn. That is the issue's explicit keyboard contract.
func TestEscDeniesTheConfirmationRatherThanCancellingTheTurn(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.cancelFn = func() { t.Error("Esc on a pending confirmation must not cancel the turn") }
	m = feed(t, m, needsConfirmation(""))

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("Esc produced no decision")
	}
	msg := cmd().(components.ConfirmationDecidedMsg)
	if msg.Approved {
		t.Error("Esc must never approve")
	}
	if !m.streaming {
		t.Error("Esc-as-deny must not cancel the turn — only the confirmation resolves")
	}
}

// Approving with no confirm_url (the shipping email sidecar's stateless D1
// contract, always) must not claim delivery it cannot make — see
// ConfirmationModel's doc comment and ui/oneshot.go's writeWithheld, which
// draws the identical line for the one-shot surface.
func TestApprovingWithNoConfirmURLDoesNotClaimDelivery(t *testing.T) {
	c := &confirmingClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30
	m.streaming = true
	m = feed(t, m, needsConfirmation(""))

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	m = updated.(ChatModel)
	msg := cmd().(components.ConfirmationDecidedMsg)
	updatedModel, cmd2 := m.Update(msg)
	m = updatedModel.(ChatModel)

	if cmd2 != nil {
		t.Error("no confirm_url means no channel to deliver on — nothing should be attempted")
	}
	if len(c.calls) != 0 {
		t.Errorf("Confirm() was called with no confirm_url on the event: %v", c.calls)
	}
	last := m.messages[len(m.messages)-1]
	if strings.Contains(last.Content, "approved,") == false || strings.Contains(last.Content, "nothing was actually sent") == false {
		t.Errorf("an unfulfillable approval must say so, not claim success: %q", last.Content)
	}
}

// Under the (currently unimplemented anywhere) resume model, an event that DOES
// carry a confirm_url gets a real delivery attempt through AgentConfirmer.
func TestApprovingWithConfirmURLDeliversViaConfirmer(t *testing.T) {
	c := &confirmingClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30
	m.streaming = true
	m = feed(t, m, needsConfirmation("/v1/email/query/run-1/confirm"))

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	m = updated.(ChatModel)
	msg := cmd().(components.ConfirmationDecidedMsg)
	if msg.ConfirmURL == "" {
		t.Fatal("the decision lost the confirm_url")
	}
	_, cmd2 := m.Update(msg)
	if cmd2 == nil {
		t.Fatal("approving with a confirm_url must attempt delivery")
	}
	result := cmd2()
	deliveryMsg, ok := result.(confirmActionResultMsg)
	if !ok {
		t.Fatalf("expected confirmActionResultMsg, got %#v", result)
	}
	if deliveryMsg.err != nil {
		t.Fatalf("delivery failed: %v", deliveryMsg.err)
	}
	if len(c.calls) != 1 || c.calls[0].runID != "run-1" || !c.calls[0].approved {
		t.Errorf("Confirm() calls = %+v, want one for run-1 approved=true", c.calls)
	}
}

// A failed delivery is surfaced, never swallowed.
func TestFailedConfirmDeliveryIsSurfaced(t *testing.T) {
	c := &confirmingClient{err: errors.New("the 'email' run had already ended")}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30
	m.streaming = true
	m = feed(t, m, needsConfirmation("/v1/email/query/run-1/confirm"))

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	m = updated.(ChatModel)
	msg := cmd().(components.ConfirmationDecidedMsg)
	_, cmd2 := m.Update(msg)
	result := cmd2().(confirmActionResultMsg)

	updated2, _ := m.Update(result)
	m = updated2.(ChatModel)
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "already ended") {
		t.Errorf("delivery failure not surfaced: %+v", last)
	}
}

// A turn that ends while a confirmation is up must take the modal down with
// it — mirrors TestTerminalEventClearsThePendingQuestion. This is the ORDINARY
// case against the current email sidecar: needs_confirmation is immediately
// followed by a final refusal in the same stream read.
func TestTerminalEventClearsThePendingConfirmation(t *testing.T) {
	for _, tc := range []struct {
		name string
		evt  interface{}
	}{
		{"final", event.CanonicalFinalEvent{Type: "final", Answer: "needs your explicit confirmation. Nothing has been sent."}},
		{"error", event.CanonicalErrorEvent{Type: "error", Detail: "the run failed"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m, _ := newTestModel(t)
			m.streaming = true
			m = feed(t, m, needsConfirmation(""))
			if m.confirmation == nil {
				t.Fatal("the confirmation was not put up")
			}
			m = feed(t, m, tc.evt)

			if m.confirmation != nil {
				t.Fatal("the confirmation outlived the turn it belonged to")
			}
			// The durable record survived even though the modal did not.
			found := false
			for _, msg := range m.messages {
				if strings.Contains(msg.Content, "resolved: denied") {
					found = true
				}
			}
			if !found {
				t.Errorf("no durable resolution line in the transcript: %+v", m.messages)
			}
			// The composer takes text again.
			updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("h")})
			if got := updated.(ChatModel).input.Value(); got != "h" {
				t.Errorf("composer value = %q, want \"h\" — keystrokes are still trapped", got)
			}
		})
	}
}

// A confirmation resolved by the run ending first must not ALSO leave an
// activity item behind — resolveConfirmationDecision (the keypress/timeout
// path) is what adds it, and the turn-end path takes a different, simpler
// route on purpose (see resolveConfirmationOnTurnEnd).
func TestTerminalEventDoesNotDoubleRecordTheOutcome(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, needsConfirmation(""))
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "declined"})

	count := 0
	for _, msg := range m.messages {
		if strings.Contains(msg.Content, "resolved:") {
			count++
		}
	}
	if count != 1 {
		t.Errorf("expected exactly one resolution line, got %d: %+v", count, m.messages)
	}
}

// The client-side timeout resolves to deny and clears the modal — this
// exercises the message ChatModel.Update actually receives from
// ConfirmationModel.TimeoutCmd, not just the component in isolation.
func TestConfirmationTimeoutDeniesAndClearsModal(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, needsConfirmation(""))

	updated, cmd := m.Update(components.ConfirmationTimeoutMsg{RunID: "run-1"})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("the timeout tick produced no decision")
	}
	msg := cmd().(components.ConfirmationDecidedMsg)
	if !msg.TimedOut || msg.Approved {
		t.Errorf("decision = %+v, want TimedOut=true Approved=false", msg)
	}
	updated2, _ := m.Update(msg)
	m = updated2.(ChatModel)
	if m.confirmation != nil {
		t.Error("the modal must clear once the timeout resolves it")
	}
	last := m.messages[len(m.messages)-1]
	if !strings.Contains(last.Content, "timeout") {
		t.Errorf("the timeout warning was not recorded: %q", last.Content)
	}
}

// The transcript line is the copy that SURVIVES: resolving clears the modal, so
// a frame later the only thing on screen saying why the call died is this.
// It named 30s while a live prompt waited ten minutes, because it spelled the
// duration by hand instead of reading the clock the prompt actually ran.
func TestTheRecordedTimeoutNamesTheClockThatActuallyRan(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, cmd := m.Update(components.ConfirmationTimeoutMsg{RunID: "run-1"})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("a live prompt must still be bounded")
	}
	msg := cmd().(components.ConfirmationDecidedMsg)
	if msg.Timeout != components.DeliverableConfirmationTimeout {
		t.Errorf("decision carried %v, want the live clock %v",
			msg.Timeout, components.DeliverableConfirmationTimeout)
	}

	updated2, _ := m.Update(msg)
	m = updated2.(ChatModel)
	last := m.messages[len(m.messages)-1].Content
	want := components.HumanTimeout(components.DeliverableConfirmationTimeout)
	if !strings.Contains(last, want) {
		t.Errorf("the record does not name the wait the user actually had: %q, want %q in it",
			last, want)
	}
	if strings.Contains(last, "30s") {
		t.Errorf("the record still names the short clock: %q", last)
	}
}

// A timeout tick for a confirmation that already resolved (or belongs to a
// superseded run) must be dropped, not misapplied.
func TestStaleConfirmationTimeoutIsDropped(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, needsConfirmation(""))
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "declined"})
	if m.confirmation != nil {
		t.Fatal("setup: the confirmation should already be cleared")
	}

	updated, cmd := m.Update(components.ConfirmationTimeoutMsg{RunID: "run-1"})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("a stale timeout must not produce a decision")
	}
	if m.confirmation != nil {
		t.Error("a stale timeout must not resurrect the modal")
	}
}

// Ctrl+C still works as the universal way out while a confirmation is up.
// Streaming does not clear synchronously — see requestCancel (#2901): Enter
// must stay blocked until the run's channel actually closes (doneMsg), not
// merely on the local cancelFn() call, or a resend on the same session can
// race the daemon's still-held run_lock into a 409.
func TestCtrlCWhilePendingConfirmationCancelsTheTurn(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	cancelled := false
	m.cancelFn = func() { cancelled = true }
	m = feed(t, m, needsConfirmation(""))

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC})
	m = updated.(ChatModel)
	if !cancelled {
		t.Error("Ctrl+C must still cancel the turn while a confirmation is pending")
	}
	if m.confirmation != nil {
		t.Error("Ctrl+C must take the confirmation down with the turn")
	}
	if !m.streaming {
		t.Error("streaming must stay true until the run's channel settles (doneMsg), not flip synchronously")
	}

	updated, _ = m.Update(doneMsg{ch: m.events})
	m = updated.(ChatModel)
	if m.streaming {
		t.Error("the turn must be marked stopped once settlement is confirmed")
	}
}
