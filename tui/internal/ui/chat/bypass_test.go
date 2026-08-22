// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// permissionClient is a transport that CAN answer a live permission prompt and
// toggle bypass — what the flagship agent's stdio control channel provides.
type permissionClient struct {
	nullClient
	decisions    []client.PermissionDecision
	confirmIDs   []string
	bypassCalls  []bool
	launchBypass bool
	err          error
}

func (c *permissionClient) RespondToolPermission(confirmID string, d client.PermissionDecision) error {
	if c.err != nil {
		return c.err
	}
	c.decisions = append(c.decisions, d)
	c.confirmIDs = append(c.confirmIDs, confirmID)
	return nil
}

func (c *permissionClient) SetBypassPermissions(enabled bool) error {
	if c.err != nil {
		return c.err
	}
	c.bypassCalls = append(c.bypassCalls, enabled)
	return nil
}

func (c *permissionClient) BypassAtLaunch() bool { return c.launchBypass }

func liveModel(t *testing.T) (ChatModel, *permissionClient) {
	t.Helper()
	c := &permissionClient{}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30
	return m, c
}

func gatedShellCall() event.CanonicalNeedsConfirmationEvent {
	return event.CanonicalNeedsConfirmationEvent{
		Type: "needs_confirmation", RunID: "run-1", Action: "run_shell_command",
		Summary: `Run 'run_shell_command' with command="pwd"?`, ConfirmID: "cid-1",
		AlwaysScope: "pwd",
	}
}

// --- the modal, end to end -------------------------------------------------

// The original defect in one test: the prompt named only the tool, and nothing
// on screen told the user they could answer it.
func TestGatedCallShowsTheCommandAndAllThreeChoices(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	view := m.View()
	for _, want := range []string{
		`command="pwd"`,
		"y run once",
		"a allow `pwd` this session",
		"n/esc deny",
	} {
		if !strings.Contains(view, want) {
			t.Errorf("the modal does not offer %q:\n%s", want, view)
		}
	}
}

func TestEachDecisionReachesTheAgent(t *testing.T) {
	for _, tc := range []struct {
		key  string
		want client.PermissionDecision
	}{
		{"y", client.PermissionAllow},
		{"a", client.PermissionAlways},
		{"n", client.PermissionDeny},
	} {
		t.Run(string(tc.want), func(t *testing.T) {
			m, c := liveModel(t)
			m.streaming = true
			m = feed(t, m, gatedShellCall())

			updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(tc.key)})
			m = updated.(ChatModel)
			if cmd == nil {
				t.Fatalf("%q produced no decision", tc.key)
			}
			decided := cmd().(components.ConfirmationDecidedMsg)
			updated2, deliver := m.Update(decided)
			m = updated2.(ChatModel)
			if deliver == nil {
				t.Fatal("a live decision must be delivered, not just recorded")
			}
			deliver()

			if len(c.decisions) != 1 || c.decisions[0] != tc.want {
				t.Errorf("delivered %v, want [%v]", c.decisions, tc.want)
			}
			if len(c.confirmIDs) != 1 || c.confirmIDs[0] != "cid-1" {
				t.Errorf("the prompt id was lost: %v", c.confirmIDs)
			}
			if m.confirmation != nil {
				t.Error("a resolved confirmation must clear")
			}
		})
	}
}

// Esc still means deny here, and the denial is actually sent — the agent is
// blocked waiting for it, so "recorded locally" would hang the run.
func TestEscDeniesAndTellsTheAgent(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m.cancelFn = func() { t.Error("Esc on a pending confirmation must not cancel the turn") }
	m = feed(t, m, gatedShellCall())

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	decided := cmd().(components.ConfirmationDecidedMsg)
	if decided.Approved {
		t.Fatal("Esc must never approve")
	}
	_, deliver := m.Update(decided)
	deliver()
	if len(c.decisions) != 1 || c.decisions[0] != client.PermissionDeny {
		t.Errorf("Esc did not deliver a denial: %v", c.decisions)
	}
}

// A live prompt must not arm the auto-deny at all. This is the regression that
// made every gated tool unusable: the user paused to read, and 30s later the
// call had been refused on their behalf.
func TestALivePromptDoesNotExpire(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, cmd := m.Update(components.ConfirmationTimeoutMsg{RunID: "run-1"})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("a timeout tick must not resolve a live confirmation")
	}
	if m.confirmation == nil || !m.confirmation.Pending() {
		t.Fatal("the prompt must still be waiting for the user")
	}
}

// A failed delivery strands the agent, so it is surfaced rather than swallowed.
func TestAFailedDecisionDeliveryIsSurfaced(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())
	c.err = errPermissionChannelGone

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	m = updated.(ChatModel)
	decided := cmd().(components.ConfirmationDecidedMsg)
	_, deliver := m.Update(decided)
	result := deliver()

	updated2, _ := m.Update(result)
	m = updated2.(ChatModel)
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "channel is gone") {
		t.Errorf("a delivery failure was not surfaced: %+v", last)
	}
}

// --- bypass mode -----------------------------------------------------------

func TestBypassIsOffOnAFreshModel(t *testing.T) {
	m, _ := liveModel(t)
	if m.bypassPermissions {
		t.Fatal("bypass must be off on a fresh launch")
	}
	if strings.Contains(m.View(), "BYPASS") {
		t.Error("no banner may show when bypass is off")
	}
}

// Turning it ON takes two deliberate steps; the first one only explains.
func TestTurningBypassOnIsDeliberate(t *testing.T) {
	m, c := liveModel(t)

	updated, _ := m.submit("/bypass")
	m = updated.(ChatModel)
	if m.bypassPermissions {
		t.Fatal("/bypass alone must not enable anything")
	}
	if len(c.bypassCalls) != 0 {
		t.Errorf("nothing should have reached the agent yet: %v", c.bypassCalls)
	}
	explained := m.messages[len(m.messages)-1].Content
	for _, want := range []string{"every tool", "/bypass confirm"} {
		if !strings.Contains(explained, want) {
			t.Errorf("the warning must contain %q, got: %q", want, explained)
		}
	}

	updated, _ = m.submit("/bypass confirm")
	m = updated.(ChatModel)
	if !m.bypassPermissions {
		t.Fatal("/bypass confirm must enable bypass")
	}
	if len(c.bypassCalls) != 1 || !c.bypassCalls[0] {
		t.Errorf("the agent was not told to bypass: %v", c.bypassCalls)
	}
}

// Confirming out of the blue must not work — the warning is the point.
func TestBypassConfirmWithoutTheWarningDoesNothing(t *testing.T) {
	m, c := liveModel(t)
	updated, _ := m.submit("/bypass confirm")
	m = updated.(ChatModel)
	if m.bypassPermissions || len(c.bypassCalls) != 0 {
		t.Error("an unprompted /bypass confirm must not enable bypass")
	}
}

// While it is on, every frame says so — and the banner lives outside the
// viewport, so scrolling cannot take it away.
func TestBypassBannerIsOnEveryFrame(t *testing.T) {
	m, _ := liveModel(t)
	updated, _ := m.submit("/bypass")
	m = updated.(ChatModel)
	updated, _ = m.submit("/bypass confirm")
	m = updated.(ChatModel)

	view := m.View()
	if !strings.Contains(view, "BYPASS PERMISSIONS") {
		t.Fatalf("no bypass banner:\n%s", view)
	}
	if !strings.Contains(view, "/bypass off") {
		t.Errorf("the banner must say how to stop:\n%s", view)
	}

	// Scrolled away from the tail, the banner is still there.
	m.followTail = false
	m.viewport.GotoTop()
	if !strings.Contains(m.View(), "BYPASS PERMISSIONS") {
		t.Error("the banner must not be scrollable out of view")
	}

	// And the always-drawn status row carries it too.
	if !strings.Contains(fitHints(m.statusHints(), m.hintBudget()), "/bypass off") {
		t.Error("the status bar must carry the way out of bypass")
	}
}

func TestTurningBypassOffIsOneStep(t *testing.T) {
	m, c := liveModel(t)
	m.bypassPermissions = true

	updated, _ := m.submit("/bypass")
	m = updated.(ChatModel)
	if m.bypassPermissions {
		t.Fatal("/bypass while on must turn it off immediately")
	}
	if len(c.bypassCalls) != 1 || c.bypassCalls[0] {
		t.Errorf("the agent was not told to stop bypassing: %v", c.bypassCalls)
	}
	if strings.Contains(m.View(), "BYPASS PERMISSIONS —") {
		t.Error("the banner must go the instant bypass is off")
	}
}

// A transport that cannot carry the toggle must not leave a banner claiming an
// autonomy the agent is not actually in.
func TestBypassIsNotClaimedWhenTheAgentCannotBeTold(t *testing.T) {
	m, c := liveModel(t)
	c.err = errPermissionChannelGone

	updated, _ := m.submit("/bypass")
	m = updated.(ChatModel)
	updated, _ = m.submit("/bypass confirm")
	m = updated.(ChatModel)

	if m.bypassPermissions {
		t.Fatal("bypass must not be claimed locally when the agent was never told")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError {
		t.Errorf("the failure must be surfaced: %+v", last)
	}
}

// A transport with no control channel at all says so rather than pretending.
func TestBypassOnATransportWithoutAChannel(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.submit("/bypass")
	m = updated.(ChatModel)
	updated, _ = m.submit("/bypass confirm")
	m = updated.(ChatModel)

	if m.bypassPermissions {
		t.Fatal("a transport with no control channel cannot enter bypass")
	}
}

// The launch flag shows the banner from the first frame, not only after a
// manual toggle.
func TestLaunchFlagShowsTheBannerImmediately(t *testing.T) {
	c := &permissionClient{launchBypass: true}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30

	if !m.bypassPermissions {
		t.Fatal("--bypass-permissions must be reflected at launch")
	}
	if !strings.Contains(m.View(), "BYPASS PERMISSIONS") {
		t.Error("the banner must be up from the very first frame")
	}
}

// The /bypass forms never reach the agent as a question.
func TestBypassCommandsAreNeverSentAsQueries(t *testing.T) {
	for _, cmd := range []string{"/bypass", "/bypass on", "/bypass off", "/bypass confirm"} {
		if !isBypassCommand(cmd) {
			t.Errorf("%q must be recognised as a local command", cmd)
		}
	}
	if isBypassCommand("what does /bypass do") {
		t.Error("a question about bypass is still a question")
	}
}

// A call the agent would not scope offers only y/n in the live chat view. This
// is the guard against one keypress on a single `gh` command handing over the
// shell for the session: the agent decides what is grantable, and a bare
// `bash -c ...` is not.
func TestChatDoesNotOfferAlwaysWithoutAScope(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	evt := gatedShellCall()
	evt.AlwaysScope = ""
	m = feed(t, m, evt)

	if strings.Contains(m.View(), " a allow ") {
		t.Errorf("always offered for an unscopable call:\n%s", m.View())
	}
	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("'a' must not resolve a confirmation the agent would not scope")
	}
	if m.confirmation == nil || !m.confirmation.Pending() {
		t.Fatal("the prompt must still be waiting")
	}
	if len(c.decisions) != 0 {
		t.Errorf("nothing should have been delivered: %v", c.decisions)
	}
}

// The transcript records the SCOPE that was granted, not the tool - so reading
// back the session says what was actually handed over.
func TestTranscriptRecordsTheGrantedScope(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	m = updated.(ChatModel)
	decided := cmd().(components.ConfirmationDecidedMsg)
	updated2, _ := m.Update(decided)
	m = updated2.(ChatModel)

	last := m.messages[len(m.messages)-1].Content
	if !strings.Contains(last, "'pwd'") {
		t.Errorf("the record must name the granted scope, got: %q", last)
	}
	if strings.Contains(last, "run_shell_command' will not ask") {
		t.Errorf("the record must not read as a tool-wide grant: %q", last)
	}
}

// errPermissionChannelGone stands in for a dead control channel.
var errPermissionChannelGone = errTest("the permission channel is gone")

type errTest string

func (e errTest) Error() string { return string(e) }
