package client

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/event"
)

// Timeouts mirror src/gaia/daemon/agent_query.py: connect fast (a dead daemon
// should fail quickly), read generously — a single upstream chunk can span a
// whole agent-loop step.
const (
	defaultConnectTimeout = 10 * time.Second
	defaultReadTimeout    = 300 * time.Second
)

// Turn is one entry of the host-owned transcript.
//
// The sidecar is stateless on /query (contract §2.4), so the TUI accumulates the
// transcript and pushes the slice as `context` on every turn. A turn is appended
// only when it terminated in `final`, so a failed run cannot poison the next one.
type Turn struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// SSEOptions configures an SSEClient. The zero value is valid.
type SSEOptions struct {
	// Model overrides the sidecar's default model id. Empty means "sidecar default".
	Model string
	// MaxSteps overrides the agent-loop step ceiling. Zero means "sidecar default".
	MaxSteps int
	// ConnectTimeout / ReadTimeout default to 10s / 300s. ReadTimeout is an
	// idle timeout: it fires only if no byte of the stream arrives within it.
	ConnectTimeout time.Duration
	ReadTimeout    time.Duration
	// Logf receives progress and best-effort-failure notes. Never given a token.
	Logf func(format string, args ...any)
}

// SSEClient drives one agent through the GAIA daemon's relay: it ensures the
// daemon and the agent's sidecar are up, POSTs /v1/<agent>/query, and streams the
// canonical SSE events (contract §4) onto the AgentClient channel.
//
// It holds only the DAEMON client token — never the sidecar's bearer, which stays
// server-side. Send() calls must be serialized, matching the AgentClient contract.
type SSEClient struct {
	agentID string
	daemon  *daemon.Client
	opts    SSEOptions
	// stream is reused across turns: one Transport per client, not per turn.
	stream *http.Client

	mu         sync.Mutex
	inst       *daemon.Instance
	transcript []Turn
	closed     bool
	active     *runHandle
}

// runHandle is the cancel side of the currently streaming run.
type runHandle struct {
	runID  string
	cancel context.CancelFunc
}

// NewSSEClient builds a daemon-transport client for agentID (the path segment in
// /v1/<agent>/query, e.g. "email").
func NewSSEClient(agentID string, dc *daemon.Client, opts SSEOptions) *SSEClient {
	if opts.ConnectTimeout <= 0 {
		opts.ConnectTimeout = defaultConnectTimeout
	}
	if opts.ReadTimeout <= 0 {
		opts.ReadTimeout = defaultReadTimeout
	}
	if opts.Logf == nil {
		opts.Logf = func(string, ...any) {}
	}
	return &SSEClient{
		agentID: agentID,
		daemon:  dc,
		opts:    opts,
		stream:  daemon.StreamHTTPClient(opts.ConnectTimeout),
	}
}

type queryRequest struct {
	Query    string `json:"query"`
	RunID    string `json:"run_id"`
	Context  []Turn `json:"context"`
	Model    string `json:"model,omitempty"`
	MaxSteps int    `json:"max_steps,omitempty"`
}

// Send starts one turn. The returned channel carries the canonical event types
// from the event package and is closed when the turn ends.
//
// Every turn ends with exactly one terminal event: the wire's `final` / `error`,
// or — if the stream ended without one — a synthesized CanonicalErrorEvent. A
// stream that stops early is a failure, never a quiet success.
func (s *SSEClient) Send(ctx context.Context, query string) (<-chan interface{}, error) {
	s.mu.Lock()
	closed := s.closed
	s.mu.Unlock()
	if closed {
		return nil, fmt.Errorf("the %s agent connection is closed; relaunch the agent from the hub", s.agentID)
	}

	runID, err := newRunID()
	if err != nil {
		return nil, err
	}

	// Blocking: daemon start-or-attach, sidecar spawn, and a possible first-run
	// binary fetch all happen here. Loud on failure — no in-process fallback.
	inst, err := s.daemon.EnsureAgent(ctx, s.agentID)
	if err != nil {
		return nil, err
	}

	s.mu.Lock()
	s.inst = inst
	// Non-nil even when empty: `context` is a REQUIRED array in the request
	// schema, and a nil slice would marshal to `null` and fail validation.
	history := make([]Turn, 0, len(s.transcript))
	history = append(history, s.transcript...)
	s.mu.Unlock()

	payload, err := json.Marshal(queryRequest{
		Query:    query,
		RunID:    runID,
		Context:  history,
		Model:    s.opts.Model,
		MaxSteps: s.opts.MaxSteps,
	})
	if err != nil {
		return nil, fmt.Errorf("could not encode the '%s' query request: %w", s.agentID, err)
	}

	// runCtx is cancelled by Close() and by the read-idle watchdog. It derives
	// from ctx so the caller's own cancel (Esc) still aborts the stream, while
	// events can still be delivered on ctx after runCtx is gone.
	runCtx, cancel := context.WithCancel(ctx)

	resp, inst, err := s.daemon.Do(runCtx, inst, daemon.Request{
		Method: http.MethodPost,
		Path:   fmt.Sprintf("/v1/%s/query", s.agentID),
		Body:   payload,
		Header: http.Header{
			"Content-Type": []string{"application/json"},
			"Accept":       []string{"text/event-stream"},
		},
		HTTPClient: s.stream,
		Op:         fmt.Sprintf("stream the '%s' query through the daemon relay", s.agentID),
	})
	if err != nil {
		cancel()
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		detail := daemon.ErrorDetail(resp)
		resp.Body.Close()
		cancel()
		return nil, fmt.Errorf(
			"the daemon relay refused the '%s' query (%s). Check `gaia daemon status`",
			s.agentID, detail)
	}

	handle := &runHandle{runID: runID, cancel: cancel}
	s.mu.Lock()
	s.inst = inst
	// Close() may have landed while the request was in flight; registering the
	// handle unconditionally would leave that run un-cancellable.
	closedMidFlight := s.closed
	if !closedMidFlight {
		s.active = handle
	}
	s.mu.Unlock()

	if closedMidFlight {
		cancel()
		resp.Body.Close()
		return nil, fmt.Errorf(
			"the %s agent connection was closed while the query was being sent", s.agentID)
	}

	ch := make(chan interface{}, 32)
	go s.consume(ctx, runCtx, handle, resp, ch, query)
	return ch, nil
}

// consume reads the SSE response and enforces the terminal-event contract.
func (s *SSEClient) consume(
	ctx context.Context,
	runCtx context.Context,
	handle *runHandle,
	resp *http.Response,
	ch chan interface{},
	query string,
) {
	defer close(ch)
	defer resp.Body.Close()
	defer handle.cancel()
	defer s.clearActive(handle)

	// Read-idle watchdog: mirrors agent_query.py's read timeout. Any traffic —
	// including a heartbeat comment — resets it; if it fires, the request is
	// cancelled and the run surfaces an error rather than hanging forever.
	var timedOut atomic.Bool
	watchdog := time.AfterFunc(s.opts.ReadTimeout, func() {
		timedOut.Store(true)
		handle.cancel()
	})
	defer watchdog.Stop()

	// emit selects on the CALLER's context, not the run context, so a watchdog or
	// Close() cancellation can still deliver its terminal error.
	emit := func(evt interface{}) bool {
		select {
		case ch <- evt:
			return true
		case <-ctx.Done():
			return false
		}
	}

	reader := newSSEFrameReader(resp.Body, func() { watchdog.Reset(s.opts.ReadTimeout) })

	var (
		terminal  string
		answer    string
		streamed  string
		delivered = true
	)

	for {
		payload, ok := reader.Next()
		if !ok {
			break
		}
		evt := event.ParseCanonicalEvent(payload)
		if malformed, bad := evt.(event.CanonicalMalformedEvent); bad {
			// Visible, but never fatal: one broken frame must not kill the run.
			s.opts.Logf("sse: unparseable '%s' frame (%s) — skipped", s.agentID, malformed.Reason)
		}
		if tok, isToken := evt.(event.CanonicalTokenEvent); isToken {
			streamed += tok.Delta
		}
		if fin, isFinal := evt.(event.CanonicalFinalEvent); isFinal {
			answer = fin.Answer
		}
		if !emit(evt) {
			delivered = false
			break
		}
		if t := event.CanonicalTerminalType(evt); t != "" {
			terminal = t
			break
		}
	}

	if terminal != "" {
		if terminal == event.CanonicalTypeFinal {
			if answer == "" {
				// The sidecar streamed the answer as tokens and closed with an
				// empty `final` — keep the streamed text as the turn's answer.
				answer = streamed
			}
			s.appendTurn(query, answer)
		}
		return
	}

	// No terminal event: report first (the user should not wait on a network
	// round-trip to learn the turn failed), then ask the relay to drop the run —
	// it may still be live inside the sidecar.
	switch {
	case timedOut.Load():
		emit(event.CanonicalErrorEvent{
			Type:   event.CanonicalTypeError,
			Detail: s.readTimeoutDetail(),
			Status: http.StatusGatewayTimeout,
			Source: "tui",
		})
	case ctx.Err() != nil || !delivered:
		// The user cancelled the turn (Esc / hub exit) — the UI already says so.
		s.opts.Logf("sse: '%s' run %s cancelled by the caller", s.agentID, handle.runID)
	case runCtx.Err() != nil:
		// Close() cancelled the run without the caller's context ending.
		emit(event.CanonicalErrorEvent{
			Type:   event.CanonicalTypeError,
			Detail: fmt.Sprintf("the '%s' agent connection was closed mid-run", s.agentID),
			Status: http.StatusServiceUnavailable,
			Source: "tui",
		})
	case reader.Err() != nil:
		emit(event.CanonicalErrorEvent{
			Type: event.CanonicalTypeError,
			Detail: fmt.Sprintf(
				"the '%s' query stream failed mid-run: %v. Check `gaia daemon status`",
				s.agentID, reader.Err()),
			Status: http.StatusBadGateway,
			Source: "tui",
		})
	default:
		// The contract mandates exactly one terminal event; a clean EOF without
		// one is a failure, surfaced loudly rather than as an empty answer.
		emit(event.CanonicalErrorEvent{
			Type: event.CanonicalTypeError,
			Detail: fmt.Sprintf(
				"the '%s' query stream ended without a terminal final/error event — "+
					"the sidecar may have crashed mid-run. Check `gaia daemon status`",
				s.agentID),
			Status: http.StatusBadGateway,
			Source: "tui",
		})
	}

	s.cancelRun(handle)
}

func (s *SSEClient) readTimeoutDetail() string {
	return fmt.Sprintf(
		"the '%s' query stream sent nothing for %s and was abandoned. "+
			"The sidecar may be stuck loading a model — check `gaia daemon status`",
		s.agentID, s.opts.ReadTimeout)
}

// cancelRun tells the relay to drop a run we are abandoning. Best-effort.
func (s *SSEClient) cancelRun(handle *runHandle) {
	s.mu.Lock()
	inst := s.inst
	s.mu.Unlock()
	if inst == nil {
		return
	}
	s.daemon.CancelRun(inst, s.agentID, handle.runID)
}

func (s *SSEClient) clearActive(handle *runHandle) {
	s.mu.Lock()
	if s.active == handle {
		s.active = nil
	}
	s.mu.Unlock()
}

func (s *SSEClient) appendTurn(query, answer string) {
	s.mu.Lock()
	s.transcript = append(s.transcript,
		Turn{Role: "user", Content: query},
		Turn{Role: "assistant", Content: answer},
	)
	s.mu.Unlock()
}

// Transcript returns a copy of the host-owned transcript pushed as `context`.
func (s *SSEClient) Transcript() []Turn {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Turn(nil), s.transcript...)
}

// ResetTranscript drops the accumulated context, so the next turn starts fresh.
// Wired to the chat screen's /clear, which would otherwise clear the visible
// history while still pushing it as `context`.
func (s *SSEClient) ResetTranscript() {
	s.mu.Lock()
	s.transcript = nil
	s.mu.Unlock()
}

// Close cancels any in-flight run and refuses further sends.
func (s *SSEClient) Close() error {
	s.mu.Lock()
	s.closed = true
	active := s.active
	s.active = nil
	s.mu.Unlock()

	if active != nil {
		active.cancel()
	}
	return nil
}

// newRunID mints the host-side run handle (contract §2.3): the client knows it
// before the request is sent, so a run is cancellable from that instant.
func newRunID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", fmt.Errorf("could not mint a run id: %w", err)
	}
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // RFC 4122 variant
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16]), nil
}

// Compile-time proof the daemon transport satisfies the same interface as the
// subprocess one.
var (
	_ AgentClient        = (*SSEClient)(nil)
	_ TranscriptResetter = (*SSEClient)(nil)
)
