package event

import (
	"encoding/json"
	"fmt"
)

// maxRawEcho caps how much of a bad payload is echoed back into a visible event.
const maxRawEcho = 400

// DaemonRelaySource marks the error frame the daemon writes when an agent's
// stream ends with no terminal event (the agent crashed mid-turn).
const DaemonRelaySource = "daemon_relay"

// AgentStoppedDetail is what the user sees for that frame. Sending again
// restarts the agent, so that is the remedy, as the Agent UI says too.
const AgentStoppedDetail = "GAIA stopped unexpectedly while answering. Anything it had " +
	"already written is kept above. Send your message again."

// AgentStoppedBeforeAnswerDetail is the same for an agent that died before
// this turn reached it (the daemon's "sidecar did not answer" 502).
const AgentStoppedBeforeAnswerDetail = "GAIA stopped unexpectedly before it could answer. " +
	"Send your message again."

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
	case CanonicalTypeNeedsInput:
		return decodeCanonical[CanonicalNeedsInputEvent](data, probe.Type)
	case CanonicalTypeFinal:
		return decodeCanonical[CanonicalFinalEvent](data, probe.Type)
	case CanonicalTypeError:
		decoded := decodeCanonical[CanonicalErrorEvent](data, probe.Type)
		if e, ok := decoded.(CanonicalErrorEvent); ok && e.Source == DaemonRelaySource {
			// The daemon's frame is worded for API callers; the daemon log keeps it.
			e.Detail = AgentStoppedDetail
			return e
		}
		return decoded
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
