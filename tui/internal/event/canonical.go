package event

import "encoding/json"

// The canonical `/query` SSE vocabulary from
// docs/spec/agent-ui-query-sse-contract.md §4, streamed by every v2 agent
// sidecar through the daemon relay: the frozen seven, plus `needs_input` — the
// additive-MINOR eighth type that resolved spec §9 Q3 (#2469).
//
// These are NOT the legacy in-process types in types.go (step / thinking /
// tool_start / chunk / answer / …), which the subprocess transport still uses.
// Both vocabularies coexist: the transport decides which parser runs.
//
// Two extra types carry the contract's receiving-end rules (§7) into the type
// system, so an event can never be silently dropped:
// CanonicalUnsupportedEvent (a `type` outside the seven) and
// CanonicalMalformedEvent (a frame that is not valid JSON for its type).
const (
	CanonicalTypeStatus            = "status"
	CanonicalTypeToken             = "token"
	CanonicalTypeToolCall          = "tool_call"
	CanonicalTypeToolResult        = "tool_result"
	CanonicalTypeNeedsConfirmation = "needs_confirmation"
	CanonicalTypeNeedsInput        = "needs_input"
	CanonicalTypeFinal             = "final"
	CanonicalTypeError             = "error"
)

// CanonicalStatusEvent — progress narration (spinner label / status line).
type CanonicalStatusEvent struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

// CanonicalTokenEvent — one incremental chunk of assistant answer text.
type CanonicalTokenEvent struct {
	Type  string `json:"type"`
	Delta string `json:"delta"`
}

// CanonicalToolCallEvent — a tool invocation with its arguments.
type CanonicalToolCallEvent struct {
	Type string          `json:"type"`
	Tool string          `json:"tool"`
	Args json.RawMessage `json:"args,omitempty"`
}

// CanonicalToolResultEvent — a tool's structured result.
//
// Render is the sidecar's declared card key (e.g. "email_pre_scan", or one of the
// generic primitives "table" / "key_value" / "list" / "image" / "diff"). Data is
// the render-specific payload. An unknown Render must degrade to a generic result
// card — never to a blank.
type CanonicalToolResultEvent struct {
	Type   string          `json:"type"`
	Tool   string          `json:"tool"`
	Render string          `json:"render,omitempty"`
	Data   json.RawMessage `json:"data,omitempty"`
}

// CanonicalNeedsConfirmationEvent — the run pauses for a user decision.
// ConfirmURL is present only under the resume model; the stateless surface omits it.
type CanonicalNeedsConfirmationEvent struct {
	Type       string `json:"type"`
	RunID      string `json:"run_id"`
	Action     string `json:"action"`
	Summary    string `json:"summary"`
	ConfirmURL string `json:"confirm_url,omitempty"`
}

// CanonicalNeedsInputEvent — the run pauses on a QUESTION and resumes on this
// same stream once the answer is POSTed to RespondURL (contract §5.1).
//
// Distinct from needs_confirmation on the one axis that matters: that one is a
// terminal approve/deny the run does not come back from, this one is answerable.
// Options are mutually exclusive; each carries a Label to pick and a Description
// of what picking it does. AllowFreeText adds the typed escape hatch — with no
// options at all it is a plain free-text prompt.
type CanonicalNeedsInputEvent struct {
	Type           string                 `json:"type"`
	RunID          string                 `json:"run_id"`
	RequestID      string                 `json:"request_id"`
	Question       string                 `json:"question"`
	Options        []CanonicalInputOption `json:"options,omitempty"`
	AllowFreeText  bool                   `json:"allow_free_text"`
	Sensitive      bool                   `json:"sensitive,omitempty"`
	RespondURL     string                 `json:"respond_url,omitempty"`
	TimeoutSeconds int                    `json:"timeout_seconds,omitempty"`
}

// CanonicalInputOption is one mutually-exclusive answer. Value is what goes back
// on the wire; Label is what the user picks; Description says what it will do.
type CanonicalInputOption struct {
	Value       string `json:"value"`
	Label       string `json:"label"`
	Description string `json:"description,omitempty"`
}

// CanonicalFinalEvent — terminal success. Usage is an optional
// {steps?, tools_used?, elapsed?, tokens?} object.
type CanonicalFinalEvent struct {
	Type   string          `json:"type"`
	Answer string          `json:"answer"`
	Usage  json.RawMessage `json:"usage,omitempty"`
}

// CanonicalUsage is the shape the TUI reads out of CanonicalFinalEvent.Usage.
// Fields absent from the payload stay zero and are simply not displayed.
// Tokens is the real generated-token count (#2899) — the TUI no longer
// guesses from the answer string's character length.
type CanonicalUsage struct {
	Steps     int     `json:"steps"`
	ToolsUsed int     `json:"tools_used"`
	Elapsed   float64 `json:"elapsed"`
	Tokens    int     `json:"tokens"`
}

// CanonicalErrorEvent — terminal failure. Detail is actionable and surfaced
// verbatim. Source is set by whoever synthesized the event when it did not come
// off the wire (e.g. "tui" for a stream that ended with no terminal event).
type CanonicalErrorEvent struct {
	Type   string `json:"type"`
	Detail string `json:"detail"`
	Status int    `json:"status,omitempty"`
	Source string `json:"source,omitempty"`
}

// CanonicalUnsupportedEvent — a top-level `type` outside the frozen seven, from a
// newer agent talking to this client. Contract §7: surface it visibly, never drop it.
type CanonicalUnsupportedEvent struct {
	EventType string
	Raw       string
}

// CanonicalMalformedEvent — a data frame that could not be parsed as its declared
// type. Surfaced so a broken producer is visible instead of looking like silence.
type CanonicalMalformedEvent struct {
	Payload string
	Reason  string
}

// CanonicalNoticeEvent — something the CLIENT has to say about this run, not a
// wire event. Used when contract negotiation finds the installed agent too old
// for a capability the user is about to want: the alternative is a feature that
// silently never appears, which reads as broken rather than as out of date.
type CanonicalNoticeEvent struct {
	Text string
}

// CanonicalUsageOf decodes a final event's usage object. A missing or unreadable
// usage payload yields the zero value — it is display metadata, not an outcome.
func CanonicalUsageOf(e CanonicalFinalEvent) CanonicalUsage {
	var u CanonicalUsage
	if len(e.Usage) == 0 {
		return u
	}
	if err := json.Unmarshal(e.Usage, &u); err != nil {
		return CanonicalUsage{}
	}
	return u
}

// CanonicalTerminalType returns "final" or "error" if evt terminates a run, else "".
// Exactly one terminal event ends a `/query` stream (contract §3).
func CanonicalTerminalType(evt interface{}) string {
	switch evt.(type) {
	case CanonicalFinalEvent:
		return CanonicalTypeFinal
	case CanonicalErrorEvent:
		return CanonicalTypeError
	}
	return ""
}
