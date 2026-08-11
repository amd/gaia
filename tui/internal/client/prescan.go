package client

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/amd/gaia/tui/internal/daemon"
)

// PreScanFetcher is implemented by transports that can fetch the email
// agent's inbox pre-scan (POST /v1/email/prescan, #2743) without going
// through the agent loop. Only the daemon-relayed SSEClient implements it
// today — a subprocess-mode client has no HTTP relay to ask, so callers
// must type-assert and skip gracefully when it's absent rather than
// treating the absence as a fetch error.
//
// Replaces the pre-#2743 AttentionFetcher: needs_you is now the ONE
// worklist view built from the ONE scan the card renders from, so the TUI
// fetches the pre-scan envelope directly rather than a second,
// independently-depthed attention aggregation.
type PreScanFetcher interface {
	// FetchPreScan returns the raw JSON body of a successful pre-scan
	// response's `result` object (the `email_pre_scan` envelope) for direct
	// rendering by cards.Render. It never touches the chat transcript —
	// this is a side-channel read, not a turn.
	//
	// Returns *ErrPreScanContractTooOld when the peer's contract predates
	// needs_you (#2743, schema 2.11) — trusting an empty needs_you from an
	// older sidecar would render a confident "nothing needs you" that is
	// indistinguishable from a genuinely clear inbox, which is exactly the
	// bug #2743 exists to fix.
	FetchPreScan(ctx context.Context) (json.RawMessage, error)
}

// preScanEnvelope mirrors just enough of EmailPreScanResponse to unwrap
// `result` — the full shape is decoded client-side by the cards renderer,
// not re-modeled here.
type preScanEnvelope struct {
	Result json.RawMessage `json:"result"`
}

// ErrPreScanContractTooOld signals a peer whose contract predates the
// needs_you worklist (#2743, schema 2.11) — the caller must render an
// honest degraded notice, never trust an empty needs_you as a confident
// "nothing needs you" (the exact bug #2743 exists to fix, reproduced by an
// old sidecar unless this is gated).
type ErrPreScanContractTooOld struct {
	AgentID string
	Version string
}

func (e *ErrPreScanContractTooOld) Error() string {
	return noticeForMissingPreScan(e.AgentID, e.Version)
}

// FetchPreScan implements PreScanFetcher for the daemon-relayed transport.
// Blocking — like Send(), it may start-or-attach the daemon and spawn the
// sidecar on first use — so callers dispatch it off the UI event loop the
// same way sendQuery does.
func (s *SSEClient) FetchPreScan(ctx context.Context) (json.RawMessage, error) {
	inst, err := s.daemon.EnsureAgent(ctx, s.agentID)
	if err != nil {
		return nil, err
	}

	// Check the peer's contract BEFORE trusting an empty needs_you, not
	// after: a pre-2.11 sidecar's /prescan response simply omits the field,
	// which Go decodes as a zero-value empty slice -- indistinguishable
	// from a genuinely clear inbox unless this gate catches it first.
	peer := s.negotiate(ctx, inst)
	if !contractAtLeast(peer.version, preScanContractMajor, preScanContractMinor) {
		return nil, &ErrPreScanContractTooOld{AgentID: s.agentID, Version: peer.version}
	}

	relayPath := fmt.Sprintf("/v1/%s/prescan", s.agentID)
	resp, inst, err := s.daemon.Do(ctx, inst, daemon.Request{
		Method: http.MethodPost,
		Path:   relayPath,
		Body:   []byte("{}"),
		Header: http.Header{
			"Accept":       []string{"application/json"},
			"Content-Type": []string{"application/json"},
		},
		Op: fmt.Sprintf("fetch the '%s' inbox pre-scan through the daemon relay", s.agentID),
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
		return nil, fmt.Errorf("could not read the '%s' pre-scan response: %w", s.agentID, readErr)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf(
			"the daemon relay refused the '%s' inbox pre-scan (%s)",
			s.agentID, errorDetailFromBody(resp.StatusCode, raw),
		)
	}

	var env preScanEnvelope
	if err := json.Unmarshal(raw, &env); err != nil {
		return nil, fmt.Errorf("could not decode the '%s' pre-scan response: %w", s.agentID, err)
	}
	return env.Result, nil
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
