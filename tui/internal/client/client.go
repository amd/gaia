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

// TranscriptResetter is implemented by transports that own the conversation
// transcript host-side and push it back to a stateless agent on every turn.
// Clearing the visible history must also clear what gets pushed.
type TranscriptResetter interface {
	ResetTranscript()
}
