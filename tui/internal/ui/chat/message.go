package chat

import (
	"encoding/json"
	"time"

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
)

type Message struct {
	Role      MessageRole
	Content   string
	Rendered  string
	ToolName  string
	Success   *bool
	Duration  time.Duration // time from query to answer
	TTFT      time.Duration // time from query submit to first inference token — not model-load or a tool/status event
	Steps     int           // agent steps taken
	ToolsUsed int           // tools invoked

	// Render / Data carry a RoleCard message's payload straight off the wire;
	// the cards package decides how (and whether) it can be drawn.
	Render string
	Data   json.RawMessage

	// Identity marks a RoleCard message as the ONE singular card of its
	// kind per turn-sequence (#2743) -- today only the email pre-scan card,
	// which can arrive from two independent sources (a typed turn's
	// tool_result, or the on-open pre-scan fetch) that must update the SAME
	// message in place rather than each appending its own. Empty for every
	// other card, which always appends. Looked up by identity, not by a
	// tracked index: `/clear` sets ChatModel.messages to nil (model.go), so
	// a stale index would panic or silently overwrite an unrelated message.
	Identity string

	// cardCache memoizes the drawn card. updateViewport re-renders every message
	// on each streamed token, and laying a card out means re-parsing its JSON —
	// so without this a long answer re-parses every card on screen per token.
	// Keyed by width; a resize invalidates it.
	cardCache      string
	cardCacheWidth int
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

// renderCardDeduped draws the card at w like renderCard, but skips any item
// whose message_id is already in seen and folds the ids it ends up showing
// into seen -- so a caller drawing more than one card in a turn threads the
// accumulated set across calls. Always recomputes rather than using the width
// cache: this card's visible content can legitimately differ call to call as
// seen grows while sibling cards in the same turn are drawn. seen may be nil,
// equivalent to renderCard.
func (m *Message) renderCardDeduped(w int, seen map[string]bool) string {
	rendered, ids := cards.RenderDeduped(m.Render, m.Data, w, seen)
	if seen != nil {
		for _, id := range ids {
			seen[id] = true
		}
	}
	return rendered
}

type ActivityItem struct {
	Kind    string // "thinking", "tool", "step", "status"
	Content string
	Done    bool
	Success *bool
	// Repeat counts additional consecutive occurrences folded into this item by
	// the live work log; 0 means it happened once.
	Repeat int
}
