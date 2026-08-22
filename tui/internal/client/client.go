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

// AgentCanceler is implemented by transports where the server, not this
// client dropping its connection, decides when a cancelled run has actually
// settled (#2901) — e.g. a daemon-relay session guarded by a server-side
// lock that a worker thread releases on its own cooperative schedule.
//
// Cancel asks the server to stop the active run out of band. It deliberately
// does NOT tear down the caller's own read of the run's event channel: that
// read has to keep going until the channel closes on its own, because THAT
// closure — not this call returning — is the one signal proven to follow the
// server's cleanup. A transport with no such server-side lock (e.g. a local
// subprocess) does not implement this; for it, tearing down the local
// connection/process IS the whole cancellation, and the caller's own
// context.CancelFunc already does that.
type AgentCanceler interface {
	// Cancel asks the server to stop the currently active run. It returns an
	// actionable error if the request could not be delivered; a run that has
	// already ended is not an error (there is nothing left to cancel).
	Cancel(ctx context.Context) error
}

// AgentConfirmer is implemented by transports that can resolve a
// needs_confirmation pause under the resume model (spec §5: the event carries
// a non-empty confirm_url and the run stays paused server-side awaiting it).
//
// No shipped sidecar sets confirm_url today — every current agent speaks the
// stateless stop-and-hand-off model, where needs_confirmation is immediately
// terminal and there is nothing to resume. A transport still implements this
// so a future resume-model peer is not left unreachable by the client; the UI
// only calls it when the triggering event actually carried a confirm_url.
type AgentConfirmer interface {
	// Confirm delivers the user's decision for runID's pending confirmation.
	// It returns an actionable error if the run is gone (the pause expired) or
	// is not waiting on a confirmation.
	Confirm(ctx context.Context, runID string, approved bool) error
}

// PermissionDecision is one answer to a live tool-permission prompt.
type PermissionDecision string

const (
	// PermissionAllow runs this call and asks again next time.
	PermissionAllow PermissionDecision = "allow"
	// PermissionDeny refuses this call. The run continues — the agent sees a
	// denied tool result and can choose something else.
	PermissionDeny PermissionDecision = "deny"
	// PermissionAlways runs this call and stops asking for the same TOOL for
	// the rest of the session, whatever arguments later calls carry. That is
	// the scope the agent actually records
	// (OutputHandler.session_approved_tools); any UI offering this must say so.
	PermissionAlways PermissionDecision = "always"
)

// ToolPermissionResponder is implemented by transports that can answer a
// permission prompt WHILE the run is parked on it.
//
// Distinct from AgentConfirmer, which resolves an already-finished run's
// recorded pause out of band. This one is the live seam: the agent thread is
// blocked inside confirm_tool_execution and resumes the moment the decision
// lands. A transport that cannot do this leaves the modal a record of intent —
// which is what produced the original defect, where every gated tool
// auto-denied because the answer had nowhere to go.
type ToolPermissionResponder interface {
	// RespondToolPermission delivers one decision. confirmID identifies the
	// prompt it was typed against so a late answer cannot resolve whichever
	// confirmation replaced it; empty means "whatever is pending".
	RespondToolPermission(confirmID string, decision PermissionDecision) error
}

// PermissionBypasser is implemented by transports that can put the agent into
// (or take it out of) bypass-permissions mode, where gated tools run without
// asking.
type PermissionBypasser interface {
	// SetBypassPermissions turns unattended approval on or off. It takes
	// effect on the next gated tool, including one in a turn already running.
	SetBypassPermissions(enabled bool) error
}
