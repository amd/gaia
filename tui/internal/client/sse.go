package client

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
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
	// A cancel POST must never wait out a stream read timeout.
	cancelTimeout = 10 * time.Second
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
	// Interactive declares that a human is watching and this client will render
	// a `needs_input` question and POST the answer. Default false: an agent that
	// asks a question nobody can answer parks the run until it times out, which
	// is indistinguishable from a hang. Only the interactive chat view sets it.
	Interactive bool
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
	// cancelHTTP is the short-timeout client for the cancel POST.
	cancelHTTP *http.Client

	// done is closed by Close(). It is the only way to unblock a consume()
	// goroutine whose consumer has stopped reading and whose buffer is full —
	// without it, Close() could not deliver the shutdown it advertises.
	done chan struct{}

	mu         sync.Mutex
	inst       *daemon.Instance
	transcript []Turn
	closed     bool
	active     *runHandle
	// peer is what negotiation learned about the sidecar's contract version;
	// peerProbed guards the one-shot probe. See negotiate.go.
	peer       peerContract
	peerProbed bool
	// noticedOldPeer records that the "this agent cannot be asked anything"
	// notice has already been shown, so it appears once per launch, not per turn.
	noticedOldPeer bool
	// sessionID identifies this conversation to a peer that supports it
	// (#2829). Minted lazily on first Send (mirrors runID's own mint time,
	// so a mint failure surfaces the same way runID's does); "" means "mint
	// on next Send". /clear clears it here so the NEXT turn starts a new
	// conversation server-side too, not just in the visible transcript.
	sessionID string
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
		agentID:    agentID,
		daemon:     dc,
		opts:       opts,
		done:       make(chan struct{}),
		stream:     daemon.StreamHTTPClient(opts.ConnectTimeout, opts.ReadTimeout),
		cancelHTTP: &http.Client{Timeout: cancelTimeout},
	}
}

type queryRequest struct {
	Query    string `json:"query"`
	RunID    string `json:"run_id"`
	Context  []Turn `json:"context"`
	Model    string `json:"model,omitempty"`
	MaxSteps int    `json:"max_steps,omitempty"`
	// A POINTER, so nil omits the key entirely. Sidecar request models are
	// strict: a peer that predates this field 422s EVERY request carrying it,
	// including `false`. Only set once negotiation proves the peer accepts it,
	// and then send it explicitly — including `false`, which is a real answer
	// the agent branches on, not an absence.
	CanAnswerQuestions *bool `json:"can_answer_questions,omitempty"`
	// Plain string (not a pointer, unlike CanAnswerQuestions): unlike a bool,
	// there is no legitimate "explicit empty" session_id to distinguish from
	// absence -- a minted id is never "", so plain omitempty is enough. Sent
	// only once negotiation proves the peer is >= 2.12 (#2829).
	SessionID string `json:"session_id,omitempty"`
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

	// Ask what the peer speaks BEFORE sending it an optional field (negotiate.go).
	// A published sidecar is routinely older than the source this TUI was built
	// from, and its strict request model rejects an unknown key outright.
	peer := s.negotiate(ctx, inst)
	var canAnswer *bool
	if peer.canAnswerQuestions {
		interactive := s.opts.Interactive
		canAnswer = &interactive
	}
	var sessionID string
	if peer.supportsSession {
		sessionID, err = s.ensureSessionID()
		if err != nil {
			return nil, err
		}
	}

	payload, err := json.Marshal(queryRequest{
		Query:              query,
		RunID:              runID,
		Context:            history,
		Model:              s.opts.Model,
		MaxSteps:           s.opts.MaxSteps,
		CanAnswerQuestions: canAnswer,
		SessionID:          sessionID,
	})
	if err != nil {
		return nil, fmt.Errorf("could not encode the '%s' query request: %w", s.agentID, err)
	}

	// runCtx is cancelled by Close() and by the read-idle watchdog. It derives
	// from ctx so the caller's own cancel (Esc) still aborts the stream, while
	// events can still be delivered on ctx after runCtx is gone.
	runCtx, cancel := context.WithCancel(ctx)

	relayPath := fmt.Sprintf("/v1/%s/query", url.PathEscape(s.agentID))
	s.opts.Logf("sse: POST %s%s run_id=%s model=%q max_steps=%d context_turns=%d",
		inst.BaseURL(), relayPath, runID, s.opts.Model, s.opts.MaxSteps, len(history))

	resp, inst, err := s.daemon.Do(runCtx, inst, daemon.Request{
		Method: http.MethodPost,
		Path:   relayPath,
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
	s.opts.Logf("sse: relay answered HTTP %d for '%s' run_id=%s", resp.StatusCode, s.agentID, runID)
	if resp.StatusCode != http.StatusOK {
		detail := daemon.ErrorDetail(resp)
		status := resp.StatusCode
		resp.Body.Close()
		cancel()
		switch status {
		case http.StatusNotFound:
			// A 404 on the query path is specifically "this route does not exist
			// there" — most often a sidecar predating the canonical /query
			// endpoint. Saying so beats making the user decode a bare 404.
			return nil, fmt.Errorf(
				"the '%s' agent has no /query endpoint (%s). Either the installed sidecar "+
					"predates the canonical query contract — check its version with "+
					"`gaia daemon agents` and reinstall/update the agent — or no agent with "+
					"that id is registered with the daemon",
				s.agentID, detail)
		case http.StatusConflict:
			// The session's run_lock is still held by a previous turn (#2901) —
			// most often the tail of a just-cancelled one the daemon has not
			// finished unwinding yet. Name the busy resource, like Respond and
			// Confirm already do for their own 409s, instead of the generic
			// relay-refused copy below (which points at a daemon that is down,
			// not one that is merely still finishing).
			return nil, fmt.Errorf(
				"the '%s' agent is still finishing the previous turn on this session (%s). "+
					"Wait a moment and try again",
				s.agentID, detail)
		default:
			return nil, fmt.Errorf(
				"the daemon relay refused the '%s' query (%s). Check `gaia daemon status`",
				s.agentID, detail)
		}
	}

	handle := &runHandle{runID: runID, cancel: cancel}
	s.mu.Lock()
	s.inst = inst
	// Close() may have landed while the request was in flight; registering the
	// handle unconditionally would leave that run un-cancellable.
	closedMidFlight := s.closed
	superseded := s.active
	if !closedMidFlight {
		s.active = handle
	}
	s.mu.Unlock()

	// Send() is documented as serialized, but an orphaned run would keep a
	// sidecar working with nobody listening — cancel it rather than trust the doc.
	if superseded != nil {
		superseded.cancel()
	}

	if closedMidFlight {
		cancel()
		resp.Body.Close()
		return nil, fmt.Errorf(
			"the %s agent connection was closed while the query was being sent", s.agentID)
	}

	ch := make(chan interface{}, 32)
	// Say it once, on the channel the UI already renders: a user at an
	// interactive session whose agent cannot be asked anything would otherwise
	// just never be offered the in-conversation fix, with no way to know why.
	if notice := s.oldPeerNotice(peer); notice != "" {
		ch <- event.CanonicalNoticeEvent{Text: notice}
	}
	go s.consume(ctx, runCtx, handle, resp, ch, query)
	return ch, nil
}

// oldPeerNotice returns the one-time warning text, or "" when none is due.
func (s *SSEClient) oldPeerNotice(peer peerContract) string {
	if peer.canAnswerQuestions || !s.opts.Interactive {
		return ""
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.noticedOldPeer {
		return ""
	}
	s.noticedOldPeer = true
	return noticeForMissingCapability(s.agentID, peer.version)
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
		case <-s.done:
			return false
		}
	}

	reader := newSSEFrameReader(resp.Body, func() { watchdog.Reset(s.opts.ReadTimeout) })

	var (
		terminal string
		answer   string
		// A local Builder is never copied, so it is safe here (unlike one held in
		// a value-copied Bubble Tea model) and avoids quadratic reallocation.
		streamed  strings.Builder
		delivered = true
		// Compact records of cards drawn this turn, folded into the transcript
		// so the next turn can resolve "that one" against what is on screen.
		shown []string
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
			streamed.WriteString(tok.Delta)
		}
		if fin, isFinal := evt.(event.CanonicalFinalEvent); isFinal {
			answer = fin.Answer
		}
		if tr, isResult := evt.(event.CanonicalToolResultEvent); isResult && tr.Render != "" {
			// What the user can SEE has to be what the model can refer to. A
			// card is drawn here, not by the sidecar, so without this the next
			// turn's history holds only the model's one-line framing — and a
			// follow-up like "when is that one?" resolves against nothing.
			shown = append(shown, displayedCard(tr))
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
				answer = streamed.String()
			}
			s.appendTurn(query, answer, shown)
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

	// Deliberately not inline: cancelRun runs before every deferred cleanup,
	// including close(ch), so a slow or hung daemon would hold the channel open
	// and let a superseded turn's completion land on the next turn.
	go s.cancelRun(handle)
}

func (s *SSEClient) readTimeoutDetail() string {
	return fmt.Sprintf(
		"the '%s' query stream sent nothing for %s and was abandoned. "+
			"The sidecar may be stuck loading a model — check `gaia daemon status`",
		s.agentID, s.opts.ReadTimeout)
}

// Respond answers the question the in-flight run is paused on.
//
// It is NOT best-effort like cancelRun: a swallowed answer looks exactly like an
// agent that stopped thinking, so every failure comes back to the caller with
// something the user can act on. The run continues on the stream Send() already
// returned — there is no second channel to read.
func (s *SSEClient) Respond(ctx context.Context, requestID, value string) error {
	s.mu.Lock()
	inst := s.inst
	active := s.active
	s.mu.Unlock()
	if inst == nil || active == nil {
		return fmt.Errorf(
			"there is no live '%s' run to answer — the question expired when the turn ended. Ask again",
			s.agentID)
	}

	payload, err := json.Marshal(respondRequest{RequestID: requestID, Value: value})
	if err != nil {
		return fmt.Errorf("could not encode the answer for '%s': %w", s.agentID, err)
	}

	resp, _, err := s.daemon.Do(ctx, inst, daemon.Request{
		Method: http.MethodPost,
		Path: fmt.Sprintf("/v1/%s/query/%s/respond",
			url.PathEscape(s.agentID), url.PathEscape(active.runID)),
		Body: payload,
		Header: http.Header{
			"Content-Type": []string{"application/json"},
		},
		HTTPClient: s.cancelHTTP,
		Op:         fmt.Sprintf("answer the '%s' agent's question", s.agentID),
	})
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		return nil
	case http.StatusNotFound:
		return fmt.Errorf(
			"the '%s' run had already ended, so the answer arrived too late. Ask again",
			s.agentID)
	case http.StatusConflict:
		return fmt.Errorf(
			"the '%s' agent is no longer waiting on that question — it timed out or was already answered. Ask again",
			s.agentID)
	default:
		return fmt.Errorf("answering the '%s' agent failed (%s)",
			s.agentID, daemon.ErrorDetail(resp))
	}
}

type respondRequest struct {
	RequestID string `json:"request_id"`
	Value     string `json:"value"`
}

// Confirm delivers an approve/deny decision for a needs_confirmation pause
// under the resume model (spec §5). The path is the deterministic one the
// spec documents for a confirm_url — "/v1/<agent>/query/{run_id}/confirm" —
// built here rather than trusting a server-supplied confirm_url string
// verbatim, exactly like Respond and cancelRun build their own paths instead
// of taking one off the wire.
//
// No shipped sidecar implements this route today: the email agent's `/query`
// speaks the stateless stop-and-hand-off model, where needs_confirmation ends
// the run immediately and there is no server-side pause to resume. Calling
// this against that sidecar gets a 404, handled below like any other
// already-ended run — the same shape Respond already handles, not a new
// failure class.
func (s *SSEClient) Confirm(ctx context.Context, runID string, approved bool) error {
	s.mu.Lock()
	inst := s.inst
	active := s.active
	s.mu.Unlock()
	if inst == nil || active == nil {
		return fmt.Errorf(
			"there is no live '%s' run to confirm — the run had already ended. Nothing was sent either way",
			s.agentID)
	}
	if active.runID != runID {
		return fmt.Errorf(
			"the '%s' run this decision was for is no longer the active one — a new turn started first. Nothing was sent",
			s.agentID)
	}

	payload, err := json.Marshal(confirmRequest{Approved: approved})
	if err != nil {
		return fmt.Errorf("could not encode the confirmation decision for '%s': %w", s.agentID, err)
	}

	resp, _, err := s.daemon.Do(ctx, inst, daemon.Request{
		Method: http.MethodPost,
		Path: fmt.Sprintf("/v1/%s/query/%s/confirm",
			url.PathEscape(s.agentID), url.PathEscape(runID)),
		Body: payload,
		Header: http.Header{
			"Content-Type": []string{"application/json"},
		},
		HTTPClient: s.cancelHTTP,
		Op:         fmt.Sprintf("deliver the '%s' agent's confirmation decision", s.agentID),
	})
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		return nil
	case http.StatusNotFound:
		return fmt.Errorf(
			"the '%s' run had already ended (no confirm endpoint to resume, or the run finished first), "+
				"so the decision arrived too late. Nothing was sent either way",
			s.agentID)
	case http.StatusConflict:
		return fmt.Errorf(
			"the '%s' agent is no longer waiting on that confirmation — it already resolved. Nothing was sent",
			s.agentID)
	default:
		return fmt.Errorf("delivering the '%s' agent's confirmation decision failed (%s)",
			s.agentID, daemon.ErrorDetail(resp))
	}
}

type confirmRequest struct {
	Approved bool `json:"approved"`
}

// postCancel POSTs /v1/<agent>/query/{runID}/cancel and returns the response,
// or an error if the request itself could not be delivered. Shared by
// cancelRun (best-effort, fire-and-forget) and Cancel (the caller-facing,
// error-reporting seam — #2901).
func (s *SSEClient) postCancel(ctx context.Context, inst *daemon.Instance, runID string) (*http.Response, error) {
	resp, _, err := s.daemon.Do(ctx, inst, daemon.Request{
		Method: http.MethodPost,
		Path: fmt.Sprintf("/v1/%s/query/%s/cancel",
			url.PathEscape(s.agentID), url.PathEscape(runID)),
		HTTPClient: s.cancelHTTP,
		Op:         fmt.Sprintf("cancel the '%s' run", s.agentID),
	})
	return resp, err
}

// cancelRun asks the relay to drop a run we are abandoning.
//
// Best-effort: the sidecar may already be gone. It owns its own background
// context so a cancelled caller context cannot kill the cancel itself, and it
// lives here rather than in the daemon package because the cancel path is part
// of the AGENT wire contract, not the daemon control plane.
func (s *SSEClient) cancelRun(handle *runHandle) {
	s.mu.Lock()
	inst := s.inst
	s.mu.Unlock()
	if inst == nil {
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), cancelTimeout)
	defer cancel()

	resp, err := s.postCancel(ctx, inst, handle.runID)
	if err != nil {
		s.opts.Logf("sse: best-effort cancel for '%s' run_id=%s failed: %v",
			s.agentID, handle.runID, err)
		return
	}
	defer resp.Body.Close()
	// 404 is the documented answer for a run that is no longer in flight — which
	// is exactly the case when we abandon a stream that already ended. That is
	// success, not a failure worth reporting as one.
	if resp.StatusCode == http.StatusNotFound {
		s.opts.Logf("sse: '%s' run_id=%s had already finished; nothing to cancel",
			s.agentID, handle.runID)
		return
	}
	if resp.StatusCode != http.StatusOK {
		s.opts.Logf("sse: cancel for '%s' run_id=%s answered HTTP %d",
			s.agentID, handle.runID, resp.StatusCode)
	}
}

// Cancel implements client.AgentCanceler (#2901). It asks the server to stop
// the active run WITHOUT touching this client's own read of the run's SSE
// stream — unlike the caller's context.CancelFunc, which tears that read
// down immediately and is exactly what let a resend beat the daemon's
// session run_lock (cooperative cancellation, released by the sidecar's
// worker thread's own `finally`, checked once per agent-loop step; see
// hub/agents/email/python/gaia_agent_email/query_routes.py).
//
// Leaving the read running is what makes the eventual channel close a true
// settlement signal: the sidecar's stream generator only sees its
// signal_done sentinel — and therefore only closes the HTTP response — after
// the SAME worker-thread `finally` has already released run_lock (signal_done
// runs, then release runs next, same thread, no I/O between them; the
// generator polls for the sentinel on a coarser interval than that gap). A
// client that aborts its own read instead observes a "done" that raced ahead
// of that release, which is the bug this method exists to avoid reintroducing.
func (s *SSEClient) Cancel(ctx context.Context) error {
	s.mu.Lock()
	inst := s.inst
	active := s.active
	s.mu.Unlock()
	if inst == nil || active == nil {
		// Nothing live to cancel — most often the run already reached its own
		// terminal event between the keypress and this call. Not an error: the
		// caller's read will observe that completion on its own.
		return nil
	}

	resp, err := s.postCancel(ctx, inst, active.runID)
	if err != nil {
		return fmt.Errorf("could not deliver the cancel request for the '%s' agent: %w", s.agentID, err)
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK, http.StatusNotFound:
		// 404 means the run had already ended by the time this landed — the
		// caller's read will see that completion on its own; nothing failed.
		return nil
	default:
		return fmt.Errorf("cancelling the '%s' run failed (%s)",
			s.agentID, daemon.ErrorDetail(resp))
	}
}

func (s *SSEClient) clearActive(handle *runHandle) {
	s.mu.Lock()
	if s.active == handle {
		s.active = nil
	}
	s.mu.Unlock()
}

// uiContextMarker labels the card-row block appended to a stored assistant
// turn. It reads as metadata, not as a quotable heading — a bare
// "[shown to the user]" sits in the transcript looking exactly like content
// the model itself wrote, and a later turn would copy it verbatim into a new
// reply instead of treating it as a reference note.
const uiContextMarker = "[ui-context: cards already shown to the user — reference only, never repeat verbatim]"

func (s *SSEClient) appendTurn(query, answer string, shown []string) {
	// The assistant turn records what the USER saw, not only what the model
	// said. Cards are drawn by this client, so their contents never reach the
	// sidecar's history on their own — and a follow-up referring to a row
	// ("when is that one?") would resolve against a one-line summary.
	content := answer
	if len(shown) > 0 {
		content = strings.TrimSpace(
			answer + "\n\n" + uiContextMarker + "\n" + strings.Join(shown, "\n"),
		)
	}
	s.mu.Lock()
	s.transcript = append(s.transcript,
		Turn{Role: "user", Content: query},
		Turn{Role: "assistant", Content: content},
	)
	s.mu.Unlock()
}

// displayedCard renders a drawn card as the few lines a model needs to resolve
// a reference to it. Deliberately lossy: senders and subjects are what users
// point at, and a verbatim payload would crowd the context it is meant to help.
func displayedCard(tr event.CanonicalToolResultEvent) string {
	const maxRows = 40
	var rows []string
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(tr.Data, &payload); err != nil {
		return tr.Render + " card displayed"
	}
	for _, bucket := range []string{"urgent", "actionable", "suggested_archives"} {
		raw, ok := payload[bucket]
		if !ok {
			continue
		}
		var items []struct {
			MessageID string `json:"message_id"`
			Sender    string `json:"sender"`
			Subject   string `json:"subject"`
		}
		if json.Unmarshal(raw, &items) != nil {
			continue
		}
		for _, it := range items {
			if len(rows) >= maxRows {
				break
			}
			rows = append(rows, fmt.Sprintf("- [%s] %s — %s (id %s)",
				bucket, it.Sender, it.Subject, it.MessageID))
		}
	}
	if len(rows) == 0 {
		return tr.Render + " card displayed"
	}
	return strings.Join(rows, "\n")
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
//
// Also drops the session id (#2829): /clear starts a NEW conversation, and a
// stale id would let the sidecar resolve the pre-clear agent even though the
// visible transcript says otherwise. No network call — matches ResetTranscript
// itself, which has no error return and is called outside a tea.Cmd
// (model.go), so a blocking DELETE here would freeze the UI. The next Send
// mints a fresh id lazily, exactly like the very first turn.
func (s *SSEClient) ResetTranscript() {
	s.mu.Lock()
	s.transcript = nil
	s.sessionID = ""
	s.mu.Unlock()
}

// ensureSessionID returns this conversation's session id, minting one on
// first use. Lazy and mutex-guarded so concurrent callers never mint two
// (Send() itself is documented as serialized, but this stays correct either
// way). Reuses newRunID's generator; a mint failure is reported the same way
// a run_id mint failure already is, by Send returning the error.
func (s *SSEClient) ensureSessionID() (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.sessionID != "" {
		return s.sessionID, nil
	}
	id, err := newRunID()
	if err != nil {
		return "", fmt.Errorf("could not mint a session id: %w", err)
	}
	s.sessionID = id
	return id, nil
}

// Close cancels any in-flight run and refuses further sends.
func (s *SSEClient) Close() error {
	s.mu.Lock()
	alreadyClosed := s.closed
	s.closed = true
	active := s.active
	s.active = nil
	s.mu.Unlock()

	if !alreadyClosed {
		close(s.done)
	}
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
	_ AgentResponder     = (*SSEClient)(nil)
	_ AgentConfirmer     = (*SSEClient)(nil)
	_ AgentCanceler      = (*SSEClient)(nil)
	_ TranscriptResetter = (*SSEClient)(nil)
)
