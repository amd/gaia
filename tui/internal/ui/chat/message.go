package chat

import (
	"encoding/json"
	"time"
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
	TTFT      time.Duration // time to first event (model load + first inference)
	Steps     int           // agent steps taken
	ToolsUsed int           // tools invoked

	// Render / Data carry a RoleCard message's payload straight off the wire;
	// the cards package decides how (and whether) it can be drawn.
	Render string
	Data   json.RawMessage
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
