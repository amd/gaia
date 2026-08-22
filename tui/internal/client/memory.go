package client

import (
	"context"
	"encoding/json"
	"fmt"

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
