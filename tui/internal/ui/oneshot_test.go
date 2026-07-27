package ui

import (
	"bytes"
	"context"
	"strings"
	"testing"

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
	res := RunOneShot(context.Background(), &scriptedClient{events: events}, "q", &out, &errW)
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
	res := RunOneShot(context.Background(), c, "q", &out, &errW)

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
	_, _, errW := captureOneShot(t,
		event.CanonicalNeedsConfirmationEvent{
			Type: "needs_confirmation", Action: "send_draft",
			Summary: "Send reply to alice@example.com",
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "skipped"},
	)
	if !strings.Contains(errW, "send_draft") || !strings.Contains(errW, "alice@example.com") {
		t.Errorf("a pending approval must never be swallowed: %q", errW)
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
