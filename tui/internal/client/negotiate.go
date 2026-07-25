package client

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

// Contract-version negotiation for optional request fields.
//
// The two halves of a feature reach users on different clocks: a TUI change
// ships the moment the TUI builds, while a sidecar change only lands when a new
// agent binary is PUBLISHED and the user installs it. So a freshly built TUI
// routinely talks to an older sidecar than the source tree it was built from.
//
// Sidecar request models are strict (`extra="forbid"`), which is correct — an
// unknown field is a loud 422, not a silently ignored one. That makes sending a
// field the peer never agreed to a hard failure of EVERY request, not a
// degraded one. So the client asks first: `GET /v1/<agent>/version` reports the
// peer's contract version, and an optional field is only sent when the peer is
// new enough to accept it.
//
// Absent the capability the client does not claim it. That is negotiation, not
// a silent fallback: the capability genuinely is not there, and the honest thing
// is to say so — which `noticeForMissingCapability` does, once, where the user
// can read it.

// questionsContract is the contract version that introduced `needs_input` plus
// `POST /query/{run_id}/respond` (#2469). A peer below it 422s the
// `can_answer_questions` field, so it is omitted entirely.
const (
	questionsContractMajor = 2
	questionsContractMinor = 6
)

// versionProbeTimeout bounds the negotiation round-trip. Short: it is a local
// daemon relay, and the probe must never be the reason a turn feels slow. On
// failure the client assumes the peer is old, which is the answer that keeps
// working.
const versionProbeTimeout = 8 * time.Second

// peerContract is what the client learned about the sidecar it is talking to.
type peerContract struct {
	// version is the peer's reported apiVersion, or "" when unknown.
	version string
	// canAnswerQuestions is true only when the peer is provably >= 2.6.
	canAnswerQuestions bool
}

// negotiate resolves the peer's contract once per client and caches it.
//
// Cached for the life of this client, which is one agent launch: reinstalling
// the agent means relaunching it, and that builds a fresh client that re-probes.
func (s *SSEClient) negotiate(ctx context.Context, inst *daemon.Instance) peerContract {
	s.mu.Lock()
	if s.peerProbed {
		peer := s.peer
		s.mu.Unlock()
		return peer
	}
	s.mu.Unlock()

	peer := s.probeContract(ctx, inst)

	s.mu.Lock()
	s.peer = peer
	s.peerProbed = true
	s.mu.Unlock()
	return peer
}

func (s *SSEClient) probeContract(ctx context.Context, inst *daemon.Instance) peerContract {
	probeCtx, cancel := context.WithTimeout(ctx, versionProbeTimeout)
	defer cancel()

	resp, refreshed, err := s.daemon.Do(probeCtx, inst, daemon.Request{
		Method:     http.MethodGet,
		Path:       fmt.Sprintf("/v1/%s/version", url.PathEscape(s.agentID)),
		HTTPClient: s.cancelHTTP,
		Op:         fmt.Sprintf("read the '%s' agent's contract version", s.agentID),
	})
	if err != nil {
		// Not fatal: the query itself is about to run and will report its own
		// failure. Assuming "old" keeps that query valid.
		s.opts.Logf("sse: could not read the '%s' contract version (%v) — "+
			"assuming it predates optional request fields", s.agentID, err)
		return peerContract{}
	}
	defer resp.Body.Close()

	// The daemon rotates its client token on restart and hands back the instance
	// whose token authorized this call; dropping it would send the query that
	// follows to a stale port with a stale token.
	if refreshed != nil {
		s.mu.Lock()
		s.inst = refreshed
		s.mu.Unlock()
	}

	if resp.StatusCode != http.StatusOK {
		s.opts.Logf("sse: '%s' /version answered HTTP %d — assuming it predates "+
			"optional request fields", s.agentID, resp.StatusCode)
		return peerContract{}
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<10))
	if err != nil {
		s.opts.Logf("sse: could not read the '%s' /version body (%v)", s.agentID, err)
		return peerContract{}
	}
	var payload struct {
		APIVersion string `json:"apiVersion"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || payload.APIVersion == "" {
		s.opts.Logf("sse: '%s' /version returned no readable apiVersion", s.agentID)
		return peerContract{}
	}

	supports := contractAtLeast(payload.APIVersion, questionsContractMajor, questionsContractMinor)
	s.opts.Logf("sse: '%s' speaks contract %s (mid-run questions: %t)",
		s.agentID, payload.APIVersion, supports)
	return peerContract{version: payload.APIVersion, canAnswerQuestions: supports}
}

// contractAtLeast reports whether a "MAJOR.MINOR" version is >= the floor.
// An unparseable version is NOT at least anything — the safe answer for
// deciding whether to send a field the peer may reject.
func contractAtLeast(version string, major, minor int) bool {
	parts := strings.Split(strings.TrimSpace(version), ".")
	haveMajor, err := strconv.Atoi(strings.TrimSpace(parts[0]))
	if err != nil {
		return false
	}
	haveMinor := 0
	if len(parts) > 1 {
		if m, cerr := strconv.Atoi(strings.TrimSpace(parts[1])); cerr == nil {
			haveMinor = m
		}
	}
	if haveMajor != major {
		return haveMajor > major
	}
	return haveMinor >= minor
}

// noticeForMissingCapability is what the user is told when they are sitting at
// an interactive session whose agent is too old to be asked anything.
//
// It matters because the feature that needs it is the one that fixes a broken
// mailbox in-conversation: without it the agent falls back to reporting the
// connector error, and the user deserves to know why the offer never came rather
// than concluding the feature is broken.
func noticeForMissingCapability(agentID, version string) string {
	have := "an older contract"
	if version != "" {
		have = "contract " + version
	}
	return fmt.Sprintf(
		"the installed '%s' agent speaks %s, so it cannot ask questions mid-task — "+
			"in-conversation mailbox setup needs %d.%d or newer. "+
			"Update it with `gaia uninstall %s` then `gaia install %s`.",
		agentID, have, questionsContractMajor, questionsContractMinor, agentID, agentID)
}
