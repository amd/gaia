package chat

import "github.com/amd/gaia/tui/internal/control"

// IsStreaming reports whether a query is currently in flight.
func (m ChatModel) IsStreaming() bool { return m.streaming }

// AgentID is the catalog id of the agent this chat is bound to.
func (m ChatModel) AgentID() string { return m.agentID }

// ControlSnapshot lets the control API describe a standalone chat session (the
// `gaia chat --subprocess` entry point, where ChatModel is the root model).
func (m ChatModel) ControlSnapshot() control.Snapshot {
	return control.Snapshot{
		View:      "chat",
		Agent:     m.agentID,
		Streaming: m.streaming,
	}
}
