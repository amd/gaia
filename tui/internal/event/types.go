package event

import "encoding/json"

// Event is the base for all events. Use ParseEvent() to get concrete types.
type Event struct {
	Type string `json:"type"`
}

// StepEvent — agent loop iteration (step N of M)
type StepEvent struct {
	Type   string `json:"type"`
	Step   int    `json:"step"`
	Total  int    `json:"total"`
	Status string `json:"status"`
}

// ThinkingEvent — agent reasoning/chain-of-thought
type ThinkingEvent struct {
	Type    string `json:"type"`
	Content string `json:"content"`
}

// StatusEvent — agent state change (working, warning, info, complete)
type StatusEvent struct {
	Type    string `json:"type"`
	Status  string `json:"status"`
	Message string `json:"message,omitempty"`
	Steps   int    `json:"steps,omitempty"`
	Total   int    `json:"total,omitempty"`
}

// ToolStartEvent — tool invocation begins
type ToolStartEvent struct {
	Type   string `json:"type"`
	Tool   string `json:"tool"`
	Detail string `json:"detail,omitempty"`
}

// ToolArgsEvent — tool arguments
type ToolArgsEvent struct {
	Type string          `json:"type"`
	Tool string          `json:"tool"`
	Args json.RawMessage `json:"args"`
}

// ToolResultEvent — tool execution output
type ToolResultEvent struct {
	Type          string          `json:"type"`
	Title         string          `json:"title"`
	Success       bool            `json:"success"`
	CommandOutput json.RawMessage `json:"command_output,omitempty"`
	Summary       string          `json:"summary,omitempty"`
	ResultData    json.RawMessage `json:"result_data,omitempty"`
}

// ToolEndEvent — tool invocation complete
type ToolEndEvent struct {
	Type    string `json:"type"`
	Success bool   `json:"success"`
}

// AnswerEvent — final agent response
type AnswerEvent struct {
	Type      string `json:"type"`
	Content   string `json:"content"`
	Steps     int    `json:"steps"`
	ToolsUsed int    `json:"tools_used"`
}

// ChunkEvent — streaming LLM token (disabled in v1 json-events mode)
type ChunkEvent struct {
	Type    string `json:"type"`
	Content string `json:"content"`
}

// ErrorEvent — transport/system error
type ErrorEvent struct {
	Type    string `json:"type"`
	Content string `json:"content"`
}

// AgentErrorEvent — agent-level error
type AgentErrorEvent struct {
	Type    string `json:"type"`
	Content string `json:"content"`
}

// PlanEvent — multi-step plan
type PlanEvent struct {
	Type        string          `json:"type"`
	Steps       json.RawMessage `json:"steps"`
	CurrentStep int             `json:"current_step"`
}

// DoneEvent — stream complete marker
type DoneEvent struct {
	Type      string `json:"type"`
	MessageID string `json:"message_id,omitempty"`
	Content   string `json:"content,omitempty"`
}
