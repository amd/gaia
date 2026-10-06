// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// The prompt from #4446, as a current agent sends it: the command once, with
// its folder on its own line, labelled by what it does, with a family grant.
func issueTestRunCall() event.CanonicalNeedsConfirmationEvent {
	return event.CanonicalNeedsConfirmationEvent{
		Type: "needs_confirmation", RunID: "run-1", Action: "run_shell_command",
		Summary:   "python -m pytest -q tests/ 2>&1\nin ~/AppData/…/newuser/proj",
		ConfirmID: "cid-1", AlwaysScope: "pytest", Risk: "execute",
	}
}

func shiftTab(t *testing.T, m ChatModel) ChatModel {
	t.Helper()
	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyShiftTab})
	return updated.(ChatModel)
}

func plainFrame(m ChatModel) string { return ansi.Strip(visibleFrame(m)) }

// A test run is labelled for what it does, shows the command once, and offers
// y / a / n with the mode hint — asserted on the painted frame.
func TestTheTestRunPromptIsProportionate(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = transcript(m, 3)
	m = feed(t, m, issueTestRunCall())

	frame := plainFrame(m)
	t.Logf("\n%s", frame)
	for _, want := range []string{
		"RUNS CODE",
		"Run this command?",
		"in ~/AppData/…/newuser/proj",
		"mode: ask · shift+tab to change",
		"y once · a always: pytest (this session) · n/esc deny",
	} {
		if !strings.Contains(frame, want) {
			t.Errorf("frame missing %q", want)
		}
	}
	for _, forbidden := range []string{"DESTRUCTIVE", "may not be reversible", "check the command above"} {
		if strings.Contains(frame, forbidden) {
			t.Errorf("a test run must not read %q", forbidden)
		}
	}
	if n := strings.Count(frame, "python -m pytest"); n != 1 {
		t.Errorf("the command is painted %d times, want once", n)
	}
}

// Shift+Tab: ask → accept edits → (full access, once unlocked) → ask.
func TestShiftTabCyclesThePermissionModes(t *testing.T) {
	m, c := liveModel(t)
	m.resize()

	m = shiftTab(t, m)
	if m.permissionMode() != modeAcceptEdits || len(c.acceptEditsCalls) != 1 || !c.acceptEditsCalls[0] {
		t.Fatalf("first Shift+Tab: mode %v, calls %v — want accept edits sent to the agent",
			m.permissionMode(), c.acceptEditsCalls)
	}
	if frame := plainFrame(m); !strings.Contains(frame, "⏵⏵ accept edits (shift+tab)") {
		t.Errorf("accept edits is not on the status bar:\n%s", frame)
	}

	m.fullAccessUnlocked = true
	m = shiftTab(t, m)
	if m.permissionMode() != modeFullAccess {
		t.Fatalf("second Shift+Tab: mode %v, want full access", m.permissionMode())
	}
	if len(c.acceptEditsCalls) != 2 || c.acceptEditsCalls[1] {
		t.Errorf("accept edits must be turned off on the way to full access: %v", c.acceptEditsCalls)
	}
	if len(c.fullAccessCalls) != 1 || !c.fullAccessCalls[0] {
		t.Errorf("full access was not sent to the agent: %v", c.fullAccessCalls)
	}

	m = shiftTab(t, m)
	if m.permissionMode() != modeAsk || c.fullAccessCalls[len(c.fullAccessCalls)-1] {
		t.Errorf("third Shift+Tab: mode %v, full-access calls %v — want back to ask",
			m.permissionMode(), c.fullAccessCalls)
	}
}

// Turning full access ON stays deliberate: before it has been confirmed once,
// Shift+Tab explains it and lands on ask — it never switches it on.
func TestShiftTabNeverTurnsOnFullAccessUnconfirmed(t *testing.T) {
	m, c := liveModel(t)
	m.resize()
	m = shiftTab(t, m) // accept edits
	m = shiftTab(t, m) // would be full access

	if m.fullAccess || len(c.fullAccessCalls) != 0 {
		t.Fatalf("full access was switched on without confirmation (calls %v)", c.fullAccessCalls)
	}
	if m.permissionMode() != modeAsk {
		t.Errorf("mode = %v, want ask", m.permissionMode())
	}
	if !m.fullAccessArmed {
		t.Error("full access must be armed, so /full-access confirm works next")
	}
	last := m.messages[len(m.messages)-1].Content
	if !strings.Contains(last, "/full-access confirm") {
		t.Errorf("the explanation must name how to turn it on: %q", last)
	}

	updated, _ := m.submit("/full-access confirm")
	m = updated.(ChatModel)
	if !m.fullAccess || !m.fullAccessUnlocked {
		t.Fatal("/full-access confirm must turn it on and unlock Shift+Tab")
	}
}

// A launch with full access is already unlocked.
func TestAFullAccessLaunchUnlocksShiftTab(t *testing.T) {
	c := &permissionClient{launchFullAccess: true}
	m := NewChatModel(c, "gaia", "", false)
	if !m.fullAccessUnlocked {
		t.Error("a --full-access launch must unlock Shift+Tab into full access")
	}
}

// Shift+Tab works over a pending prompt, and the prompt's mode line follows it.
func TestShiftTabOverAPromptUpdatesItsModeLine(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m.resize()
	m = feed(t, m, issueTestRunCall())

	m = shiftTab(t, m)
	if m.confirmation == nil || !m.confirmation.Pending() {
		t.Fatal("Shift+Tab must not answer the prompt")
	}
	if frame := plainFrame(m); !strings.Contains(frame, "mode: accept edits") {
		t.Errorf("the prompt still names the old mode:\n%s", frame)
	}
}

// A transport that cannot carry the mode says so and leaves the mode alone.
func TestShiftTabWithoutAControlChannelChangesNothing(t *testing.T) {
	m := NewChatModel(&nullClient{}, "email", "", false)
	m.width, m.height = 100, 30
	m.resize()
	m = shiftTab(t, m)
	if m.permissionMode() != modeAsk {
		t.Errorf("mode changed with no channel to tell the agent: %v", m.permissionMode())
	}
	if last := m.messages[len(m.messages)-1]; last.Role != RoleError {
		t.Errorf("the refusal must be visible, got %+v", last)
	}
}

// An unanswered prompt is delivered as a timeout, never as a "deny" the agent
// would report as the user's decision.
func TestATimedOutPromptIsSentAsATimeout(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, issueTestRunCall())

	c2, cmd := m.confirmation.ResolveTimeout(components.ConfirmationTimeoutMsg{RunID: "run-1"})
	m.confirmation = &c2
	decided := cmd().(components.ConfirmationDecidedMsg)
	updated, deliver := m.Update(decided)
	m = updated.(ChatModel)
	if deliver == nil {
		t.Fatal("the timeout was not delivered to the agent")
	}
	deliver()

	if len(c.decisions) != 1 || c.decisions[0] != client.PermissionTimeout {
		t.Errorf("decisions sent = %v, want [timeout]", c.decisions)
	}
	if got := approvalOf(m); !strings.Contains(got, "timed out") {
		t.Errorf("the step's record must say it timed out, not that it was denied: %q", got)
	}
}

// The mode hint must not push the way out off the bar. The budget once counted
// the bare agent name and "connected", while the bar painted "agent gaia" and
// "waiting for your answer" — so a full hint list clipped to "Ctrl+…".
func TestTheModeHintNeverClipsTheWayOut(t *testing.T) {
	for _, width := range []int{60, 80, 100, 120, 160} {
		for _, pending := range []bool{false, true} {
			m, _ := liveModel(t)
			m.width = width
			m.streaming = pending
			m.followTail = false
			m.acceptEdits = true
			m.resize()
			if pending {
				m = feed(t, m, issueTestRunCall())
			}
			lines := strings.Split(plainFrame(m), "\n")
			bar := strings.TrimRight(lines[len(lines)-1], " ")
			if strings.HasSuffix(bar, "…") || !strings.HasSuffix(bar, "Ctrl+C quit") {
				t.Errorf("width %d pending %t: the bar clipped its last item: %q", width, pending, bar)
			}
		}
	}
}

// fullAccessOnlyClient can toggle full access but has no accept-edits verb —
// the HTTP transport's shape.
type fullAccessOnlyClient struct {
	nullClient
	fullAccessCalls []bool
}

func (c *fullAccessOnlyClient) SetFullAccess(enabled bool) error {
	c.fullAccessCalls = append(c.fullAccessCalls, enabled)
	return nil
}

// A transport without accept edits skips that step instead of dead-ending.
func TestShiftTabSkipsAcceptEditsWhereTheTransportHasNone(t *testing.T) {
	c := &fullAccessOnlyClient{}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30
	m.resize()
	m.fullAccessUnlocked = true

	m = shiftTab(t, m)
	if m.permissionMode() != modeFullAccess || len(c.fullAccessCalls) != 1 {
		t.Fatalf("mode %v, full-access calls %v — want straight to full access",
			m.permissionMode(), c.fullAccessCalls)
	}
	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Errorf("a supported step must not report an error: %q", msg.Content)
		}
	}
}

// Leaving full access lands on ask, even if accept edits was on before it.
func TestLeavingFullAccessAlwaysLandsOnAsk(t *testing.T) {
	m, c := liveModel(t)
	m.resize()
	m = shiftTab(t, m) // accept edits
	updated, _ := m.submit("/full-access")
	m = updated.(ChatModel)
	updated, _ = m.submit("/full-access confirm")
	m = updated.(ChatModel)
	if !m.fullAccess {
		t.Fatal("setup: full access did not turn on")
	}

	m = shiftTab(t, m)
	if m.permissionMode() != modeAsk {
		t.Errorf("mode = %v, want ask", m.permissionMode())
	}
	if last := c.acceptEditsCalls[len(c.acceptEditsCalls)-1]; last {
		t.Errorf("accept edits was left on at the agent: %v", c.acceptEditsCalls)
	}
}
