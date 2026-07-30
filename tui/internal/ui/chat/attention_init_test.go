package chat

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
)

// fakeAttentionClient satisfies both client.AgentClient and
// client.AttentionFetcher, so Init()'s on-open fetch (#2582) has something to
// type-assert against.
type fakeAttentionClient struct {
	nullClient
	data       json.RawMessage
	err        error
	fetchCalls int
}

func (f *fakeAttentionClient) FetchAttention(context.Context) (json.RawMessage, error) {
	f.fetchCalls++
	if f.err != nil {
		return nil, f.err
	}
	return f.data, nil
}

const sampleAttentionJSON = `{"kind":"email_attention","items":[],"coverage":{"scanned":3},"generated_at":"x","cache_age_seconds":0.0,"stale":false}`

func newAttentionTestModel(t *testing.T, c client.AgentClient, agentID, initialQuery string) ChatModel {
	t.Helper()
	m := NewChatModel(c, agentID, initialQuery, false)
	m.agentID = agentID
	m.width, m.height = 100, 30
	return m
}

// findBatchedMsg runs every Cmd in a (possibly batched) tea.Cmd and returns
// the first message matching want's dynamic type, or nil if none did.
func findBatchedMsg(cmd tea.Cmd, isWanted func(tea.Msg) bool) tea.Msg {
	if cmd == nil {
		return nil
	}
	msg := cmd()
	if isWanted(msg) {
		return msg
	}
	if batch, ok := msg.(tea.BatchMsg); ok {
		for _, sub := range batch {
			if found := findBatchedMsg(sub, isWanted); found != nil {
				return found
			}
		}
	}
	return nil
}

func TestFetchAttentionOnOpenForEmailAgentWithNoInitialQuery(t *testing.T) {
	c := &fakeAttentionClient{data: json.RawMessage(sampleAttentionJSON)}
	m := newAttentionTestModel(t, c, "email", "")

	cmd := m.Init()
	found := findBatchedMsg(cmd, func(msg tea.Msg) bool {
		_, ok := msg.(attentionFetchedMsg)
		return ok
	})
	if found == nil {
		t.Fatal("Init() did not dispatch an attention fetch for the email agent with no initial query")
	}
	fetched := found.(attentionFetchedMsg)
	if string(fetched.data) != sampleAttentionJSON {
		t.Errorf("fetched data = %s, want %s", fetched.data, sampleAttentionJSON)
	}
}

func TestFetchAttentionSkippedWhenInitialQueryPresent(t *testing.T) {
	// A launch-with-query must never race the attention fetch against the
	// answer the user is actually waiting on.
	c := &fakeAttentionClient{data: json.RawMessage(sampleAttentionJSON)}
	m := newAttentionTestModel(t, c, "email", "what's in my inbox?")

	cmd := m.Init()
	_ = findBatchedMsg(cmd, func(tea.Msg) bool { return false }) // drain, ignore result
	if c.fetchCalls != 0 {
		t.Errorf("attention fetch was called %d times; want 0 when an initial query is present", c.fetchCalls)
	}
}

func TestFetchAttentionSkippedForNonEmailAgent(t *testing.T) {
	c := &fakeAttentionClient{data: json.RawMessage(sampleAttentionJSON)}
	m := newAttentionTestModel(t, c, "code", "")

	cmd := m.Init()
	_ = findBatchedMsg(cmd, func(tea.Msg) bool { return false })
	if c.fetchCalls != 0 {
		t.Errorf("attention fetch was called %d times for a non-email agent; want 0", c.fetchCalls)
	}
}

func TestFetchAttentionSkippedWhenClientLacksInterface(t *testing.T) {
	// nullClient does not implement client.AttentionFetcher (the subprocess-
	// mode case) -- Init() must not panic and must simply not fetch.
	c := &nullClient{}
	m := newAttentionTestModel(t, c, "email", "")

	cmd := m.fetchAttention()
	if cmd != nil {
		t.Fatal("fetchAttention() returned a non-nil Cmd for a client without AttentionFetcher")
	}
}

func TestAttentionFetchedMsgAppendsCardMessage(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.Update(attentionFetchedMsg{data: json.RawMessage(sampleAttentionJSON)})
	m = updated.(ChatModel)

	if len(m.messages) != 1 {
		t.Fatalf("got %d messages, want 1", len(m.messages))
	}
	got := m.messages[0]
	if got.Role != RoleCard {
		t.Errorf("Role = %v, want RoleCard", got.Role)
	}
	if got.Render != "email_attention" {
		t.Errorf("Render = %q, want email_attention", got.Render)
	}
	if string(got.Data) != sampleAttentionJSON {
		t.Errorf("Data = %s, want %s", got.Data, sampleAttentionJSON)
	}
}

// ---------------------------------------------------------------------------
// #2639 -- turn ordering. A fetch that resolves while a turn is in flight
// must be held, not spliced between the question and its reply, and must
// never be silently lost however the turn ends.
// ---------------------------------------------------------------------------

// hasAttentionCard reports whether an email_attention RoleCard message is
// anywhere in the transcript.
func hasAttentionCard(m ChatModel) bool {
	for _, msg := range m.messages {
		if msg.Role == RoleCard && msg.Render == "email_attention" {
			return true
		}
	}
	return false
}

func TestAttentionFetchResolvingMidTurnIsBufferedThenAppendedAfterReply(t *testing.T) {
	m, _ := newTestModel(t)

	// The user starts a turn before the on-open attention fetch has resolved.
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)

	// The fetch resolves now, mid-turn -- it must not land between the
	// question just asked and its answer (#2639).
	updated2, _ := m.Update(attentionFetchedMsg{data: json.RawMessage(sampleAttentionJSON)})
	m = updated2.(ChatModel)

	if len(m.messages) != 1 {
		t.Fatalf("attention card landed mid-turn instead of being buffered; got %d messages: %+v", len(m.messages), m.messages)
	}
	if m.pendingAttention == nil {
		t.Fatal("fetch result was dropped instead of buffered")
	}

	// The turn completes.
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "here is your triage"})

	if len(m.messages) != 3 {
		t.Fatalf("got %d messages, want 3 ([User, Assistant, Card]): %+v", len(m.messages), m.messages)
	}
	wantRoles := []MessageRole{RoleUser, RoleAssistant, RoleCard}
	for i, want := range wantRoles {
		if m.messages[i].Role != want {
			t.Errorf("messages[%d].Role = %v, want %v", i, m.messages[i].Role, want)
		}
	}
	if m.messages[2].Render != "email_attention" {
		t.Errorf("messages[2].Render = %q, want email_attention -- the buffered card must not be lost", m.messages[2].Render)
	}
	if m.pendingAttention != nil {
		t.Error("pendingAttention was not cleared after draining")
	}
}

func TestPendingAttentionDrainedOnCtrlCCancel(t *testing.T) {
	// Ctrl+C ends the turn without ever reaching CanonicalFinalEvent or
	// doneMsg -- a drain hooked only on the happy path would orphan the
	// buffered card here (#2631 reflection C2).
	m, _ := newTestModel(t)
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	m.pendingAttention = json.RawMessage(sampleAttentionJSON)

	updated2, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC})
	m = updated2.(ChatModel)

	if !hasAttentionCard(m) {
		t.Errorf("buffered attention card was lost on Ctrl+C cancel; messages: %+v", m.messages)
	}
	if m.pendingAttention != nil {
		t.Error("pendingAttention was not cleared after draining")
	}
}

func TestPendingAttentionDrainedOnEscCancel(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	m.pendingAttention = json.RawMessage(sampleAttentionJSON)

	updated2, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated2.(ChatModel)

	if !hasAttentionCard(m) {
		t.Errorf("buffered attention card was lost on Esc cancel; messages: %+v", m.messages)
	}
}

func TestPendingAttentionDrainedOnErrMsg(t *testing.T) {
	// A transport-level error (e.g. the POST that starts a turn fails) also
	// ends the turn without reaching CanonicalFinalEvent or doneMsg.
	m, _ := newTestModel(t)
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	m.pendingAttention = json.RawMessage(sampleAttentionJSON)

	updated2, _ := m.Update(errMsg{err: errors.New("transport dropped")})
	m = updated2.(ChatModel)

	if !hasAttentionCard(m) {
		t.Errorf("buffered attention card was lost on errMsg; messages: %+v", m.messages)
	}
}

func TestAttentionFetchAppendsImmediatelyWhenNotStreaming(t *testing.T) {
	// With no user query in flight (the #2582 on-open case), the fetch
	// resolving must still render right away -- it must not regress into
	// always buffering.
	m, _ := newTestModel(t)
	if m.streaming {
		t.Fatal("test setup: model must start out not streaming")
	}

	updated, _ := m.Update(attentionFetchedMsg{data: json.RawMessage(sampleAttentionJSON)})
	m = updated.(ChatModel)

	if !hasAttentionCard(m) {
		t.Fatal("attention card did not render immediately when no turn was in flight")
	}
	if m.pendingAttention != nil {
		t.Error("nothing should be buffered when the fetch resolves outside a turn")
	}
}

// ---------------------------------------------------------------------------
// #2631 -- one coherent surface. When a pre-scan card has already rendered
// this session, the attention card is redundant (both describe "what needs
// you" from different taxonomies over roughly the same inbox state) and is
// suppressed rather than shown a second time.
// ---------------------------------------------------------------------------

const preScanCardForSuppressionTest = `{
  "kind": "email_pre_scan",
  "urgent": [
    {"message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs","why":"urgent"}
  ],
  "actionable": [],
  "suggested_archives": [],
  "needs_review": [],
  "totals": {"urgent": 1, "actionable": 0, "informational": 0, "suggested_archives": 0, "needs_review": 0}
}`

func TestAttentionCardSuppressedWhenPreScanAlreadyRenderedThisSession(t *testing.T) {
	m, _ := newTestModel(t)
	m.messages = append(m.messages, Message{
		Role:   RoleCard,
		Render: "email_pre_scan",
		Data:   json.RawMessage(preScanCardForSuppressionTest),
	})

	updated, _ := m.Update(attentionFetchedMsg{data: json.RawMessage(sampleAttentionJSON)})
	m = updated.(ChatModel)

	if hasAttentionCard(m) {
		t.Errorf("attention card rendered even though a pre-scan card already covered this session; messages: %+v", m.messages)
	}
	if len(m.messages) != 1 {
		t.Errorf("expected only the pre-scan card to remain, got %d messages: %+v", len(m.messages), m.messages)
	}
}

// TestOverlappingTurnRendersEachSharedSubjectOnce is the #2631 "no message id
// twice" acceptance criterion, read literally off the issue: an attention
// envelope and a pre-scan envelope sharing a message must not both draw it.
//
// The chosen fix (plan's Adversarial Reflection, Option B) suppresses the
// WHOLE attention card once a pre-scan card has rendered, rather than
// merging the two card types into one combined render. That means the
// shared subject renders exactly once (via the surviving pre-scan card) and
// the pre-scan-only subject is untouched -- but an attention-only subject
// does not render AT ALL in this turn, because the whole card it lived in
// was suppressed, not merged.
//
// That is the design's own documented trade-off ("what B gives up" in the
// plan), not a bug this test papers over: it is asserted here explicitly,
// both ways, rather than claiming a per-item merge that was not built.
func TestOverlappingTurnRendersEachSharedSubjectOnceAndDropsAttentionOnlyContent(t *testing.T) {
	m, _ := newTestModel(t)
	m.width, m.height = 100, 30

	const preScanWithSharedAndUnique = `{
	  "kind": "email_pre_scan",
	  "urgent": [
	    {"message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs","why":"urgent"}
	  ],
	  "actionable": [
	    {"message_id":"MSG_B","sender":"b@example.com","subject":"PreScanOnlySubject","why":"needs a reply"}
	  ],
	  "suggested_archives": [],
	  "needs_review": [],
	  "totals": {"urgent": 1, "actionable": 1, "informational": 0, "suggested_archives": 0, "needs_review": 0}
	}`
	m.messages = append(m.messages, Message{
		Role:   RoleCard,
		Render: "email_pre_scan",
		Data:   json.RawMessage(preScanWithSharedAndUnique),
	})

	const attentionWithSharedAndUnique = `{
	  "kind":"email_attention",
	  "items":[
	    {"kind":"waiting_on_you","message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs","why":"waiting"},
	    {"kind":"action_item","message_id":"MSG_C","sender":"c@example.com","subject":"AttentionOnlySubject","why":"open item"}
	  ],
	  "coverage":{"scanned":2,"total_unread":2},
	  "generated_at":"x","cache_age_seconds":0.0,"stale":false
	}`
	updated, _ := m.Update(attentionFetchedMsg{data: json.RawMessage(attentionWithSharedAndUnique)})
	m = updated.(ChatModel)

	var sb strings.Builder
	for i := range m.messages {
		sb.WriteString(m.renderMessage(&m.messages[i]))
		sb.WriteString("\n")
	}
	rendered := sb.String()
	t.Logf("\n%s", rendered)

	if n := strings.Count(rendered, "F-Bombs"); n != 1 {
		t.Errorf("shared subject rendered %d times, want exactly 1", n)
	}
	if !strings.Contains(rendered, "PreScanOnlySubject") {
		t.Error("the surviving pre-scan card lost its own unique content")
	}
	if strings.Contains(rendered, "AttentionOnlySubject") {
		t.Error("attention-only subject rendered even though the whole attention card should be suppressed -- if this now passes, re-check whether suppression narrowed to per-item and update this test's assumptions")
	}
}

func TestAttentionFetchFailedMsgAppendsStatusNotError(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.Update(attentionFetchFailedMsg{err: errors.New("no mailbox connected")})
	m = updated.(ChatModel)

	if len(m.messages) != 1 {
		t.Fatalf("got %d messages, want 1", len(m.messages))
	}
	got := m.messages[0]
	// A failed best-effort side-channel read must not read as a turn-ending
	// error (RoleError renders in the error panel and sets m.err) -- it's a
	// status note the user can act on (connect a mailbox) or ignore.
	if got.Role != RoleStatus {
		t.Errorf("Role = %v, want RoleStatus", got.Role)
	}
	want := fmt.Sprintf("[!] attention view unavailable: %v", errors.New("no mailbox connected"))
	if got.Content != want {
		t.Errorf("Content = %q, want %q", got.Content, want)
	}
}
