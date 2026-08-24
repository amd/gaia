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
//
// The Model* fields are additive, riding the existing `status` type rather
// than a new one — but unlike Narration/Preview they are NOT part of the
// frozen `/query` HTTP contract other hub agents publish
// (docs/spec/agent-ui-query-sse-contract.md governs that one; the gaia
// flagship agent has no such surface). They are a stdio-transport-local
// extension: a model-state ping gaia_agent/stdio.py sends at startup and
// after a live `/model` switch. Safe to share this Go type with the
// daemon-relay path anyway — an agent that never sets these fields leaves
// them at their zero value, which decodes as an ordinary (blank) status
// line. ModelID is empty on every ordinary progress status — that's the
// field a receiver checks to tell the two apart (see
// ChatModel.handleCanonicalEvent).
type CanonicalStatusEvent struct {
	Type    string `json:"type"`
	Message string `json:"message"`
	// ModelID is the model actually resolved for chat (e.g. "claude-sonnet-5"
	// or "Gemma-4-E4B-it-GGUF") — empty means this is a plain status line, not
	// a model banner.
	ModelID string `json:"model_id,omitempty"`
	// ModelDisplay is the short header name for ModelID (e.g. "Sonnet 5"),
	// falling back to ModelID itself when there is no friendlier form.
	ModelDisplay string `json:"model_display,omitempty"`
	// ModelBackend is "claude" or "lemonade".
	ModelBackend string `json:"model_backend,omitempty"`
	// ModelRemote is true while inference runs off-machine (Anthropic) —
	// the header chip's warning color is keyed on this, not on ModelBackend,
	// so a future non-Claude remote backend still gets the warning.
	ModelRemote bool `json:"model_remote,omitempty"`
	// LemonadeReachable/LemonadeVersion/LemonadeBaseURL report the local model
	// server, and are sent even for a remote chat model: embeddings (RAG,
	// memory) still run on Lemonade, so "chat is remote" does not make Lemonade
	// being down harmless. Reachable is a *bool so "not reported" (an older
	// agent) stays distinguishable from "reported as down".
	LemonadeReachable *bool  `json:"lemonade_reachable,omitempty"`
	LemonadeVersion   string `json:"lemonade_version,omitempty"`
	LemonadeBaseURL   string `json:"lemonade_base_url,omitempty"`
}

// CanonicalTokenEvent — one incremental chunk of assistant answer text.
type CanonicalTokenEvent struct {
	Type  string `json:"type"`
	Delta string `json:"delta"`
}

// CanonicalToolCallEvent — a tool invocation with its arguments.
//
// Narration is the sidecar's own plain-language sentence for this call ("Reading
// issue #2924"). Additive and optional: an agent that doesn't send one gets a
// phrase derived from the tool name and its salient argument (see toolNarration
// in ui/chat), because a bare tool name is not what the user asked about.
type CanonicalToolCallEvent struct {
	Type      string          `json:"type"`
	Tool      string          `json:"tool"`
	Args      json.RawMessage `json:"args,omitempty"`
	Narration string          `json:"narration,omitempty"`
}

// CanonicalToolResultEvent — a tool's structured result.
//
// Render is the sidecar's declared card key (e.g. "email_pre_scan", or one of the
// generic primitives "table" / "key_value" / "list" / "image" / "diff"). Data is
// the render-specific payload. An unknown Render must degrade to a generic result
// card — never to a blank.
//
// Preview is the sidecar's own one-line outcome for the work log ("18 skills ·
// 21ms"). Additive and optional: absent, one is composed from Data (see
// toolResultDetail in ui/chat). It is never a substitute for the card — the card
// is the full result, this is the line under the tool call.
type CanonicalToolResultEvent struct {
	Type    string          `json:"type"`
	Tool    string          `json:"tool"`
	Render  string          `json:"render,omitempty"`
	Data    json.RawMessage `json:"data,omitempty"`
	Preview string          `json:"preview,omitempty"`
}

// CanonicalNeedsConfirmationEvent — the run pauses for a user decision.
// ConfirmURL is present only under the resume model; the stateless surface omits it.
type CanonicalNeedsConfirmationEvent struct {
	Type    string `json:"type"`
	RunID   string `json:"run_id"`
	Action  string `json:"action"`
	Summary string `json:"summary"`
	// ConfirmID is the emitter's handle for THIS prompt, echoed back with the
	// decision so a late answer cannot resolve the confirmation that replaced
	// the one it was typed for. Absent on transports that do not mint one.
	ConfirmID string `json:"confirm_id,omitempty"`
	// AlwaysScope is what an "always" answer would grant, e.g. `gh issue list`.
	// Empty means this call has no scope narrow enough to grant, so the client
	// must not offer the choice — the agent decides that, not the renderer.
	AlwaysScope string `json:"always_scope,omitempty"`
	ConfirmURL  string `json:"confirm_url,omitempty"`
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
// {steps?, tools_used?, elapsed?, tokens?, ttft?} object.
type CanonicalFinalEvent struct {
	Type   string          `json:"type"`
	Answer string          `json:"answer"`
	Usage  json.RawMessage `json:"usage,omitempty"`
}

// CanonicalUsage is the shape the TUI reads out of CanonicalFinalEvent.Usage.
// Fields absent from the payload stay zero and are simply not displayed.
// Tokens is the real generated-token count. TTFT is the turn's first LLM
// call's own measured time-to-first-token — the server-measured fallback
// used when no token ever streamed this turn.
// Metrics is the agent's per-turn performance record, present only when the
// agent ran with GAIA_TURN_LOG set. Nil on every ordinary turn and from any
// agent older than the record — callers must treat absence as normal.
type CanonicalUsage struct {
	Steps     int                 `json:"steps"`
	ToolsUsed int                 `json:"tools_used"`
	Elapsed   float64             `json:"elapsed"`
	Tokens    int                 `json:"tokens"`
	TTFT      float64             `json:"ttft"`
	Metrics   *CanonicalTurnStats `json:"-"`
}

// usageWire decodes the usage object with the record left as raw bytes, so a
// record whose inner shape we misread cannot take steps/tokens/ttft down with
// it — those are what the user sees on every turn.
type usageWire struct {
	CanonicalUsage
	Metrics json.RawMessage `json:"metrics,omitempty"`
}

// CanonicalTurnStats mirrors gaia.turn/1 (see turn_metrics.py). Only the fields
// the TUI renders are decoded; the record carries more, and the log file is
// where the rest is read.
type CanonicalTurnStats struct {
	TurnID    string              `json:"turn_id"`
	Model     string              `json:"model"`
	StartedAt string              `json:"started_at"`
	EndedAt   string              `json:"ended_at"`
	TotalS    float64             `json:"total_s"`
	Steps     int                 `json:"steps"`
	Prompt    CanonicalTurnPrompt `json:"prompt"`
	LLMCalls  []CanonicalTurnCall `json:"llm_calls"`
	ToolCalls []CanonicalTurnTool `json:"tool_calls"`
	Totals    CanonicalTurnTotals `json:"totals"`
}

// CanonicalTurnPrompt is the fixed cost re-sent on every call of the turn:
// FixedPrefillTokens is the system prompt plus tool schemas, which is the
// dominant term in a local turn's latency.
type CanonicalTurnPrompt struct {
	FixedPrefillTokens int      `json:"fixed_prefill_tokens"`
	SystemTokens       int      `json:"system_tokens"`
	ToolSchemaTokens   int      `json:"tool_schema_tokens"`
	ToolsSent          int      `json:"tools_sent"`
	SkillsActive       []string `json:"skills_active"`
}

// CanonicalTurnCall is one backend request. InputTokensCached is the KV-cache
// prefix this call could reuse; PrefillTokPerS is measured over the tokens the
// server actually had to read, so it reads far above the cold rate on a hit.
type CanonicalTurnCall struct {
	Step              int     `json:"step"`
	At                string  `json:"at"`
	WallS             float64 `json:"wall_s"`
	TTFTS             float64 `json:"ttft_s"`
	TokPerS           float64 `json:"tok_per_s"`
	InputTokensLocal  int     `json:"input_tokens_local"`
	InputTokensCached int     `json:"input_tokens_cached"`
	InputTokensNew    int     `json:"input_tokens_new"`
	InputTokens       float64 `json:"input_tokens"`
	OutputTokens      float64 `json:"output_tokens"`
	PrefillTokPerS    float64 `json:"prefill_tok_per_s"`
	// Reported by the backend itself, on backends that report it at all
	// (Anthropic's prefix cache does; a local llama.cpp KV cache does not).
	// Absent means unmeasured — not a miss.
	CacheReadInputTokens     float64 `json:"cache_read_input_tokens"`
	CacheCreationInputTokens float64 `json:"cache_creation_input_tokens"`
}

// CanonicalTurnTool is one tool execution, timed around every return path.
type CanonicalTurnTool struct {
	Step  int     `json:"step"`
	Name  string  `json:"name"`
	WallS float64 `json:"wall_s"`
	OK    bool    `json:"ok"`
}

// CanonicalTurnTotals splits the turn's wall time and its token counts. The
// *Local counts come from the client-side estimator and the *Server ones from
// the backend: they use different tokenizers, so a cached/new split is only
// ever valid within one source — never Cached_Server against Local.
type CanonicalTurnTotals struct {
	LLMS                    float64 `json:"llm_s"`
	ToolS                   float64 `json:"tool_s"`
	OverheadS               float64 `json:"overhead_s"`
	InputTokensLocal        int     `json:"input_tokens_local"`
	InputTokensCachedLocal  int     `json:"input_tokens_cached_local"`
	InputTokensNewLocal     int     `json:"input_tokens_new_local"`
	InputTokensServer       int     `json:"input_tokens_server"`
	InputTokensCachedServer int     `json:"input_tokens_cached_server"`
	CacheWriteTokensServer  int     `json:"cache_write_tokens_server"`
	OutputTokensServer      float64 `json:"output_tokens_server"`
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
	var wire usageWire
	if err := json.Unmarshal(e.Usage, &wire); err != nil {
		return CanonicalUsage{}
	}
	u = wire.CanonicalUsage
	if len(wire.Metrics) == 0 {
		return u
	}
	var stats CanonicalTurnStats
	if err := json.Unmarshal(wire.Metrics, &stats); err != nil {
		// The turn's own stats still stand; only the breakdown is lost.
		return u
	}
	u.Metrics = &stats
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
