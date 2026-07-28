package client

import (
	"context"
)

// AgentClient is the interface for communicating with an agent backend.
// Both subprocess (JSONL) and daemon-relay (SSE) transports implement it.
type AgentClient interface {
	// Send starts a conversation turn. Events stream on the returned channel.
	// The channel is closed when the turn is complete (answer/done/status-complete event).
	Send(ctx context.Context, query string) (<-chan interface{}, error)

	// Close terminates the connection or process.
	Close() error
}

// AgentResponder is implemented by transports that can answer a mid-run
// question (a `needs_input` event) and let the paused run continue on its
// ORIGINAL stream. Send() would start a new turn instead — the answer has to go
// out of band, which is the whole point of the resume seam.
//
// A transport that cannot do this simply does not implement it, and the UI says
// so instead of silently swallowing the answer.
type AgentResponder interface {
	// Respond delivers value as the answer to requestID on the in-flight run.
	// It returns an actionable error if the run is gone (the question expired)
	// or is not waiting on that question.
	Respond(ctx context.Context, requestID, value string) error
}

// TranscriptResetter is implemented by transports that own the conversation
// transcript host-side and push it back to a stateless agent on every turn.
// Clearing the visible history must also clear what gets pushed.
type TranscriptResetter interface {
	ResetTranscript()
}
