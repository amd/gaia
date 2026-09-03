package ui

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// okEnvelope is the shape the email agent documents for its tools:
// `{"ok": true, "data": …}` / `{"ok": false, "error": …}`.
func okEnvelope(ok bool, body string) []byte {
	if ok {
		return []byte(`{"ok": true, "data": {` + body + `}}`)
	}
	return []byte(`{"ok": false, "error": "` + body + `"}`)
}

// `run --help` promises exit 0 on an answer and 1 on an error, and that promise
// is what makes --query usable from a script. A turn whose only tool call came
// back `{"ok": false, …}` and which then wrote an apology used to exit 0, so
// `gaia tui run … && next-step` fired over work that never happened.
func TestFailedToolMakesTheTurnExitNonZero(t *testing.T) {
	res, out, errW := captureOneShot(t,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "pre_scan_inbox",
			Data: okEnvelope(false, "CONNECTOR_ERROR: no forwarded credential"),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "There seems to be a connection issue."},
	)

	if res.ExitCode != 1 {
		t.Fatalf("exit = %d, want 1: the only tool call failed and no work was done", res.ExitCode)
	}
	if len(res.FailedTools) != 1 || res.FailedTools[0] != "pre_scan_inbox" {
		t.Errorf("FailedTools = %v, want [pre_scan_inbox]", res.FailedTools)
	}
	if !strings.Contains(errW, "exit 1") {
		t.Errorf("stderr does not explain the exit code:\n%s", errW)
	}
	// The agent's answer is still the answer; only the exit code judges it.
	if !strings.Contains(out, "connection issue") {
		t.Errorf("the answer was dropped from stdout:\n%s", out)
	}
}

// An agent that retries and succeeds has recovered; that turn is a success.
func TestARetriedToolThatSucceedsClearsTheFailure(t *testing.T) {
	res, _, _ := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "pre_scan_inbox",
			Data: okEnvelope(false, "CONNECTOR_ERROR: token expired"),
		},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "pre_scan_inbox",
			Data: okEnvelope(true, `"unread": 3`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "You have 3 unread."},
	)

	if res.ExitCode != 0 {
		t.Fatalf("exit = %d, want 0: the retry succeeded", res.ExitCode)
	}
	if len(res.FailedTools) != 0 {
		t.Errorf("FailedTools = %v, want none after a successful retry", res.FailedTools)
	}
}

// A DIFFERENT tool succeeding is not evidence that the failed one recovered.
func TestAnotherToolSucceedingDoesNotClearAFailure(t *testing.T) {
	res, _, _ := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "pre_scan_inbox",
			Data: okEnvelope(false, "CONNECTOR_ERROR: no credential"),
		},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "list_folders",
			Data: okEnvelope(true, `"folders": 4`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "I listed your folders instead."},
	)

	if res.ExitCode != 1 {
		t.Fatalf("exit = %d, want 1: pre_scan_inbox never recovered", res.ExitCode)
	}
}

// An unknown result is not recovery either: it is not evidence of anything.
func TestAnUnknownResultDoesNotClearAFailure(t *testing.T) {
	res, _, _ := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "pre_scan_inbox",
			Data: okEnvelope(false, "CONNECTOR_ERROR: no credential"),
		},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "pre_scan_inbox",
			Data: []byte(`{"messages": []}`), // no ok, no status, no error
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "Done."},
	)

	if res.ExitCode != 1 {
		t.Fatalf("exit = %d, want 1: an unstated outcome is not a recovery", res.ExitCode)
	}
}

// A missing `ok` means UNKNOWN, never success: it is a per-agent convention
// today, so an agent that omits it must not be reported green on its behalf —
// and must not be called a failure either. It is said out loud and left there.
//
// Canonical-only by nature: the subprocess vocabulary carries `success` as a
// field on the event itself, so a legacy tool result always states an outcome.
// That is why this case has no mock-agent equivalent.
func TestAnUnstatedOutcomeIsReportedAndDoesNotFailTheTurn(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "search_messages",
			Data: []byte(`{"messages": [{"subject": "hi"}]}`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "I found one message."},
	)

	if res.ExitCode != 0 {
		t.Fatalf("exit = %d, want 0: nothing reported a failure", res.ExitCode)
	}
	if len(res.UndeterminedTools) != 1 || res.UndeterminedTools[0] != "search_messages" {
		t.Errorf("UndeterminedTools = %v, want [search_messages]", res.UndeterminedTools)
	}
	if !strings.Contains(errW, "did not report whether the work succeeded") {
		t.Errorf("an unstated outcome was passed over in silence:\n%s", errW)
	}
}

// The ordinary case must stay quiet: a stated success is not "unverified".
func TestAStatedSuccessIsNotReportedAsUnverified(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "list_folders", Data: okEnvelope(true, `"folders": 4`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "You have 4 folders."},
	)

	if res.ExitCode != 0 {
		t.Fatalf("exit = %d, want 0", res.ExitCode)
	}
	if strings.Contains(errW, "unverified") {
		t.Errorf("a stated success was reported as unverified:\n%s", errW)
	}
}

// A turn with no tools at all is unaffected — most conversation turns.
func TestATurnWithNoToolsStillExitsZero(t *testing.T) {
	res, _, errW := captureOneShot(t,
		event.CanonicalFinalEvent{Type: "final", Answer: "Hello!"},
	)
	if res.ExitCode != 0 {
		t.Fatalf("exit = %d, want 0", res.ExitCode)
	}
	if strings.Contains(errW, "unverified") || strings.Contains(errW, "exit 1") {
		t.Errorf("a plain conversational turn was judged:\n%s", errW)
	}
}

// The email sidecar's real shape: event says success:true, the tool's own
// envelope inside `summary` says ok:false. The envelope wins.
func TestTheSidecarsEncodedEnvelopeDecidesTheExitCode(t *testing.T) {
	res, _, _ := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "triage_inbox",
			Data: []byte(`{"summary":"{\"ok\": false, \"error\": \"CONNECTOR_ERROR: no credential\"}","success":true,"latency_ms":17.7}`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "There was a connection issue."},
	)

	if res.ExitCode != 1 {
		t.Fatalf("exit = %d, want 1: success:true on the event does not outrank ok:false in the envelope", res.ExitCode)
	}
}

func TestTruncatedPartialSuccessExitsZero(t *testing.T) {
	data, err := json.Marshal(map[string]any{
		"summary": `{"succeeded":["a","b"],"failed":[{"error":"already archived"`,
		"success": true,
	})
	if err != nil {
		t.Fatal(err)
	}
	res, _, _ := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "archive_message_batch",
			Data: data,
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "Two messages archived."},
	)
	if res.ExitCode != 0 {
		t.Fatalf("exit = %d, want 0: visible successes prove the partial batch did work", res.ExitCode)
	}
	if len(res.FailedTools) != 0 {
		t.Fatalf("FailedTools = %v, want none", res.FailedTools)
	}
}

// A terminal error still exits 1 regardless of tools — unchanged behaviour.
func TestATerminalErrorStillExitsOne(t *testing.T) {
	res, _, _ := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "list_folders", Data: okEnvelope(true, `"folders": 4`),
		},
		event.CanonicalErrorEvent{Type: "error", Detail: "the model server went away"},
	)
	if res.ExitCode != 1 {
		t.Fatalf("exit = %d, want 1", res.ExitCode)
	}
}
