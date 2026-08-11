package ui

import (
	"bytes"
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// The remedy the tool author wrote is the one the user needs, and it is CLI
// native: `gaia connectors connect …`, marked "no Agent UI required". Left to
// the model to relay, it came back as "use Settings → Connections in the Agent
// UI" — a GUI a terminal user cannot reach. It has to reach stderr verbatim.
const connectorRemedy = "`gaia connectors connect google --scopes gmail.readonly --grant-agent installed:email`"

func toolErrorPayload() []byte {
	return []byte(`{"status":"error","code":"CONNECTOR_ERROR","error":` +
		`"no forwarded 'google' credential is available to the email sidecar.\n` +
		`Connect and grant it in one command — no Agent UI required:\n` +
		connectorRemedy + `"}`)
}

func TestFailedToolPrintsItsOwnRemedyVerbatim(t *testing.T) {
	_, out, errW := captureOneShot(t,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "email_pre_scan"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "email_pre_scan",
			Render: "email_pre_scan", Data: toolErrorPayload(),
		},
		event.CanonicalFinalEvent{
			Type:   "final",
			Answer: "You will need to reconnect via Settings → Connections in the Agent UI.",
		},
	)

	if !strings.Contains(errW, connectorRemedy) {
		t.Errorf("the tool's own remedy never reached stderr:\n%s", errW)
	}
	if !strings.Contains(errW, "CONNECTOR_ERROR") {
		t.Errorf("stderr does not carry the error code:\n%s", errW)
	}
	if strings.Contains(errW, "email_pre_scan returned") {
		t.Errorf("a failed tool was reported as having returned normally:\n%s", errW)
	}
	// The model's paraphrase is still the answer — this is about the remedy
	// being available, not about censoring the reply.
	if !strings.Contains(out, "Settings") {
		t.Errorf("the answer was dropped from stdout:\n%s", out)
	}
}

// The subprocess vocabulary was worse still: its tool_result fell through to
// "[unhandled event]", so a failing tool said nothing at all.
func TestFailedLegacyToolResultIsRenderedNotUnhandled(t *testing.T) {
	_, _, errW := captureOneShot(t,
		event.ToolStartEvent{Type: "tool_start", Tool: "bash_execute"},
		event.ToolResultEvent{
			Type: "tool_result", Title: "bash_execute", Success: false,
			Summary: "the tool could not run", ResultData: toolErrorPayload(),
		},
		event.AnswerEvent{Type: "answer", Content: "I could not do that."},
	)

	if strings.Contains(errW, "unhandled event") {
		t.Fatalf("a legacy tool result is still unhandled:\n%s", errW)
	}
	if !strings.Contains(errW, connectorRemedy) {
		t.Errorf("the tool's remedy never reached stderr:\n%s", errW)
	}
}

func TestSuccessfulToolResultIsNotReportedAsAFailure(t *testing.T) {
	_, _, errW := captureOneShot(t,
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "list_files", Render: "table",
			Data: []byte(`{"rows":[{"name":"a.txt"}]}`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "done"},
	)

	if strings.Contains(errW, "failed") {
		t.Errorf("a successful tool was reported as failed:\n%s", errW)
	}
	if !strings.Contains(errW, "list_files returned") {
		t.Errorf("the tool result was not reported at all:\n%s", errW)
	}
}

// --debug was documented as "enable debug logging to stderr" and produced
// nothing at all on this path, which is the path it is most needed on.
func TestDebugLoggerReceivesEveryEventWithItsRawPayload(t *testing.T) {
	var out, errW bytes.Buffer
	var debug strings.Builder

	RunOneShot(context.Background(), &scriptedClient{events: []interface{}{
		event.CanonicalStatusEvent{Type: "status", Message: "Scanning inbox"},
		event.CanonicalToolCallEvent{
			Type: "tool_call", Tool: "email_pre_scan", Args: []byte(`{"folder":"INBOX"}`),
		},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "email_pre_scan", Data: toolErrorPayload(),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "done"},
	}}, "triage", &out, &errW, func(format string, args ...any) {
		debug.WriteString(strings.TrimSpace(fmt.Sprintf(format, args...)) + "\n")
	})

	text := debug.String()
	for _, want := range []string{
		"one-shot: query=\"triage\"", // what was asked
		"Scanning inbox",             // status narration
		`{"folder":"INBOX"}`,         // the tool's arguments, raw
		"CONNECTOR_ERROR",            // the raw tool-result payload
		"final:",                     // the terminal event
	} {
		if !strings.Contains(text, want) {
			t.Errorf("--debug output is missing %q:\n%s", want, text)
		}
	}
}

// A nil logger must stay silent — --debug off is the default.
func TestNilDebugLoggerIsSilent(t *testing.T) {
	res, _, _ := captureOneShot(t, event.CanonicalFinalEvent{Type: "final", Answer: "done"})
	if res.ExitCode != 0 {
		t.Fatalf("exit = %d, want 0", res.ExitCode)
	}
}
