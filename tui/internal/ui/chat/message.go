package chat

type MessageRole string

const (
	RoleUser      MessageRole = "user"
	RoleAssistant MessageRole = "assistant"
	RoleTool      MessageRole = "tool"
	RoleError     MessageRole = "error"
	RoleStatus    MessageRole = "status"
)

type Message struct {
	Role     MessageRole
	Content  string
	Rendered string
	ToolName string
	Success  *bool
}

type ActivityItem struct {
	Kind    string // "thinking", "tool", "step", "status"
	Content string
	Done    bool
	Success *bool
}
