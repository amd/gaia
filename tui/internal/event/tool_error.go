package event

import (
	"encoding/json"
	"strings"
)

// ToolError is the failure a tool result carries in its payload.
//
// It exists because a tool's own error message is the most actionable text in a
// run — the email sidecar's CONNECTOR_ERROR ends with the exact
// `gaia connectors connect …` line that fixes it — and routing it through the
// model's summary loses that. Surfacing it verbatim keeps the tool author's
// remedy intact.
type ToolError struct {
	// Code is the machine-readable class, e.g. "CONNECTOR_ERROR". Empty when
	// the payload carries none.
	Code string
	// Message is the tool's own text, newlines and all. Never reflowed.
	Message string
}

// ToolOutcome is what a tool result PROVES about its call.
//
// Unknown is a real answer, never a pass: `ok` is an agent convention today
// (documented for the email agent as `{"ok": true, "data": …}` /
// `{"ok": false, "error": …}`), not a contract guarantee, so an agent that
// sends no outcome at all must not be reported green on its behalf.
type ToolOutcome int

const (
	// ToolOutcomeUnknown — the payload says nothing either way.
	ToolOutcomeUnknown ToolOutcome = iota
	// ToolOutcomeSucceeded — the payload says so explicitly.
	ToolOutcomeSucceeded
	// ToolOutcomeFailed — the payload carries an error.
	ToolOutcomeFailed
)

func (o ToolOutcome) String() string {
	switch o {
	case ToolOutcomeSucceeded:
		return "succeeded"
	case ToolOutcomeFailed:
		return "failed"
	default:
		return "unknown"
	}
}

// ToolOutcomeOf classifies a canonical tool result, returning the error when
// there is one. Failure is checked first: the email sidecar sends `success:
// true` on the event while the tool's own envelope says `ok: false`, so a
// positive field proves nothing until the error checks have run.
func ToolOutcomeOf(e CanonicalToolResultEvent) (ToolOutcome, ToolError) {
	if te, failed := ToolErrorOf(e); failed {
		return ToolOutcomeFailed, te
	}
	if toolSucceeded(e.Data) {
		return ToolOutcomeSucceeded, ToolError{}
	}
	return ToolOutcomeUnknown, ToolError{}
}

// LegacyToolOutcomeOf classifies a subprocess-vocabulary tool result. That
// event carries `success` as a required field, so the answer is never unknown.
func LegacyToolOutcomeOf(e ToolResultEvent) (ToolOutcome, ToolError) {
	if te, failed := LegacyToolErrorOf(e); failed {
		return ToolOutcomeFailed, te
	}
	return ToolOutcomeSucceeded, ToolError{}
}

// toolSucceeded reports an EXPLICIT positive: `ok: true` or `status: "ok"`,
// at the top level or inside a string-encoded envelope. Silence is not a yes.
func toolSucceeded(data json.RawMessage) bool {
	if len(data) == 0 {
		return false
	}
	var payload map[string]json.RawMessage
	if json.Unmarshal(data, &payload) != nil {
		return false
	}
	if raw, ok := payload["ok"]; ok {
		var b bool
		if json.Unmarshal(raw, &b) == nil && b {
			return true
		}
	}
	if s, ok := stringField(payload, "status"); ok &&
		(strings.EqualFold(s, "ok") || strings.EqualFold(s, "success")) {
		return true
	}
	if summary, ok := stringField(payload, "summary"); ok {
		trimmed := strings.TrimSpace(summary)
		if strings.HasPrefix(trimmed, "{") && json.Valid([]byte(trimmed)) {
			return toolSucceeded(json.RawMessage(trimmed))
		}
	}
	return false
}

// ToolErrorOf reports the error inside a canonical tool_result payload.
//
// The canonical event has no `ok` field (#2495), so failure is read from the
// payload the sidecar sent: `status: "error"`, `success: false`, or a non-empty
// `error`. Anything else is a result, not a failure.
func ToolErrorOf(e CanonicalToolResultEvent) (ToolError, bool) {
	return toolErrorFrom(e.Data, false)
}

// LegacyToolErrorOf does the same for the subprocess vocabulary's tool_result,
// where `success` is a field on the event rather than inside the payload.
//
// An emitter that omits `success` is read as a failure: the field is part of
// that contract (gaia.ui.sse_handler always sets it), and a wrong "failed" is
// visible where a wrong "returned" hides the only actionable text in the run.
func LegacyToolErrorOf(e ToolResultEvent) (ToolError, bool) {
	te, failed := toolErrorFrom(e.ResultData, !e.Success)
	if failed && te.Message == "" {
		te.Message = e.Summary
	}
	return te, failed
}

// toolErrorFrom reads the shared payload conventions. failedHint carries an
// out-of-band failure signal (the legacy event's success flag).
func toolErrorFrom(data json.RawMessage, failedHint bool) (ToolError, bool) {
	failed := failedHint
	var te ToolError
	var summaryTruncated bool

	var payload map[string]json.RawMessage
	if len(data) > 0 {
		if err := json.Unmarshal(data, &payload); err != nil {
			// A payload that is not an object carries no error fields to read.
			return ToolError{}, failed
		}
	}
	if raw, ok := payload["summary_truncated"]; ok {
		_ = json.Unmarshal(raw, &summaryTruncated)
	}

	if s, ok := stringField(payload, "status"); ok && strings.EqualFold(s, "error") {
		failed = true
	}
	// `success` on the event and `ok` inside a GAIA tool's return value. Only
	// FALSE is read: the email sidecar sends success:true on a tool whose own
	// result says ok:false, so a true here proves nothing.
	for _, key := range []string{"success", "ok"} {
		if raw, ok := payload[key]; ok {
			var b bool
			if json.Unmarshal(raw, &b) == nil && !b {
				failed = true
			}
		}
	}

	// `error` is a string in most GAIA tools and an object in some sidecars.
	if raw, ok := payload["error"]; ok {
		var text string
		if json.Unmarshal(raw, &text) == nil {
			if strings.TrimSpace(text) != "" {
				failed = true
				te.Message = text
			}
		} else {
			var obj map[string]json.RawMessage
			if json.Unmarshal(raw, &obj) == nil {
				if msg := firstStringField(obj, "message", "detail", "description"); msg != "" {
					failed = true
					te.Message = msg
				}
				te.Code = firstStringField(obj, "code", "type")
			}
		}
	}

	// The email sidecar string-encodes the tool's whole return value into
	// `summary`, so the failure is one level down.
	if summary, ok := stringField(payload, "summary"); ok {
		if nested, nestedFailed := toolErrorFromEncoded(summary, summaryTruncated); nestedFailed {
			failed = true
			if te.Message == "" {
				te.Message = nested.Message
			}
			if te.Code == "" {
				te.Code = nested.Code
			}
		}
	}

	if !failed {
		return ToolError{}, false
	}
	if te.Message == "" {
		te.Message = firstStringField(payload, "detail", "message", "summary")
	}
	if te.Code == "" {
		te.Code = firstStringField(payload, "code", "error_code")
	}
	return te, true
}

// TruncatedNote is appended when a tool's own message arrived cut short. The
// producer caps `summary` (gaia.ui.sse_handler) and can sever it mid-escape, so
// the remedy the tool author wrote may be gone before the TUI ever sees it —
// say that rather than present a half sentence as the whole answer.
const TruncatedNote = "(the agent cut this message short before sending it — run with --debug for the raw payload)"

// toolErrorFromEncoded reads a tool result that was JSON-encoded into a string.
// A payload that was truncated mid-encoding no longer parses, so the raw text is
// surfaced instead: it is still the tool's own words, and dropping it would
// leave only the model's paraphrase.
func toolErrorFromEncoded(encoded string, truncatedHint bool) (ToolError, bool) {
	trimmed := strings.TrimSpace(encoded)
	if !strings.HasPrefix(trimmed, "{") {
		return ToolError{}, false
	}
	if te, failed := toolErrorFrom(json.RawMessage(trimmed), false); failed || json.Valid([]byte(trimmed)) {
		return te, failed
	}
	// Unparsable: look for the markers a failed GAIA tool result carries.
	if truncatedHint && truncatedPayloadShowsPartialSuccess(trimmed) {
		return ToolError{}, false
	}
	for _, marker := range []string{`"error"`, `"ok": false`, `"ok":false`} {
		if strings.Contains(trimmed, marker) {
			return ToolError{Message: trimmed + "\n" + TruncatedNote}, true
		}
	}
	return ToolError{}, false
}

// truncatedPayloadShowsPartialSuccess recognizes the stable batch envelope
// evidence without trying to parse an intentionally incomplete JSON string.
func truncatedPayloadShowsPartialSuccess(encoded string) bool {
	const key = `"succeeded"`
	idx := strings.Index(encoded, key)
	if idx < 0 {
		return false
	}
	rest := strings.TrimSpace(encoded[idx+len(key):])
	if !strings.HasPrefix(rest, ":") {
		return false
	}
	rest = strings.TrimSpace(rest[1:])
	if strings.HasPrefix(rest, "[") {
		rest = strings.TrimSpace(rest[1:])
		return rest != "" && !strings.HasPrefix(rest, "]")
	}
	return rest != "" && rest[0] >= '1' && rest[0] <= '9'
}

func stringField(payload map[string]json.RawMessage, key string) (string, bool) {
	raw, ok := payload[key]
	if !ok {
		return "", false
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return "", false
	}
	return s, true
}

func firstStringField(payload map[string]json.RawMessage, keys ...string) string {
	for _, key := range keys {
		if s, ok := stringField(payload, key); ok && strings.TrimSpace(s) != "" {
			return s
		}
	}
	return ""
}
