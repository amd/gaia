package event

import (
	"encoding/json"
	"fmt"
)

// ParseEvent parses a JSONL line into a concrete event type.
// Returns the typed event or an error if the line is invalid JSON or has an unknown type.
func ParseEvent(line []byte) (interface{}, error) {
	var base Event
	if err := json.Unmarshal(line, &base); err != nil {
		return nil, fmt.Errorf("invalid JSON: %w", err)
	}

	switch base.Type {
	case "step":
		var e StepEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid step event: %w", err)
		}
		return e, nil
	case "thinking":
		var e ThinkingEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid thinking event: %w", err)
		}
		return e, nil
	case "status":
		var e StatusEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid status event: %w", err)
		}
		return e, nil
	case "tool_start":
		var e ToolStartEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid tool_start event: %w", err)
		}
		return e, nil
	case "tool_args":
		var e ToolArgsEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid tool_args event: %w", err)
		}
		return e, nil
	case "tool_result":
		var e ToolResultEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid tool_result event: %w", err)
		}
		return e, nil
	case "tool_end":
		var e ToolEndEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid tool_end event: %w", err)
		}
		return e, nil
	case "answer":
		var e AnswerEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid answer event: %w", err)
		}
		return e, nil
	case "chunk":
		var e ChunkEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid chunk event: %w", err)
		}
		return e, nil
	case "error":
		var e ErrorEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid error event: %w", err)
		}
		return e, nil
	case "agent_error":
		var e AgentErrorEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid agent_error event: %w", err)
		}
		return e, nil
	case "plan":
		var e PlanEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid plan event: %w", err)
		}
		return e, nil
	case "done":
		var e DoneEvent
		if err := json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("invalid done event: %w", err)
		}
		return e, nil
	default:
		return nil, fmt.Errorf("unknown event type: %q", base.Type)
	}
}
