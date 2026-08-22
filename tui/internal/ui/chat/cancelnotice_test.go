package chat

import (
	"strings"
	"testing"
)

// "cancelling…" describes a request in flight. Left in the transcript it became
// a permanent claim — and when the cancel lost the race it sat directly above
// the answer that did arrive, so the scrollback contradicted itself.
func TestCancellingNoticeIsRemovedWhenTheTurnSettles(t *testing.T) {
	m := &ChatModel{
		cancelPending: true,
		messages: []Message{
			{Role: RoleUser, Content: "load the skill"},
			{Role: RoleStatus, Content: cancellingNotice + " (the agent stops at its next step)"},
		},
	}

	m.settleTurn()

	for _, msg := range m.messages {
		if strings.HasPrefix(msg.Content, cancellingNotice) {
			t.Fatalf("the transient notice outlived the turn: %q", msg.Content)
		}
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || last.Content != "cancelled" {
		t.Errorf("last message = %q (%v), want the confirmed \"cancelled\"", last.Content, last.Role)
	}
}

// The race that produced the contradiction: the answer lands after the cancel
// was asked for but before the turn settles, so the notice is no longer last.
func TestCancellingNoticeIsRemovedEvenWhenAnAnswerLandedAfterIt(t *testing.T) {
	m := &ChatModel{
		cancelPending: false, // the cancel lost — the turn completed normally
		messages: []Message{
			{Role: RoleUser, Content: "load the skill"},
			{Role: RoleStatus, Content: cancellingNotice + " (the agent stops at its next step)"},
			{Role: RoleAssistant, Content: "Got it. The github-triage skill is now active."},
		},
	}

	m.settleTurn()

	for _, msg := range m.messages {
		if strings.HasPrefix(msg.Content, cancellingNotice) {
			t.Fatalf("notice survived above the answer it contradicts: %q", msg.Content)
		}
	}
	if got := m.messages[len(m.messages)-1].Content; !strings.Contains(got, "github-triage") {
		t.Errorf("the answer was disturbed: last message = %q", got)
	}
	// Nothing was cancelled, so nothing should claim it was.
	for _, msg := range m.messages {
		if msg.Content == "cancelled" {
			t.Error("a completed turn was labelled cancelled")
		}
	}
}

func TestSettleTurnLeavesAnUncancelledTranscriptAlone(t *testing.T) {
	m := &ChatModel{
		messages: []Message{
			{Role: RoleUser, Content: "hello"},
			{Role: RoleAssistant, Content: "hi"},
		},
	}

	m.settleTurn()

	if len(m.messages) != 2 {
		t.Errorf("settleTurn changed a clean transcript: %d messages, want 2", len(m.messages))
	}
}
