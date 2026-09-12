package client

import (
	"context"
	"net/http"
	"strings"
	"testing"
)

// The live permission seam over the daemon transport
// (docs/plans/daemon-convergence.mdx §3.4).
//
// Before this the flagship could not run a gated tool over the daemon at all:
// SSEClient implemented neither interface, so the chat view could only record
// intent and reported "this agent connection cannot deliver a permission
// decision". These pin the seam, and pin that the wire words match what the
// agent actually parses — it fails closed on a value it does not know, so a
// mismatch would silently turn an approval into a denial.

// liveRun starts a turn and parks the server mid-stream, so the client has a
// run to answer against. Returns the client and a func that releases the
// stream.
func liveRun(t *testing.T, f *fakeRelay) (*SSEClient, func()) {
	t.Helper()
	started := make(chan struct{})
	release := make(chan struct{})
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"thinking"}`)
		flush()
		close(started)
		<-release
	}

	c := f.client(t)
	ch, err := c.Send(context.Background(), "do a gated thing")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	if _, ok := <-ch; !ok {
		t.Fatal("expected the status event before answering")
	}
	<-started
	return c, func() { close(release); c.Close() }
}

func TestSSEClientImplementsTheLivePermissionInterfaces(t *testing.T) {
	var c any = &SSEClient{}
	if _, ok := c.(ToolPermissionResponder); !ok {
		t.Error("SSEClient must deliver permission decisions, or gated tools never run")
	}
	if _, ok := c.(PermissionBypasser); !ok {
		t.Error("SSEClient must carry bypass, or /bypass is subprocess-only")
	}
}

func TestTheWireWordsAreTheOnesTheAgentParses(t *testing.T) {
	// Contract with gaia_agent.stdio's DECISION_* constants and the server's
	// _TOOL_DECISIONS. The agent fails closed on anything else, so a drift here
	// turns an approval into a silent denial.
	for decision, want := range map[PermissionDecision]string{
		PermissionAllow:  "allow",
		PermissionAlways: "always",
		PermissionDeny:   "deny",
	} {
		got, err := decisionWire(decision)
		if err != nil {
			t.Errorf("decisionWire(%v): %v", decision, err)
			continue
		}
		if got != want {
			t.Errorf("decisionWire(%v) = %q, want %q", decision, got, want)
		}
	}
}

func TestAnUnknownDecisionIsRefusedRatherThanSentAsSomethingElse(t *testing.T) {
	if _, err := decisionWire(PermissionDecision("sure-why-not")); err == nil {
		t.Error("an unmapped decision must not be sent to the agent")
	}
}

func TestADecisionNamesTheRunAndThePromptItAnswers(t *testing.T) {
	f := newFakeRelay(t)
	c, done := liveRun(t, f)
	defer done()

	if err := c.RespondToolPermission("c7", PermissionAllow); err != nil {
		t.Fatalf("RespondToolPermission: %v", err)
	}

	f.mu.Lock()
	got := append([]decisionCall(nil), f.decisions...)
	f.mu.Unlock()

	if len(got) != 1 {
		t.Fatalf("decisions = %+v, want exactly one", got)
	}
	if got[0].decision != "allow" {
		t.Errorf("decision = %q, want allow", got[0].decision)
	}
	// Without confirm_id a late answer resolves whichever prompt replaced the
	// one it was typed against.
	if got[0].confirmID != "c7" {
		t.Errorf("confirm_id = %q, want c7", got[0].confirmID)
	}
	if got[0].runID != f.lastQuery().RunID {
		t.Errorf("runID = %q, want the live run %q", got[0].runID, f.lastQuery().RunID)
	}
}

func TestEveryDecisionReachesTheAgentVerbatim(t *testing.T) {
	for _, tc := range []struct {
		decision PermissionDecision
		want     string
	}{
		{PermissionAllow, "allow"},
		{PermissionAlways, "always"},
		{PermissionDeny, "deny"},
	} {
		f := newFakeRelay(t)
		c, done := liveRun(t, f)
		if err := c.RespondToolPermission("c1", tc.decision); err != nil {
			t.Fatalf("RespondToolPermission(%v): %v", tc.decision, err)
		}
		f.mu.Lock()
		got := append([]decisionCall(nil), f.decisions...)
		f.mu.Unlock()
		if len(got) != 1 || got[0].decision != tc.want {
			t.Errorf("decision = %+v, want %q", got, tc.want)
		}
		done()
	}
}

func TestAnsweringWithNoLiveRunSaysNothingWasSent(t *testing.T) {
	f := newFakeRelay(t)
	c := f.client(t)
	defer c.Close()

	err := c.RespondToolPermission("c1", PermissionAllow)
	if err == nil {
		t.Fatal("answering with no run must fail rather than appear to work")
	}
	if !strings.Contains(err.Error(), "Nothing was sent") {
		t.Errorf("the user must learn nothing happened, got: %v", err)
	}
}

func TestAPromptThatAlreadyResolvedIsReportedNotSwallowed(t *testing.T) {
	f := newFakeRelay(t)
	f.decisionStatus = http.StatusConflict
	c, done := liveRun(t, f)
	defer done()

	err := c.RespondToolPermission("c1", PermissionAllow)
	if err == nil {
		t.Fatal("a 409 must surface: the agent is no longer waiting")
	}
	if !strings.Contains(err.Error(), "already answered") {
		t.Errorf("the reason must be actionable, got: %v", err)
	}
}

func TestBypassIsScopedToTheConversationNotTheRun(t *testing.T) {
	f := newFakeRelay(t)
	c, done := liveRun(t, f)
	defer done()

	if err := c.SetBypassPermissions(true); err != nil {
		t.Fatalf("SetBypassPermissions: %v", err)
	}

	f.mu.Lock()
	got := append([]bypassCall(nil), f.bypasses...)
	f.mu.Unlock()

	if len(got) != 1 {
		t.Fatalf("bypasses = %+v, want exactly one", got)
	}
	if !got[0].enabled {
		t.Error("enabled did not reach the agent")
	}
	// Bypass outliving a turn is the entire point of it.
	if got[0].sessionID == "" {
		t.Error("bypass must name a session, not a run")
	}
}

func TestBypassBeforeTheConversationStartsExplainsItself(t *testing.T) {
	f := newFakeRelay(t)
	f.bypassStatus = http.StatusNotFound
	c, done := liveRun(t, f)
	defer done()

	err := c.SetBypassPermissions(true)
	if err == nil {
		t.Fatal("a 404 must surface rather than looking like success")
	}
	if !strings.Contains(err.Error(), "Send a message first") {
		t.Errorf("the user just pressed a key; say what to do, got: %v", err)
	}
}
