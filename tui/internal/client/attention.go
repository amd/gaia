package client

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/amd/gaia/tui/internal/daemon"
)

// AttentionFetcher is implemented by transports that can fetch the email
// agent's read-only attention view (GET /v1/email/attention, #2582) without
// going through the agent loop. Only the daemon-relayed SSEClient implements
// it today — a subprocess-mode client has no HTTP relay to ask, so callers
// must type-assert and skip gracefully when it's absent rather than treating
// the absence as a fetch error.
type AttentionFetcher interface {
	// FetchAttention returns the raw JSON body of a successful attention-view
	// response's `result` object (the `email_attention` envelope) for direct
	// rendering by cards.RenderEmailAttention. It never touches the chat
	// transcript — this is a side-channel read, not a turn.
	FetchAttention(ctx context.Context) (json.RawMessage, error)
}

// attentionEnvelope mirrors just enough of EmailAttentionResponse to unwrap
// `result` — the full shape is decoded client-side by the cards renderer,
// not re-modeled here.
type attentionEnvelope struct {
	Result json.RawMessage `json:"result"`
}

// errorDetailFromBody mirrors daemon.ErrorDetail's parsing but works from
// already-read bytes, since the body can only be consumed once and this
// caller needs it both for the success path (decode `result`) and the
// error path (extract `detail`).
func errorDetailFromBody(statusCode int, raw []byte) string {
	if len(raw) == 0 {
		return fmt.Sprintf("HTTP %d", statusCode)
	}
	var body struct {
		Detail json.RawMessage `json:"detail"`
	}
	if err := json.Unmarshal(raw, &body); err == nil && len(body.Detail) > 0 {
		var s string
		if err := json.Unmarshal(body.Detail, &s); err == nil && s != "" {
			return fmt.Sprintf("HTTP %d: %s", statusCode, s)
		}
		return fmt.Sprintf("HTTP %d: %s", statusCode, string(body.Detail))
	}
	return fmt.Sprintf("HTTP %d", statusCode)
}

// FetchAttention implements AttentionFetcher for the daemon-relayed
// transport. Blocking — like Send(), it may start-or-attach the daemon and
// spawn the sidecar on first use — so callers dispatch it off the UI event
// loop the same way sendQuery does.
func (s *SSEClient) FetchAttention(ctx context.Context) (json.RawMessage, error) {
	inst, err := s.daemon.EnsureAgent(ctx, s.agentID)
	if err != nil {
		return nil, err
	}

	relayPath := fmt.Sprintf("/v1/%s/attention", s.agentID)
	resp, inst, err := s.daemon.Do(ctx, inst, daemon.Request{
		Method: http.MethodGet,
		Path:   relayPath,
		Header: http.Header{"Accept": []string{"application/json"}},
		Op:     fmt.Sprintf("fetch the '%s' attention view through the daemon relay", s.agentID),
	})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	s.mu.Lock()
	s.inst = inst
	s.mu.Unlock()

	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if readErr != nil {
		return nil, fmt.Errorf("could not read the '%s' attention response: %w", s.agentID, readErr)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf(
			"the daemon relay refused the '%s' attention view (%s)",
			s.agentID, errorDetailFromBody(resp.StatusCode, raw),
		)
	}

	var env attentionEnvelope
	if err := json.Unmarshal(raw, &env); err != nil {
		return nil, fmt.Errorf("could not decode the '%s' attention response: %w", s.agentID, err)
	}
	return env.Result, nil
}
