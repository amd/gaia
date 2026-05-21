package chat

import "time"

type MessageRole string

const (
	RoleUser      MessageRole = "user"
	RoleAssistant MessageRole = "assistant"
	RoleTool      MessageRole = "tool"
	RoleError     MessageRole = "error"
	RoleStatus    MessageRole = "status"
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
}

type ActivityItem struct {
	Kind    string // "thinking", "tool", "step", "status"
	Content string
	Done    bool
	Success *bool
}
