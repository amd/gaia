package client

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/event"
)

var _ MemoryProvider = (*SubprocessClient)(nil)

// memoryDumpQuery is the sentinel FetchMemory sends instead of a real
// question. Must match MEMORY_DUMP_QUERY in
// hub/agents/gaia/python/gaia_agent/memory_dump.py exactly — the wire text is
// the whole contract, and the leading/trailing NUL makes it something a human
// could never type, so it can never collide with a real chat message.
const memoryDumpQuery = "\x00gaia:memory_dump\x00"

// MemoryProvider is implemented by clients that can hand back the agent's
// stored memory directly, bypassing the LLM entirely. The /memory view uses
// this instead of opening ~/.gaia/memory.db itself: the agent process already
// holds that store open, and reading it a second time from Go would race the
// agent's own writes.
type MemoryProvider interface {
	FetchMemory(ctx context.Context) (MemoryDump, error)
}

// MemoryDump mirrors the JSON payload build_memory_dump() in
// hub/agents/gaia/python/gaia_agent/memory_dump.py returns. Available is
// false when the session has no live store (Lemonade down, embedding model
// not pulled, GAIA_MEMORY_DISABLED=1) — Reason is the actionable, cause-
// specific explanation from MemoryMixin.memory_unavailable_message(), not a
// generic "no memories" that would misreport an outage as an empty brain.
type MemoryDump struct {
	Available bool            `json:"available"`
	Reason    string          `json:"reason,omitempty"`
	Stats     MemoryStats     `json:"stats"`
	Contexts  []MemoryContext `json:"contexts"`
	Shown     int             `json:"shown"`
	Total     int             `json:"total"`
	Items     []MemoryItem    `json:"items"`
}

// MemoryStats is MemoryStore.get_stats()'s knowledge section, trimmed to what
// the view renders. ByCategory/ByContext count EVERY row (including ones
// beyond Shown), so the header can say "the agent knows about N categories"
// even when the item list itself is capped.
type MemoryStats struct {
	TotalKnowledge int            `json:"total_knowledge"`
	ByCategory     map[string]int `json:"by_category"`
	ByContext      map[string]int `json:"by_context"`
	SensitiveCount int            `json:"sensitive_count"`
	EntityCount    int            `json:"entity_count"`
	AvgConfidence  float64        `json:"avg_confidence"`
}

// MemoryContext is one row of MemoryStore.get_contexts().
type MemoryContext struct {
	Context string `json:"context"`
	Count   int    `json:"count"`
}

// MemoryItem is one active (non-superseded) knowledge row. Sensitive rows are
// included, not filtered — hiding them would defeat the reason this view
// exists (a plaintext secret the agent had stored was found only by asking
// the LLM to summarize its own memory, which is exactly the unreliable path
// this view replaces).
type MemoryItem struct {
	ID         string  `json:"id"`
	Category   string  `json:"category"`
	Content    string  `json:"content"`
	Entity     string  `json:"entity,omitempty"`
	Context    string  `json:"context"`
	Confidence float64 `json:"confidence"`
	Sensitive  bool    `json:"sensitive"`
	CreatedAt  string  `json:"created_at,omitempty"`
	UpdatedAt  string  `json:"updated_at,omitempty"`
	LastUsed   string  `json:"last_used,omitempty"`
}

// FetchMemory asks the running agent for its memory snapshot over the SAME
// stdin/stdout pipe Send uses, reusing its turn machinery rather than opening
// a second reader on the child's stdout. Like Send, callers must not overlap
// this with another in-flight turn; the chat model only calls it from
// submit(), which never runs while a turn is streaming (slash commands queue
// behind a live turn instead — see ChatModel.submit's doc comment).
func (s *SubprocessClient) FetchMemory(ctx context.Context) (MemoryDump, error) {
	ch, err := s.Send(ctx, memoryDumpQuery)
	if err != nil {
		return MemoryDump{}, err
	}
	for evt := range ch {
		switch e := evt.(type) {
		case event.CanonicalFinalEvent:
			var dump MemoryDump
			if err := json.Unmarshal([]byte(e.Answer), &dump); err != nil {
				return MemoryDump{}, fmt.Errorf(
					"could not read the agent's memory response: %w", err)
			}
			return dump, nil
		case event.CanonicalErrorEvent:
			return MemoryDump{}, fmt.Errorf("%s", e.Detail)
		case event.AgentErrorEvent:
			// A transport-level failure (the child exited, the pipe broke) —
			// Send synthesizes this one itself rather than reading it off the
			// wire, same as it does for a normal turn.
			return MemoryDump{}, fmt.Errorf("%s", e.Content)
		}
	}
	return MemoryDump{}, fmt.Errorf("the agent closed the connection before answering")
}

var _ MemoryProvider = (*SSEClient)(nil)

// ErrMemoryContractTooOld signals a peer whose contract predates
// GET /v1/<agent>/memory (#3978, schema 2.13) -- the caller must render an
// honest refusal naming the floor and the fix, never an empty memory view
// that reads as "the agent remembers nothing".
type ErrMemoryContractTooOld struct {
	AgentID string
	Version string
}

func (e *ErrMemoryContractTooOld) Error() string {
	return noticeForMissingMemory(e.AgentID, e.Version)
}

// FetchMemory implements MemoryProvider for the daemon-relayed transport:
// GET /v1/<agent>/memory through the daemon relay, cloning FetchPreScan's
// shape (prescan.go) -- ensure the sidecar, negotiate the peer's contract,
// gate on it before trusting the response, then relay the request.
func (s *SSEClient) FetchMemory(ctx context.Context) (MemoryDump, error) {
	// First, before any network work: this answer needs nothing from the peer,
	// and asking anyway would spawn a sidecar just to refuse — and a probe that
	// then failed would report a version problem for a situation that has
	// nothing to do with versions.
	if s.agentID != memoryAgentID {
		// A different agent entirely, not an out-of-date one: telling the user
		// to reinstall it would be wrong, and calling the route anyway would
		// turn a 404 into the "advertised then refused" shape #3978 removes.
		return MemoryDump{}, fmt.Errorf(
			"the '%s' agent does not keep a memory store — only '%s' does. "+
				"Run `gaia tui` to talk to it, or `gaia tui status` to see what is installed",
			s.agentID, memoryAgentID)
	}

	inst, err := s.daemon.EnsureAgent(ctx, s.agentID)
	if err != nil {
		return MemoryDump{}, err
	}

	// Check the peer's contract BEFORE trusting a response body, not after: a
	// pre-2.13 sidecar has no /memory route at all, and calling it anyway
	// would surface whatever a stray 404 handler answers as if it were a
	// real (if empty) memory dump.
	peer := s.negotiate(ctx, inst)
	if !peer.answered {
		// The /version probe never actually heard from the peer (relay error,
		// timeout, 401, 503, ...) -- that is NOT the same fact as "the peer
		// answered and is too old" (#3978 A1), and must not be reported as
		// ErrMemoryContractTooOld, which tells the user to reinstall a
		// possibly-current agent.
		return MemoryDump{}, fmt.Errorf(
			"could not confirm the '%s' agent's contract version, so its memory "+
				"route cannot be trusted yet. Check `gaia daemon status` and try "+
				"again once the agent responds",
			s.agentID)
	}
	if !contractAtLeast(peer.version, memoryContractMajor, memoryContractMinor) {
		return MemoryDump{}, &ErrMemoryContractTooOld{AgentID: s.agentID, Version: peer.version}
	}

	relayPath := fmt.Sprintf("/v1/%s/memory", s.agentID)
	resp, inst, err := s.daemon.Do(ctx, inst, daemon.Request{
		Method: http.MethodGet,
		Path:   relayPath,
		Header: http.Header{
			"Accept": []string{"application/json"},
		},
		Op: fmt.Sprintf("fetch the '%s' agent's memory through the daemon relay", s.agentID),
	})
	if err != nil {
		return MemoryDump{}, err
	}
	defer resp.Body.Close()

	s.mu.Lock()
	s.inst = inst
	s.mu.Unlock()

	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if readErr != nil {
		return MemoryDump{}, fmt.Errorf("could not read the '%s' memory response: %w", s.agentID, readErr)
	}
	if resp.StatusCode != http.StatusOK {
		return MemoryDump{}, fmt.Errorf(
			"the daemon relay refused the '%s' memory fetch (%s)",
			s.agentID, errorDetailFromBody(resp.StatusCode, raw),
		)
	}

	// Unlike /prescan, the route returns build_memory_dump()'s payload
	// directly as the response body -- no `result` envelope to unwrap.
	var dump MemoryDump
	if err := json.Unmarshal(raw, &dump); err != nil {
		return MemoryDump{}, fmt.Errorf("could not decode the '%s' memory response: %w", s.agentID, err)
	}
	return dump, nil
}
