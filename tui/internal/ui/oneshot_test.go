package ui

import (
	"bytes"
	"context"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
)

// scriptedClient replays a fixed event sequence, or fails Send outright.
type scriptedClient struct {
	events  []interface{}
	sendErr error
}

func (s *scriptedClient) Send(context.Context, string) (<-chan interface{}, error) {
	if s.sendErr != nil {
		return nil, s.sendErr
	}
	ch := make(chan interface{}, len(s.events))
	for _, e := range s.events {
		ch <- e
	}
	close(ch)
	return ch, nil
}

func (s *scriptedClient) Close() error { return nil }

func captureOneShot(t *testing.T, events ...interface{}) (OneShotResult, string, string) {
	t.Helper()
	var out, errW bytes.Buffer
	res := RunOneShot(context.Background(), &scriptedClient{events: events}, "q", &out, &errW, nil)
	return res, out.String(), errW.String()
}

// The answer goes to stdout and nothing else does, so `--query X > file` captures
// exactly the answer.
func TestRunOneShotSeparatesAnswerFromProgress(t *testing.T) {
	res, out, errW := captureOneShot(t,
		event.CanonicalStatusEvent{Type: "status", Message: "Scanning inbox"},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "pre_scan_inbox", Render: "email_pre_scan"},
		event.CanonicalFinalEvent{Type: "final", Answer: "3 urgent emails", Usage: []byte(`{"steps":2,"tools_used":1}`)},
	)

	if res.ExitCode != 0 {
		t.Errorf("exit code = %d, want 0", res.ExitCode)
	}
	if res.TerminalType != event.CanonicalTypeFinal {
		t.Errorf("terminal = %q", res.TerminalType)
	}
	if strings.TrimSpace(out) != "3 urgent emails" {
		t.Errorf("stdout must carry only the answer, got %q", out)
	}
	for _, want := range []string{"Scanning inbox", "pre_scan_inbox", "email_pre_scan", "2 steps"} {
		if !strings.Contains(errW, want) {
			t.Errorf("stderr missing %q: %s", want, errW)
		}
	}
}

func TestRunOneShotStreamsTokensWithoutDuplicatingTheAnswer(t *testing.T) {
	res, out, _ := captureOneShot(t,
		event.CanonicalTokenEvent{Type: "token", Delta: "You have "},
		event.CanonicalTokenEvent{Type: "token", Delta: "3 urgent emails"},
		event.CanonicalFinalEvent{Type: "final", Answer: "You have 3 urgent emails"},
	)

	if got := strings.TrimSpace(out); got != "You have 3 urgent emails" {
		t.Errorf("the answer must not be printed twice, got %q", got)
	}
	if res.Answer != "You have 3 urgent emails" {
		t.Errorf("answer = %q", res.Answer)
	}
}

func TestRunOneShotFinalWithEmptyAnswerKeepsStreamedTokens(t *testing.T) {
	res, out, _ := captureOneShot(t,
		event.CanonicalTokenEvent{Type: "token", Delta: "streamed only"},
		event.CanonicalFinalEvent{Type: "final", Answer: ""},
	)
	if res.Answer != "streamed only" {
		t.Errorf("answer = %q, want the streamed text", res.Answer)
	}
	if !strings.Contains(out, "streamed only") {
		t.Errorf("stdout = %q", out)
	}
}

func TestRunOneShotTerminalErrorExitsNonZero(t *testing.T) {
	res, out, errW := captureOneShot(t,
		event.CanonicalErrorEvent{Type: "error", Detail: "Lemonade is not reachable — run `gaia init`", Status: 503},
	)
	if res.ExitCode != 1 {
		t.Errorf("exit code = %d, want 1", res.ExitCode)
	}
	if out != "" {
		t.Errorf("a failed turn must not write to stdout, got %q", out)
	}
	if !strings.Contains(errW, "gaia init") {
		t.Errorf("the actionable detail must be surfaced verbatim: %q", errW)
	}
}

// A stream that ends with no terminal event is a failure, not an empty success.
func TestRunOneShotNoTerminalEventExitsNonZero(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalStatusEvent{Type: "status", Message: "working"},
	)
	if res.ExitCode != 1 {
		t.Errorf("exit code = %d, want 1", res.ExitCode)
	}
	if !strings.Contains(errW, "without a terminal") {
		t.Errorf("stderr must explain the missing terminal event: %q", errW)
	}
}

func TestRunOneShotSendFailureIsReported(t *testing.T) {
	var out, errW bytes.Buffer
	c := &scriptedClient{sendErr: errFake("no daemon is registered; start one with `gaia daemon start`")}
	res := RunOneShot(context.Background(), c, "q", &out, &errW, nil)

	if res.ExitCode != 1 {
		t.Errorf("exit code = %d, want 1", res.ExitCode)
	}
	if !strings.Contains(errW.String(), "gaia daemon start") {
		t.Errorf("the transport error must be surfaced: %q", errW.String())
	}
}

// Unknown and unreadable events stay visible (contract §7) and do not end the run.
func TestRunOneShotSurfacesUnsupportedAndMalformed(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalUnsupportedEvent{EventType: "needs_input"},
		event.CanonicalMalformedEvent{Reason: "not valid JSON"},
		event.CanonicalFinalEvent{Type: "final", Answer: "done"},
	)
	if res.ExitCode != 0 {
		t.Errorf("neither event should fail the run, exit = %d", res.ExitCode)
	}
	if !strings.Contains(errW, "needs_input") || !strings.Contains(errW, "not valid JSON") {
		t.Errorf("both must be visible: %q", errW)
	}
}

func TestRunOneShotSurfacesNeedsConfirmation(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{
			Type: "needs_confirmation", Action: "send_draft",
			Summary: "Send reply to alice@example.com",
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "skipped"},
	)
	if !strings.Contains(errW, "send_draft") || !strings.Contains(errW, "alice@example.com") {
		t.Errorf("a pending approval must never be swallowed: %q", errW)
	}
	// The fixture says it: the answer is "skipped". Reporting that as success
	// let `gaia tui run … && next-step` fire over an action a safety gate
	// deliberately withheld.
	if res.ExitCode == 0 {
		t.Errorf("a withheld action exited 0; stderr was:\n%s", errW)
	}
}

// The same renderer also handles a subprocess agent's legacy vocabulary.
func TestRunOneShotHandlesLegacyEvents(t *testing.T) {
	res, out, _ := captureOneShot(t,
		event.ToolStartEvent{Type: "tool_start", Tool: "bash"},
		event.AnswerEvent{Type: "answer", Content: "legacy answer"},
	)
	if res.ExitCode != 0 {
		t.Errorf("exit code = %d, want 0", res.ExitCode)
	}
	if strings.TrimSpace(out) != "legacy answer" {
		t.Errorf("stdout = %q", out)
	}
}

type errFake string

func (e errFake) Error() string { return string(e) }

// stallingClient accepts the query and then never says anything: the channel is
// never written to and never closed. This is the failure a readiness check
// cannot catch — the dependency was reachable, it just stopped answering — so
// the caller's deadline is the only thing standing between it and a hang.
type stallingClient struct {
	// first is emitted before the silence, so the partial-line handling is
	// exercised too.
	first interface{}
}

func (s *stallingClient) Send(context.Context, string) (<-chan interface{}, error) {
	ch := make(chan interface{}, 1)
	if s.first != nil {
		ch <- s.first
	}
	return ch, nil
}

func (s *stallingClient) Close() error { return nil }

// runOneShotAsync runs a turn off the test goroutine so a hang fails the test
// instead of wedging the suite.
func runOneShotAsync(
	t *testing.T,
	ctx context.Context,
	c client.AgentClient,
	out, errW *bytes.Buffer,
) OneShotResult {
	t.Helper()
	done := make(chan OneShotResult, 1)
	go func() { done <- RunOneShot(ctx, c, "triage my inbox", out, errW, nil) }()
	select {
	case res := <-done:
		return res
	case <-time.After(15 * time.Second):
		t.Fatal("RunOneShot never returned after its context was done — this is issue #2483")
		return OneShotResult{}
	}
}

// The bug: an agent that accepts the query and then goes quiet used to hang
// forever, with nothing on either stream.
func TestRunOneShotAbandonsAStalledStreamAtTheDeadline(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()

	var out, errW bytes.Buffer
	c := &stallingClient{first: event.CanonicalTokenEvent{Type: "token", Delta: "Look"}}
	res := runOneShotAsync(t, ctx, c, &out, &errW)

	if res.ExitCode != 1 {
		t.Errorf("exit code = %d, want 1 — an abandoned turn is a failure", res.ExitCode)
	}
	if res.TerminalType != event.CanonicalTypeError {
		t.Errorf("terminal = %q, want error", res.TerminalType)
	}
	// The partial answer keeps its own line; the diagnosis belongs on stderr.
	if out.String() != "Look\n" {
		t.Errorf("stdout = %q, want the streamed text terminated by a newline", out.String())
	}
	for _, want := range []string{"gave up", "triage my inbox", "gaia daemon logs"} {
		if !strings.Contains(errW.String(), want) {
			t.Errorf("stderr must say what happened and where to look, missing %q:\n%s", want, errW.String())
		}
	}
}

// A cancelled parent context is reported as a cancellation, not as a deadline —
// the two send the reader to different places.
func TestRunOneShotReportsACancelledTurnDistinctly(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	var out, errW bytes.Buffer
	res := runOneShotAsync(t, ctx, &stallingClient{}, &out, &errW)

	if res.ExitCode != 1 {
		t.Errorf("exit code = %d, want 1", res.ExitCode)
	}
	if !strings.Contains(errW.String(), "cancelled") {
		t.Errorf("stderr = %q, want it to name the cancellation", errW.String())
	}
	if strings.Contains(errW.String(), "gave up") {
		t.Errorf("a cancellation must not be reported as a deadline: %q", errW.String())
	}
	if out.String() != "" {
		t.Errorf("stdout = %q, want empty", out.String())
	}
}
