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

// An UNDELIVERABLE prompt keeps the auto-deny, and says so. There is nothing
// to wait for: the run already ended, so expiring costs nothing and pretending
// otherwise would be the lie.
func TestConfirmationHintNamesTheKeysAndTimeout(t *testing.T) {
	view := stripANSI(NewConfirmationModel("run-1", "send_draft", "x", "").View())
	for _, want := range []string{"y run once", "n/esc deny", "30s"} {
		if !strings.Contains(view, want) {
			t.Errorf("hint missing %q:\n%s", want, view)
		}
	}
	if strings.Contains(view, " a allow any ") {
		t.Errorf("always must not be offered with no channel to grant it on:\n%s", view)
	}
}

// A LIVE prompt offers all three outcomes and runs the long clock, not the
// short one. This is the defect the whole change exists for: a real question
// that silently answered itself after 30s.
func TestLiveConfirmationOffersAlwaysAndABoundedClock(t *testing.T) {
	m := NewConfirmationModel("run-1", "run_shell_command",
		`Run 'run_shell_command' with command="pwd"?`, "").
		WithLiveChannel("cid-1", "pwd")
	view := stripANSI(m.View())

	for _, want := range []string{
		"y run once",
		"a allow `pwd` this session",
		"n/esc deny",
		"auto-denies in 10",
		`command="pwd"`, // the payload the old prompt hid
	} {
		if !strings.Contains(view, want) {
			t.Errorf("live modal missing %q:\n%s", want, view)
		}
	}
	// The short clock belongs to an undeliverable prompt. Advertising 30s on a
	// live one is the defect the long bound exists to avoid: it steals a
	// decision the user was in the middle of making.
	if strings.Contains(view, "30s") {
		t.Errorf("a live prompt must not advertise the short countdown:\n%s", view)
	}
	if !m.Deliverable() {
		t.Error("a live prompt must be deliverable")
	}
	// Every prompt is bounded — unbounded is a hang with no way out — so the
	// live one differs only in how long it waits.
	if m.timeout() != DeliverableConfirmationTimeout {
		t.Errorf("live clock = %v, want %v", m.timeout(), DeliverableConfirmationTimeout)
	}
}

// The three outcomes are distinct on the wire, and "always" implies approval.
func TestEachOutcomeIsDistinct(t *testing.T) {
	for _, tc := range []struct {
		key          string
		wantApproved bool
		wantAlways   bool
		wantState    ConfirmState
	}{
		{"y", true, false, ConfirmationApproved},
		{"a", true, true, ConfirmationAlways},
		{"n", false, false, ConfirmationDenied},
	} {
		t.Run(tc.key, func(t *testing.T) {
			m := NewConfirmationModel("run-1", "run_shell_command", "x", "").
				WithLiveChannel("cid-1", "gh issue list")
			updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(tc.key)})
			if cmd == nil {
				t.Fatalf("%q did not resolve the confirmation", tc.key)
			}
			msg := cmd().(ConfirmationDecidedMsg)
			if msg.Approved != tc.wantApproved || msg.Always != tc.wantAlways {
				t.Errorf("%q -> Approved=%v Always=%v, want %v/%v",
					tc.key, msg.Approved, msg.Always, tc.wantApproved, tc.wantAlways)
			}
			if updated.State() != tc.wantState {
				t.Errorf("%q -> state %v, want %v", tc.key, updated.State(), tc.wantState)
			}
			if msg.ConfirmID != "cid-1" {
				t.Errorf("the decision lost the prompt id: %q", msg.ConfirmID)
			}
			if !msg.Deliverable {
				t.Error("a live decision must be marked deliverable")
			}
		})
	}
}

// "always" on a prompt with no channel would promise a suppression nobody
// records, so the key does nothing at all rather than lying.
func TestAlwaysIsInertWithoutALiveChannel(t *testing.T) {
	m := NewConfirmationModel("run-1", "send_draft", "x", "")
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	if cmd != nil {
		t.Error("'a' must not resolve an undeliverable confirmation")
	}
	if !updated.Pending() {
		t.Error("the confirmation must still be pending")
	}
}

// The timeout is the one thing that must never change meaning: where it still
// applies, expiry denies; where it does not, it is not armed at all.
func TestTimeoutNeverApproves(t *testing.T) {
	t.Run("undeliverable expires to denied", func(t *testing.T) {
		m := NewConfirmationModel("run-1", "send_draft", "x", "")
		updated, cmd := m.ResolveTimeout(ConfirmationTimeoutMsg{RunID: "run-1"})
		if cmd == nil {
			t.Fatal("the timeout produced no decision")
		}
		msg := cmd().(ConfirmationDecidedMsg)
		if msg.Approved || msg.Always || !msg.TimedOut {
			t.Errorf("expiry must deny: %+v", msg)
		}
		if updated.State() != ConfirmationTimedOut {
			t.Errorf("state = %v, want timed out", updated.State())
		}
	})

	// A live prompt used to ignore the tick entirely and wait forever. It now
	// waits far longer than a decision takes, and then denies — because a turn
	// that can never end is not the safer failure. What must NOT change is the
	// direction: expiry denies, never approves.
	t.Run("live prompt expires on the long clock, and denies", func(t *testing.T) {
		m := NewConfirmationModel("run-1", "run_shell_command", "x", "").
			WithLiveChannel("cid-1", "gh issue list")
		updated, cmd := m.ResolveTimeout(ConfirmationTimeoutMsg{RunID: "run-1"})
		if cmd == nil {
			t.Fatal("a live prompt must still be bounded")
		}
		msg := cmd().(ConfirmationDecidedMsg)
		if msg.Approved || msg.Always || !msg.TimedOut {
			t.Errorf("expiry must deny, never approve: %+v", msg)
		}
		if updated.State() != ConfirmationTimedOut {
			t.Errorf("state = %v, want timed out", updated.State())
		}
	})

	t.Run("the live clock is much longer than the undeliverable one", func(t *testing.T) {
		live := NewConfirmationModel("r", "a", "s", "").WithLiveChannel("c", "")
		dead := NewConfirmationModel("r", "a", "s", "")
		if live.timeout() <= dead.timeout() {
			t.Errorf("live %v must outlast undeliverable %v — a human is reading it",
				live.timeout(), dead.timeout())
		}
		if dead.timeout() != ConfirmationTimeout {
			t.Errorf("undeliverable clock changed: %v", dead.timeout())
		}
	})
}

// "Always" is the agent's call, not the renderer's. No scope on the event means
// no grant is on offer, however deliverable the decision is - a client that
// invented one would promise something nothing enforces.
func TestAlwaysNeedsAScopeFromTheAgent(t *testing.T) {
	m := NewConfirmationModel("run-1", "run_shell_command", "x", "").
		WithLiveChannel("cid-1", "")
	view := stripANSI(m.View())
	if strings.Contains(view, " a allow ") {
		t.Errorf("always offered with no scope to grant:\n%s", view)
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	if cmd != nil || !updated.Pending() {
		t.Error("'a' must be inert when the agent named no scope")
	}
}

// The grant the modal promises is the one the agent will record - never widened
// to the whole tool. One keypress on `gh issue list` must not read as, or
// become, "any run_shell_command".
func TestAlwaysPromisesOnlyTheScopeTheAgentNamed(t *testing.T) {
	m := NewConfirmationModel("run-1", "run_shell_command",
		`Run 'run_shell_command' with command="gh issue list"?`, "").
		WithLiveChannel("cid-1", "gh issue list")

	view := stripANSI(m.View())
	if !strings.Contains(view, "a allow `gh issue list` this session") {
		t.Errorf("the offer must name the scope:\n%s", view)
	}
	for _, forbidden := range []string{"any arguments", "any run_shell_command"} {
		if strings.Contains(view, forbidden) {
			t.Errorf("the offer must not read as tool-wide (%q):\n%s", forbidden, view)
		}
	}

	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	msg := cmd().(ConfirmationDecidedMsg)
	if msg.AlwaysScope != "gh issue list" {
		t.Errorf("the decision carried scope %q, want the one shown", msg.AlwaysScope)
	}
}

// A `pwd` must not be badged the same as a permanent delete: a badge that
// reads DESTRUCTIVE on everything stops carrying information.
func TestKnownToolsAreClassifiedRatherThanAllDestructive(t *testing.T) {
	for tool, want := range map[string]RiskTier{
		"write_file":        RiskWrite,
		"install_skill":     RiskWrite,
		"run_shell_command": RiskDestructive,
		"permanent_delete":  RiskDestructive,
	} {
		if got := ClassifyActionRisk(tool); got != want {
			t.Errorf("ClassifyActionRisk(%q) = %v, want %v", tool, got, want)
		}
	}
	// The cautious default for something this build has never heard of stands.
	if got := ClassifyActionRisk("some_tool_from_the_future"); got != RiskDestructive {
		t.Errorf("unknown tool tier = %v, want destructive", got)
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

// A warning users learn is false on the safe calls is the warning they dismiss
// without reading on the dangerous one. `pwd` is tiered Destructive because
// run_shell_command's NAME does not bound it — not because pwd is destructive.
func TestDestructiveWarningDistinguishesUnboundedFromIrreversible(t *testing.T) {
	shell := destructiveWarning("run_shell_command")
	if strings.Contains(shell, "may not be reversible") {
		t.Errorf("shell warning still claims irreversibility: %q", shell)
	}
	if !strings.Contains(shell, "check the command above") {
		t.Errorf("shell warning must point at the command on screen: %q", shell)
	}

	if got := destructiveWarning("run_cli_command"); got != shell {
		t.Errorf("both shell tools must share the wording, got %q", got)
	}

	// A genuinely irreversible action keeps the strong claim.
	del := destructiveWarning("permanent_delete")
	if !strings.Contains(del, "may not be reversible") {
		t.Errorf("permanent_delete lost its warning: %q", del)
	}
}

// The modal renders the wording the tier chose, not a hardcoded sentence.
func TestConfirmationViewUsesTheUnboundedWording(t *testing.T) {
	m := NewConfirmationModel(
		"run-1",
		"run_shell_command",
		`Run 'run_shell_command' with command="pwd"?`,
		"",
	)

	view := m.View()
	if strings.Contains(view, "may not be reversible") {
		t.Errorf("a pwd confirmation still reads as irreversible:\n%s", view)
	}
	if !strings.Contains(view, "DESTRUCTIVE") {
		t.Errorf("the cautious badge should remain:\n%s", view)
	}
}
