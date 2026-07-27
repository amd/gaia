package event

import (
	"strings"
	"testing"
)

func TestToolErrorOfReadsTheSidecarErrorShapes(t *testing.T) {
	cases := []struct {
		name     string
		data     string
		wantCode string
		wantMsg  string
	}{
		{
			name:     "status error with a code",
			data:     `{"status":"error","code":"CONNECTOR_ERROR","error":"no forwarded credential"}`,
			wantCode: "CONNECTOR_ERROR",
			wantMsg:  "no forwarded credential",
		},
		{
			name:    "success false with a detail",
			data:    `{"success":false,"detail":"the mailbox refused the request"}`,
			wantMsg: "the mailbox refused the request",
		},
		{
			name:     "error as an object",
			data:     `{"error":{"code":"RATE_LIMITED","message":"try again in 60s"}}`,
			wantCode: "RATE_LIMITED",
			wantMsg:  "try again in 60s",
		},
		{
			name:    "error as a bare string",
			data:    `{"error":"disk is full"}`,
			wantMsg: "disk is full",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, failed := ToolErrorOf(CanonicalToolResultEvent{Tool: "t", Data: []byte(tc.data)})
			if !failed {
				t.Fatalf("payload %s did not read as a failure", tc.data)
			}
			if got.Code != tc.wantCode {
				t.Errorf("code = %q, want %q", got.Code, tc.wantCode)
			}
			if got.Message != tc.wantMsg {
				t.Errorf("message = %q, want %q", got.Message, tc.wantMsg)
			}
		})
	}
}

// A tool that worked must never be reported as failed — a false failure is as
// misleading as the silent success it replaces.
func TestToolErrorOfLeavesASuccessfulResultAlone(t *testing.T) {
	for _, data := range []string{
		`{"status":"ok","rows":3}`,
		`{"success":true,"summary":"Listed 5 files"}`,
		`{"error":""}`,
		`{"messages":[{"subject":"hi"}]}`,
		`[1,2,3]`,
		``,
	} {
		if _, failed := ToolErrorOf(CanonicalToolResultEvent{Tool: "t", Data: []byte(data)}); failed {
			t.Errorf("payload %q was read as a failure", data)
		}
	}
}

// The message keeps its line breaks: a remedy is usually a command on its own
// line, and that is the whole point of surfacing it.
func TestToolErrorKeepsAMultiLineRemedyIntact(t *testing.T) {
	const remedy = "`gaia connectors connect google --scopes gmail.readonly --grant-agent installed:email`"
	data := `{"status":"error","code":"CONNECTOR_ERROR","error":"no credential.\nRun this:\n` + remedy + `"}`

	got, failed := ToolErrorOf(CanonicalToolResultEvent{Tool: "email_pre_scan", Data: []byte(data)})
	if !failed {
		t.Fatal("the payload did not read as a failure")
	}
	if !strings.Contains(got.Message, remedy) {
		t.Errorf("the remedy was lost:\n%s", got.Message)
	}
	if strings.Count(got.Message, "\n") != 2 {
		t.Errorf("line breaks were not preserved: %q", got.Message)
	}
}

// The subprocess vocabulary carries success on the event, not in the payload.
func TestLegacyToolErrorOfReadsTheEventFlag(t *testing.T) {
	got, failed := LegacyToolErrorOf(ToolResultEvent{
		Title: "bash_execute", Success: false, Summary: "command exited 1",
	})
	if !failed {
		t.Fatal("success=false did not read as a failure")
	}
	if got.Message != "command exited 1" {
		t.Errorf("message = %q, want the summary as the fallback", got.Message)
	}

	if _, failed := LegacyToolErrorOf(ToolResultEvent{
		Title: "bash_execute", Success: true, Summary: "Listed 5 files",
	}); failed {
		t.Error("a successful legacy tool result was read as a failure")
	}
}

// The payload wins over the summary when it carries a real message.
func TestLegacyToolErrorPrefersThePayloadMessage(t *testing.T) {
	got, failed := LegacyToolErrorOf(ToolResultEvent{
		Title: "read_email", Success: false, Summary: "the tool could not run",
		ResultData: []byte(`{"status":"error","code":"CONNECTOR_ERROR","error":"no forwarded credential"}`),
	})
	if !failed {
		t.Fatal("the payload did not read as a failure")
	}
	if got.Code != "CONNECTOR_ERROR" || got.Message != "no forwarded credential" {
		t.Errorf("got %+v, want the payload's own code and message", got)
	}
}

// The live shape (#2492 follow-up): the email sidecar string-encodes the tool's
// whole return value into `summary`, reports success:true on the event, and the
// producer truncates that string at 300 chars — sometimes mid-escape, so it is
// no longer valid JSON. Reporting "returned" for it is how the user ended up
// with only the model's paraphrase.
func TestToolErrorReadsAnEncodedSummary(t *testing.T) {
	data := `{"summary":"{\"ok\": false, \"error\": \"CONNECTOR_ERROR: no forwarded 'google' credential\"}","success":true,"latency_ms":17.7}`

	got, failed := ToolErrorOf(CanonicalToolResultEvent{Tool: "triage_inbox", Data: []byte(data)})
	if !failed {
		t.Fatal("a tool whose encoded result says ok:false was read as a success")
	}
	if !strings.Contains(got.Message, "CONNECTOR_ERROR") {
		t.Errorf("the tool's own message was lost: %q", got.Message)
	}
}

func TestToolErrorSurfacesATruncatedEncodedSummary(t *testing.T) {
	// Cut mid-escape, exactly as the 300-char cap does.
	truncated := `{\"ok\": false, \"error\": \"CONNECTOR_ERROR: no forwarded 'google' credential. Connect and grant it in one command —`
	data := `{"summary":"` + truncated + `","success":true}`

	got, failed := ToolErrorOf(CanonicalToolResultEvent{Tool: "triage_inbox", Data: []byte(data)})
	if !failed {
		t.Fatal("a truncated failure payload was read as a success")
	}
	if !strings.Contains(got.Message, "CONNECTOR_ERROR") {
		t.Errorf("what did arrive was dropped: %q", got.Message)
	}
	if !strings.Contains(got.Message, TruncatedNote) {
		t.Errorf("a half-sentence was presented as the whole message: %q", got.Message)
	}
}

// An encoded summary that reports success must stay a success.
func TestToolErrorLeavesAHealthyEncodedSummaryAlone(t *testing.T) {
	data := `{"summary":"{\"ok\": true, \"count\": 3}","success":true}`
	if _, failed := ToolErrorOf(CanonicalToolResultEvent{Tool: "triage_inbox", Data: []byte(data)}); failed {
		t.Error("a healthy encoded summary was read as a failure")
	}
	// Prose summaries are the common case and must never be scanned for JSON.
	plain := `{"summary":"Listed 5 files and 1 directory","success":true}`
	if _, failed := ToolErrorOf(CanonicalToolResultEvent{Tool: "ls", Data: []byte(plain)}); failed {
		t.Error("a prose summary was read as a failure")
	}
}
