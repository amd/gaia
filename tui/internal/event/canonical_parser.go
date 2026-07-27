package event

import (
	"encoding/json"
	"fmt"
)

// maxRawEcho caps how much of a bad payload is echoed back into a visible event.
const maxRawEcho = 400

// ParseCanonicalEvent parses one canonical `/query` SSE data payload.
//
// It never returns an error, by design: the contract's receiving-end rules
// require that nothing is dropped, so both failure modes become visible events —
// an unknown `type` yields CanonicalUnsupportedEvent and an unreadable payload
// yields CanonicalMalformedEvent. Callers render whatever comes back and keep
// reading; only `final` / `error` end a run.
func ParseCanonicalEvent(data []byte) interface{} {
	var probe struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(data, &probe); err != nil {
		return CanonicalMalformedEvent{
			Payload: truncate(string(data), maxRawEcho),
			Reason:  fmt.Sprintf("not valid JSON: %v", err),
		}
	}
	if probe.Type == "" {
		return CanonicalMalformedEvent{
			Payload: truncate(string(data), maxRawEcho),
			Reason:  "the event carries no `type` field",
		}
	}

	switch probe.Type {
	case CanonicalTypeStatus:
		return decodeCanonical[CanonicalStatusEvent](data, probe.Type)
	case CanonicalTypeToken:
		return decodeCanonical[CanonicalTokenEvent](data, probe.Type)
	case CanonicalTypeToolCall:
		return decodeCanonical[CanonicalToolCallEvent](data, probe.Type)
	case CanonicalTypeToolResult:
		return decodeCanonical[CanonicalToolResultEvent](data, probe.Type)
	case CanonicalTypeNeedsConfirmation:
		return decodeCanonical[CanonicalNeedsConfirmationEvent](data, probe.Type)
	case CanonicalTypeFinal:
		return decodeCanonical[CanonicalFinalEvent](data, probe.Type)
	case CanonicalTypeError:
		return decodeCanonical[CanonicalErrorEvent](data, probe.Type)
	default:
		// §7: a newer agent's new event type must be visible, not dropped.
		return CanonicalUnsupportedEvent{
			EventType: probe.Type,
			Raw:       truncate(string(data), maxRawEcho),
		}
	}
}

func decodeCanonical[T any](data []byte, etype string) interface{} {
	var e T
	if err := json.Unmarshal(data, &e); err != nil {
		return CanonicalMalformedEvent{
			Payload: truncate(string(data), maxRawEcho),
			Reason:  fmt.Sprintf("malformed %s event: %v", etype, err),
		}
	}
	return e
}

func truncate(s string, limit int) string {
	if len(s) <= limit {
		return s
	}
	return s[:limit] + "…"
}
