package client

import (
	"context"
)

// AgentClient is the interface for communicating with an agent backend.
// Both subprocess (JSONL) and API (SSE) modes implement this interface.
type AgentClient interface {
	// Send starts a conversation turn. Events stream on the returned channel.
	// The channel is closed when the turn is complete (answer/done/status-complete event).
	Send(ctx context.Context, query string) (<-chan interface{}, error)

	// Close terminates the connection or process.
	Close() error
}
