package chat

import (
	"encoding/json"
	"time"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/cards"
)

type MessageRole string

const (
	RoleUser      MessageRole = "user"
	RoleAssistant MessageRole = "assistant"
	RoleTool      MessageRole = "tool"
	RoleError     MessageRole = "error"
	RoleStatus    MessageRole = "status"
	// RoleCard is a typed `tool_result.render` card, drawn inline in the
	// transcript at the point the tool returned so work and results stay in order.
	RoleCard MessageRole = "card"
	// RoleWork is a finished turn's work log, kept in the transcript: one row
	// per step, with a failed step's reason under it. It is the durable record
	// of what ran, what failed and what the user approved.
	RoleWork MessageRole = "work"
)

type Message struct {
	Role         MessageRole
	Content      string
	Rendered     string
	ToolName     string
	Success      *bool
	Duration     time.Duration // time from query to answer
	TTFT         time.Duration // backend-measured time to first token; 0 => not reported, omit from display
	TokPerS      float64       // backend-measured generation rate; 0 => not reported, omit from display
	Steps        int           // agent steps taken
	ToolsUsed    int           // tools invoked
	Tokens       int           // real generated-token count; 0 => not reported, omit from display
	InputTokens  int           // prompt tokens this turn sent; 0 => not reported
	CachedTokens int           // prompt tokens served from the backend's cache

	// Recap is the finished turn's work in a few words ("Read 8 files · ran 2
	// commands"), and Deliverables the files it produced. FinishedAt is when
	// the answer landed, for the recap's one-time sweep.
	Recap        string
	Deliverables []deliverable
	// Provenance is where the turn ran and what it drew on; nil when it used
	// no tools.
	Provenance *provenance
	FinishedAt time.Time
	// CostUSD is what the turn cost on a per-token provider with a known
	// price; 0 when either is not the case.
	CostUSD float64

	// Metrics is the agent's per-turn performance record. Nil unless the agent
	// ran with GAIA_TURN_LOG set — every ordinary turn, and every turn from an
	// agent older than the record, leaves it nil.
	Metrics *event.CanonicalTurnStats

	// Render / Data carry a RoleCard message's payload straight off the wire;
	// the cards package decides how (and whether) it can be drawn.
	Render string
	Data   json.RawMessage

	// Identity marks a message updated in place rather than appended again
	// (see setup.go). Looked up by identity, not by a tracked index: `/clear`
	// sets ChatModel.messages to nil, so a stale index would overwrite an
	// unrelated message.
	Identity string

	// ImagePath marks a status line naming an image the agent created: the
	// path is drawn as a link, and a click on the line opens it.
	ImagePath string

	// Work is a RoleWork message's steps, as the live log held them.
	Work []ActivityItem

	// cardCache memoizes the drawn card. updateViewport re-renders every message
	// on each streamed token, and laying a card out means re-parsing its JSON —
	// so without this a long answer re-parses every card on screen per token.
	// Keyed by width; a resize invalidates it.
	cardCache      string
	cardCacheWidth int

	// renderedWrap is the markdown measure Rendered was laid out at. An answer
	// rendered before the window narrowed keeps rows wider than the pane, and
	// the viewport clips them; renderMessage re-renders when this differs.
	renderedWrap int
}

// renderCard draws the card at w, reusing the last render when the width has not
// changed.
func (m *Message) renderCard(w int) string {
	if m.cardCache != "" && m.cardCacheWidth == w {
		return m.cardCache
	}
	m.cardCache = cards.Render(m.Render, m.Data, w)
	m.cardCacheWidth = w
	return m.cardCache
}

type ActivityItem struct {
	// "thinking", "tool", "step", "status", "confirm", "note" — model text
	// streamed before a tool call, which is narration rather than the answer —
	// or "stage", a progress line the TUI itself owns ("Clearing conversation").
	Kind string
	// Content is the user-facing line — for a tool, the narrated phrase
	// ("Loading the github-triage skill"), never the bare tool name.
	Content string
	// Tool is the raw tool name behind a "tool" item. Kept alongside the
	// narration so repeat-folding groups by the tool that ran rather than by
	// the prose, which legitimately differs per call.
	Tool string
	// Detail is the one-line outcome drawn under the item once its result
	// lands ("18 skills · 21ms"). Empty until then.
	Detail string
	// Call is what the call said about itself: its category, the description
	// the model wrote with it, the derived summary, the command or code (for
	// Ctrl+O), the files it named and, for a write or an edit, the change.
	Call callInfo
	// Goal is the agent's announced goal standing when the call was made — the
	// headline when the call carries no description of its own.
	Goal string
	// At and End bracket the call, for the elapsed time on its row.
	At, End time.Time
	// OutLines is how many lines of output the call produced, 0 when unknown.
	OutLines int
	Done     bool
	Success  *bool
	// Approval is the user's answer to a confirmation this step asked for —
	// approvalWaiting until they answer, then "approved", "denied"… — or ""
	// when it asked for none.
	Approval string
	// Repeat counts additional consecutive occurrences folded into this item by
	// the live work log; 0 means it happened once.
	Repeat int
	// Sources are the pages and documents the call drew on (provenance.go).
	Sources []string
	// RetryKey is what "the same kind of call" means for a failed step that a
	// later success can settle (see retryKey). Empty means nothing to match
	// on, so the failure is never marked retried.
	RetryKey string
	// Recovery is how a failed step reads given the rest of its turn — set at
	// render time by settleFailures, never stored. "" is a plain failure.
	Recovery string
	// Progress is a running delegation's latest count ("2 of 4 helpers
	// done"), shown on the step's own row until its result lands.
	Progress string
	// Helpers is a finished delegation's per-helper outcomes, in id order;
	// MoreHelpers counts any past the most one step lists.
	Helpers     []helperOutcome
	MoreHelpers int
	// Partial marks a delegation some helpers failed or stopped short in,
	// while others returned something usable: a warning, not a failed step.
	Partial bool
}
