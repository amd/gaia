package chat

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

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
// #2631 -- shared items render once. The pre-scan and attention cards do not
// share a taxonomy (meeting_request/waiting_on_you/action_item exist only on
// the attention card), so a duplicate is resolved per item, not by
// suppressing whichever card renders second -- that would throw away
// meeting proposals and action items the attention card exists to surface.
// Whole-card suppression only happens as a side effect of every one of a
// card's items turning out to be a duplicate.
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

const attentionWithSharedAndUnique = `{
  "kind":"email_attention",
  "items":[
    {"kind":"waiting_on_you","message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs","why":"waiting"},
    {"kind":"action_item","message_id":"MSG_C","sender":"c@example.com","subject":"AttentionOnlySubject","why":"open item"}
  ],
  "coverage":{"scanned":2,"total_unread":2},
  "generated_at":"x","cache_age_seconds":0.0,"stale":false
}`

const attentionAllDuplicateOfPreScan = `{
  "kind":"email_attention",
  "items":[
    {"kind":"waiting_on_you","message_id":"MSG_A","sender":"a@example.com","subject":"F-Bombs (attention copy)","why":"still waiting"}
  ],
  "coverage":{"scanned":1,"total_unread":1},
  "generated_at":"x","cache_age_seconds":0.0,"stale":false
}`

const attentionActionItemsWithNullMessageID = `{
  "kind":"email_attention",
  "items":[
    {"kind":"action_item","message_id":null,"sender":"a@example.com","subject":"Renew the domain","why":"expires Friday"},
    {"kind":"action_item","message_id":null,"sender":"b@example.com","subject":"Approve the invoice","why":"awaiting sign-off"}
  ],
  "coverage":{"scanned":2,"total_unread":2},
  "generated_at":"x","cache_age_seconds":0.0,"stale":false
}`

// preScanWithNullMessageIDItem's own urgent item also has no message_id --
// the pre-scan card renders first, so this is what proves a missing id from
// an EARLIER card can't poison a later card's missing-id items either: if
// dedup ever keyed on the empty string, this card's own null id would enter
// the turn's seen set and the attention card's null-id items would come out
// looking like duplicates of it.
const preScanWithNullMessageIDItem = `{
  "kind": "email_pre_scan",
  "urgent": [
    {"message_id":null,"sender":"legal@example.com","subject":"Follow up with Legal","why":"urgent"}
  ],
  "actionable": [],
  "suggested_archives": [],
  "needs_review": [],
  "totals": {"urgent": 1, "actionable": 0, "informational": 0, "suggested_archives": 0, "needs_review": 0}
}`

// renderOverlappingTurn drives one real turn where a pre-scan card renders
// mid-stream and an attention fetch resolves during that same turn -- so it
// buffers and drains right after the reply, per #2639 -- the one shape in
// which these two card types can ever land in the same turn. It returns the
// fully rendered, ANSI-stripped transcript, exercising the real
// updateViewport/renderMessage wiring rather than a re-implementation of it.
func renderOverlappingTurn(t *testing.T, preScanData, attentionData json.RawMessage) string {
	t.Helper()
	m, _ := newTestModel(t)
	m.width, m.height = 100, 60
	m.resize()

	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)

	m = feed(t, m, event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "pre_scan_inbox",
		Render: "email_pre_scan", Data: preScanData,
	})

	updated2, _ := m.Update(attentionFetchedMsg{data: attentionData})
	m = updated2.(ChatModel)

	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "here is your triage"})

	return ansi.Strip(m.viewport.View())
}

// #2631 -- when every item an attention card would show is a duplicate of
// something the turn's pre-scan card already rendered, the attention card
// ends up with nothing left to say and is suppressed. This is the only case
// where dropping the whole card is correct, and it falls out of the
// per-item logic rather than a separate whole-card check.
func TestAttentionCardSuppressedWhenEveryItemAlreadyShownByPreScan(t *testing.T) {
	view := renderOverlappingTurn(t,
		json.RawMessage(preScanCardForSuppressionTest),
		json.RawMessage(attentionAllDuplicateOfPreScan))
	t.Logf("\n%s", view)

	if !strings.Contains(view, "F-Bombs") {
		t.Error("the surviving pre-scan card lost its own content")
	}
	if strings.Contains(view, "attention copy") {
		t.Error("the duplicate attention item rendered even though its message_id was already shown by the pre-scan card")
	}
	if strings.Contains(view, "Needs you") {
		t.Error("attention card title rendered even though every one of its items was a duplicate -- the whole card should be suppressed")
	}
}

// TestOverlappingTurnDedupsSharedItemButKeepsBothCardsUniqueContent is the
// #2631 "no message id twice" acceptance criterion, read literally off the
// issue: an attention envelope and a pre-scan envelope sharing a message
// must not both draw it. Unlike the whole-card-suppression design this
// replaces, each card's own unique content survives: the shared subject
// renders exactly once, the pre-scan-only subject is untouched, and the
// attention-only subject (a taxonomy the pre-scan card has no equivalent
// for) still renders too. A section left with nothing after dedup is
// dropped, not shown with a hollow "0" count.
func TestOverlappingTurnDedupsSharedItemButKeepsBothCardsUniqueContent(t *testing.T) {
	view := renderOverlappingTurn(t,
		json.RawMessage(preScanWithSharedAndUnique),
		json.RawMessage(attentionWithSharedAndUnique))
	t.Logf("\n%s", view)

	if n := strings.Count(view, "F-Bombs"); n != 1 {
		t.Errorf("shared subject rendered %d times, want exactly 1", n)
	}
	if !strings.Contains(view, "PreScanOnlySubject") {
		t.Error("the pre-scan card lost its own unique content")
	}
	if !strings.Contains(view, "AttentionOnlySubject") {
		t.Error("the attention card's own unique content was dropped -- per-item dedup must not suppress the whole card when it still has something to say")
	}
	if strings.Contains(view, "WAITING ON YOU") {
		t.Error("the WAITING ON YOU section should have been dropped -- its only item was deduped away, so an empty section must not render")
	}
	if !strings.Contains(view, "ACTION ITEMS") {
		t.Error("the ACTION ITEMS section, whose one item is not a duplicate, should still render")
	}
	if !strings.Contains(view, "Needs you") {
		t.Error("the attention card itself should still render -- it still has real content (the action item)")
	}
}

// #2631 -- action items legitimately carry no message_id (they are not tied
// to one specific message), so an empty id must never be treated as a
// duplicate: not of a real id already in play, not of another empty-id item
// in the very same card, and -- the sharpest case -- not of an empty id an
// EARLIER card in the same turn also carried. The pre-scan fixture here has
// its own null-id item specifically to prove that last case: seen must never
// gain an "" entry that a later card's null-id items could collide with.
func TestAttentionItemsWithNoMessageIDAreNeverDedupedAsDuplicates(t *testing.T) {
	view := renderOverlappingTurn(t,
		json.RawMessage(preScanWithNullMessageIDItem),
		json.RawMessage(attentionActionItemsWithNullMessageID))
	t.Logf("\n%s", view)

	if !strings.Contains(view, "Follow up with Legal") {
		t.Error("the pre-scan card's own null-message_id item was dropped")
	}
	if !strings.Contains(view, "Renew the domain") {
		t.Error("a null-message_id action item was dropped -- an empty id must never be treated as a duplicate")
	}
	if !strings.Contains(view, "Approve the invoice") {
		t.Error("a second null-message_id action item was dropped -- empty ids must not be deduped against each other either")
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
