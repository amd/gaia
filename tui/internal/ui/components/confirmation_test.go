package components

import (
	"strconv"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// --- state machine: pending -> approved / denied / timed-out ---------------

func TestConfirmationStartsPending(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_draft", "Send reply to alice@example.com", "")
	if !m.Pending() {
		t.Fatalf("state = %v, want Pending", m.State())
	}
}

func TestConfirmationYApproves(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_draft", "Send reply to alice@example.com", "")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	if updated.State() != ConfirmationApproved {
		t.Errorf("state = %v, want Approved", updated.State())
	}
	if cmd == nil {
		t.Fatal("approving produced no command")
	}
	msg, ok := cmd().(ConfirmationDecidedMsg)
	if !ok {
		t.Fatalf("expected ConfirmationDecidedMsg, got %#v", cmd())
	}
	if !msg.Approved || msg.TimedOut {
		t.Errorf("decided msg = %+v, want Approved=true TimedOut=false", msg)
	}
	if msg.RunID != "run-1" || msg.Action != "send_draft" {
		t.Errorf("decided msg lost identity: %+v", msg)
	}
}

func TestConfirmationCapitalYApproves(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_draft", "", "")
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("Y")})
	if updated.State() != ConfirmationApproved {
		t.Errorf("state = %v, want Approved", updated.State())
	}
}

func TestConfirmationNDenies(t *testing.T) {
	m := NewConfirmationModel("run-1", "permanent_delete", "Delete the message from spam@example.com", "")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("n")})
	if updated.State() != ConfirmationDenied {
		t.Errorf("state = %v, want Denied", updated.State())
	}
	msg := cmd().(ConfirmationDecidedMsg)
	if msg.Approved {
		t.Error("'n' must never approve")
	}
}

func TestConfirmationEscDenies(t *testing.T) {
	m := NewConfirmationModel("run-1", "permanent_delete", "", "")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if updated.State() != ConfirmationDenied {
		t.Errorf("state = %v, want Denied", updated.State())
	}
	msg := cmd().(ConfirmationDecidedMsg)
	if msg.Approved {
		t.Error("Esc must never approve")
	}
}

// No other key may approve or deny by accident — a stray keystroke while the
// user is mid-thought must not silently make an irreversible decision.
func TestConfirmationOtherKeysDoNothing(t *testing.T) {
	for _, key := range []tea.KeyMsg{
		{Type: tea.KeyRunes, Runes: []rune("x")},
		{Type: tea.KeyRunes, Runes: []rune("1")},
		{Type: tea.KeyRunes, Runes: []rune(" ")},
		{Type: tea.KeyEnter},
		{Type: tea.KeyTab},
		{Type: tea.KeyUp},
	} {
		m := NewConfirmationModel("run-1", "send_draft", "", "")
		updated, cmd := m.Update(key)
		if updated.State() != ConfirmationPending {
			t.Errorf("key %v resolved the confirmation to %v; only y/n/esc may", key, updated.State())
		}
		if cmd != nil {
			t.Errorf("key %v produced a command; only y/n/esc may", key)
		}
	}
}

func TestConfirmationTimeoutDeniesWithWarning(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_now", "Send to bob@example.com", "")
	updated, cmd := m.ResolveTimeout(ConfirmationTimeoutMsg{RunID: "run-1"})
	if updated.State() != ConfirmationTimedOut {
		t.Fatalf("state = %v, want TimedOut", updated.State())
	}
	if cmd == nil {
		t.Fatal("the timeout produced no decision")
	}
	msg := cmd().(ConfirmationDecidedMsg)
	if msg.Approved {
		t.Error("a timeout must never resolve to approved")
	}
	if !msg.TimedOut {
		t.Error("the decision must record that it came from the timeout, so the UI can warn")
	}
}

// A timer for a DIFFERENT (superseded) confirmation must never resolve this one.
func TestConfirmationTimeoutIgnoresAnotherRun(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_now", "", "")
	updated, cmd := m.ResolveTimeout(ConfirmationTimeoutMsg{RunID: "run-2"})
	if updated.State() != ConfirmationPending {
		t.Errorf("state = %v, want still Pending (the timer was for a different run)", updated.State())
	}
	if cmd != nil {
		t.Error("a stale timer must not produce a decision")
	}
}

// Once resolved, further keys and a late timeout are no-ops — a decision is
// made once.
func TestConfirmationResolvedIgnoresFurtherInput(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_now", "", "")
	m, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	if m.State() != ConfirmationApproved {
		t.Fatalf("setup: state = %v, want Approved", m.State())
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("n")})
	if updated.State() != ConfirmationApproved {
		t.Errorf("a resolved confirmation flipped to %v on a further key", updated.State())
	}
	if cmd != nil {
		t.Error("a resolved confirmation must not emit a second decision")
	}

	updated, cmd = m.ResolveTimeout(ConfirmationTimeoutMsg{RunID: "run-1"})
	if updated.State() != ConfirmationApproved {
		t.Errorf("a resolved confirmation flipped to %v on a late timeout", updated.State())
	}
	if cmd != nil {
		t.Error("a resolved confirmation must not emit a second decision from a late timeout")
	}
}

// --- risk tiers --------------------------------------------------------

func TestClassifyActionRiskKnownActions(t *testing.T) {
	for action, want := range map[string]RiskTier{
		"send_now":                    RiskWrite,
		"send_draft":                  RiskWrite,
		"schedule_send":               RiskWrite,
		"forward_message":             RiskWrite,
		"accept_invite":               RiskWrite,
		"decline_invite":              RiskWrite,
		"create_event_from_email":     RiskWrite,
		"permanent_delete":            RiskDestructive,
		"quarantine_phishing_message": RiskDestructive,
	} {
		if got := ClassifyActionRisk(action); got != want {
			t.Errorf("ClassifyActionRisk(%q) = %v, want %v", action, got, want)
		}
	}
}

// An action this client has never heard of still gated the sidecar on
// confirmation, so it gets the MORE cautious tier, not less.
func TestClassifyActionRiskUnknownDefaultsToDestructive(t *testing.T) {
	if got := ClassifyActionRisk("some_future_tool"); got != RiskDestructive {
		t.Errorf("unknown action classified as %v, want RiskDestructive (fail cautious)", got)
	}
}

// Risk-tier -> badge/appearance mapping for all four tiers, including the two
// (Read, Denied) that a live needs_confirmation event never actually carries —
// the type is a complete, reusable classification, not one built to fit only
// what the wire happens to send today.
func TestRiskTierBadgesAreDistinctForAllFourTiers(t *testing.T) {
	badges := map[RiskTier]string{}
	for _, tier := range []RiskTier{RiskRead, RiskWrite, RiskDestructive, RiskDenied} {
		b := tier.Badge()
		if strings.TrimSpace(b) == "" {
			t.Errorf("tier %v has an empty badge", tier)
		}
		badges[tier] = b
	}
	seen := map[string]RiskTier{}
	for tier, b := range badges {
		if other, dup := seen[b]; dup {
			t.Errorf("tiers %v and %v render the same badge %q", tier, other, b)
		}
		seen[b] = tier
	}
}

func TestRiskTierBadgeWords(t *testing.T) {
	for tier, want := range map[RiskTier]string{
		RiskRead:        "READ",
		RiskWrite:       "WRITE",
		RiskDestructive: "DESTRUCTIVE",
		RiskDenied:      "BLOCKED",
	} {
		if b := tier.Badge(); !strings.Contains(b, want) {
			t.Errorf("Badge() for %v = %q, want it to contain %q", tier, b, want)
		}
	}
}

// --- rendering -----------------------------------------------------------

func TestConfirmationViewShowsActionSummaryAndBadge(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_draft", `Send reply to alice@example.com — subject "Re: invoice"?`, "")
	view := stripANSI(m.View())
	for _, want := range []string{"send_draft", "alice@example.com", "WRITE"} {
		if !strings.Contains(view, want) {
			t.Errorf("view missing %q:\n%s", want, view)
		}
	}
}

// The destructive tier carries a visible extra warning the write tier does not.
func TestDestructiveTierShowsExtraWarning(t *testing.T) {
	write := stripANSI(NewConfirmationModel("run-1", "send_draft", "x", "").View())
	destructive := stripANSI(NewConfirmationModel("run-1", "permanent_delete", "x", "").View())

	if strings.Contains(write, "cannot be") {
		t.Errorf("write tier should not show the irreversibility warning:\n%s", write)
	}
	if !strings.Contains(destructive, "cannot be") && !strings.Contains(destructive, "not be reversible") {
		t.Errorf("destructive tier must show an extra warning:\n%s", destructive)
	}
}

func TestConfirmationHintNamesTheKeysAndTimeout(t *testing.T) {
	view := stripANSI(NewConfirmationModel("run-1", "send_draft", "x", "").View())
	for _, want := range []string{"y approve", "n/esc deny", "30s"} {
		if !strings.Contains(view, want) {
			t.Errorf("hint missing %q:\n%s", want, view)
		}
	}
}

// Rendering at a narrow width must not panic and must not wrap raggedly — every
// line at or under the effective width (see #2518, a ragged-wrap defect in a
// neighbouring card this modal must not repeat). SetWidth floors at 24 — below
// that a bordered, padded panel has no room left for readable text — so a
// request under the floor is checked against the floor, not the raw ask.
func TestConfirmationRendersLegiblyAtNarrowWidths(t *testing.T) {
	for _, width := range []int{20, 24, 32, 40, 60, 80} {
		t.Run(strconv.Itoa(width), func(t *testing.T) {
			defer func() {
				if r := recover(); r != nil {
					t.Fatalf("View() panicked at width %d: %v", width, r)
				}
			}()
			effective := width
			if effective < 24 {
				effective = 24
			}
			m := NewConfirmationModel("run-1", "permanent_delete",
				"Permanently delete the message from a-very-long-sender-address@example-corp.com with subject "+
					`"Re: Re: Re: quarterly numbers and the follow up nobody asked for"?`, "")
			m.SetWidth(width)
			view := stripANSI(m.View())
			for _, line := range strings.Split(view, "\n") {
				if w := len([]rune(line)); w > effective {
					t.Errorf("width %d (effective %d): line is %d cols: %q", width, effective, w, line)
				}
			}
		})
	}
}

func TestConfirmationViewDoesNotPanicWhenResolved(t *testing.T) {
	for _, state := range []ConfirmState{ConfirmationApproved, ConfirmationDenied, ConfirmationTimedOut} {
		m := NewConfirmationModel("run-1", "send_draft", "x", "")
		switch state {
		case ConfirmationApproved:
			m, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
		case ConfirmationDenied:
			m, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("n")})
		case ConfirmationTimedOut:
			m, _ = m.ResolveTimeout(ConfirmationTimeoutMsg{RunID: "run-1"})
		}
		_ = m.View() // must not panic
	}
}
