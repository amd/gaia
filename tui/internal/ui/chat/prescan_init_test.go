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

// fakePreScanClient satisfies both client.AgentClient and
// client.PreScanFetcher, so Init()'s on-open fetch (#2743) has something to
// type-assert against.
type fakePreScanClient struct {
	nullClient
	data       json.RawMessage
	err        error
	fetchCalls int
}

func (f *fakePreScanClient) FetchPreScan(context.Context) (json.RawMessage, error) {
	f.fetchCalls++
	if f.err != nil {
		return nil, f.err
	}
	return f.data, nil
}

const samplePreScanJSON = `{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":3,"needs_you":[],"needs_you_total":0,"bulk":{"count":0,"filter_tests":[]}}`

func newPreScanTestModel(t *testing.T, c client.AgentClient, agentID, initialQuery string) ChatModel {
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

func TestNoPreScanFetchOnOpenForEmailAgent(t *testing.T) {
	// Opening the agent must cost nothing: the on-open pre-scan spent a Gmail
	// scan before the user had asked for anything, and showed a shallower
	// version of what "triage my inbox" answers a moment later.
	c := &fakePreScanClient{data: json.RawMessage(samplePreScanJSON)}
	m := newPreScanTestModel(t, c, "email", "")

	cmd := m.Init()
	found := findBatchedMsg(cmd, func(msg tea.Msg) bool {
		_, ok := msg.(preScanFetchedMsg)
		return ok
	})
	if found != nil {
		t.Fatal("Init() dispatched a pre-scan fetch on open; the email agent must open with an empty transcript")
	}
	if c.fetchCalls != 0 {
		t.Errorf("pre-scan fetch was called %d times on open; want 0", c.fetchCalls)
	}
}

func TestFetchPreScanSkippedWhenInitialQueryPresent(t *testing.T) {
	// A launch-with-query must never race the pre-scan fetch against the
	// answer the user is actually waiting on.
	c := &fakePreScanClient{data: json.RawMessage(samplePreScanJSON)}
	m := newPreScanTestModel(t, c, "email", "what's in my inbox?")

	cmd := m.Init()
	_ = findBatchedMsg(cmd, func(tea.Msg) bool { return false }) // drain, ignore result
	if c.fetchCalls != 0 {
		t.Errorf("pre-scan fetch was called %d times; want 0 when an initial query is present", c.fetchCalls)
	}
}

// newPreScanTestModel above hand-sets m.agentID after construction, which
// would mask a regression in how RunAgent actually builds the model. This
// drives the real constructor RunAgent calls, with a display name that
// differs in case from the id, the way the catalog's real "email"/"Email"
// entry does.
func TestNoPreScanFetchViaDirectCLIConstruction(t *testing.T) {
	c := &fakePreScanClient{data: json.RawMessage(samplePreScanJSON)}
	m := NewChatModelForCatalogAgent(c, "email", "Email", false)
	m.width, m.height = 100, 30

	cmd := m.Init()
	found := findBatchedMsg(cmd, func(msg tea.Msg) bool {
		_, ok := msg.(preScanFetchedMsg)
		return ok
	})
	if found != nil {
		t.Fatal("the direct-CLI construction path (RunAgent) still fetches a pre-scan on open")
	}
}

func TestFetchPreScanSkippedForNonEmailAgent(t *testing.T) {
	c := &fakePreScanClient{data: json.RawMessage(samplePreScanJSON)}
	m := newPreScanTestModel(t, c, "code", "")

	cmd := m.Init()
	_ = findBatchedMsg(cmd, func(tea.Msg) bool { return false })
	if c.fetchCalls != 0 {
		t.Errorf("pre-scan fetch was called %d times for a non-email agent; want 0", c.fetchCalls)
	}
}

// The id gate used to fail silently: an agent whose client could serve the
// pre-scan view but whose agentID happened not to match got no fetch and no
// explanation. preScanGateMismatch names that condition so it can be
// logged instead of swallowed.
func TestPreScanGateMismatchDetectsFetcherClientWithWrongAgentID(t *testing.T) {
	cases := []struct {
		name    string
		agentID string
		client  client.AgentClient
		want    bool
	}{
		{"matching id, fetcher client", "email", &fakePreScanClient{}, false},
		{"mismatched id, fetcher client", "code", &fakePreScanClient{}, true},
		{"mismatched id, no fetcher", "code", &nullClient{}, false},
		{"matching id, no fetcher", "email", &nullClient{}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m := newPreScanTestModel(t, tc.client, tc.agentID, "")
			if got := m.preScanGateMismatch(); got != tc.want {
				t.Errorf("preScanGateMismatch() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestFetchPreScanSkippedWhenClientLacksInterface(t *testing.T) {
	// nullClient does not implement client.PreScanFetcher (the subprocess-
	// mode case) -- Init() must not panic and must simply not fetch.
	c := &nullClient{}
	m := newPreScanTestModel(t, c, "email", "")

	cmd := m.fetchPreScan()
	if cmd != nil {
		t.Fatal("fetchPreScan() returned a non-nil Cmd for a client without PreScanFetcher")
	}
}

func TestPreScanFetchedMsgAppendsCardMessage(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.Update(preScanFetchedMsg{data: json.RawMessage(samplePreScanJSON)})
	m = updated.(ChatModel)

	if len(m.messages) != 1 {
		t.Fatalf("got %d messages, want 1", len(m.messages))
	}
	got := m.messages[0]
	if got.Role != RoleCard {
		t.Errorf("Role = %v, want RoleCard", got.Role)
	}
	if got.Render != "email_pre_scan" {
		t.Errorf("Render = %q, want email_pre_scan", got.Render)
	}
	if got.Identity != preScanCardIdentity {
		t.Errorf("Identity = %q, want %q", got.Identity, preScanCardIdentity)
	}
	if string(got.Data) != samplePreScanJSON {
		t.Errorf("Data = %s, want %s", got.Data, samplePreScanJSON)
	}
}

func TestPreScanDegradedMsgAppendsStatusNotice(t *testing.T) {
	// #2743: a peer whose contract predates needs_you must never render the
	// confident empty card -- an honest status note instead.
	m, _ := newTestModel(t)
	updated, _ := m.Update(preScanDegradedMsg{notice: "the installed 'email' agent speaks contract 2.6..."})
	m = updated.(ChatModel)

	if len(m.messages) != 1 {
		t.Fatalf("got %d messages, want 1", len(m.messages))
	}
	got := m.messages[0]
	if got.Role != RoleStatus {
		t.Errorf("Role = %v, want RoleStatus", got.Role)
	}
	if !strings.Contains(got.Content, "contract 2.6") {
		t.Errorf("Content = %q, want it to carry the notice text", got.Content)
	}
}

func TestFetchPreScanReturnsDegradedMsgForOldPeer(t *testing.T) {
	c := &fakePreScanClient{err: &client.ErrPreScanContractTooOld{AgentID: "email", Version: "2.6"}}
	m := newPreScanTestModel(t, c, "email", "")

	cmd := m.fetchPreScan()
	found := findBatchedMsg(cmd, func(msg tea.Msg) bool {
		_, ok := msg.(preScanDegradedMsg)
		return ok
	})
	if found == nil {
		t.Fatal("fetchPreScan() did not translate ErrPreScanContractTooOld into preScanDegradedMsg")
	}
}

// ---------------------------------------------------------------------------
// #2639 -- turn ordering. A fetch that resolves while a turn is in flight
// must be held, not spliced between the question and its reply, and must
// never be silently lost however the turn ends.
// ---------------------------------------------------------------------------

// hasPreScanCard reports whether an email_pre_scan RoleCard message is
// anywhere in the transcript.
func hasPreScanCard(m ChatModel) bool {
	for _, msg := range m.messages {
		if msg.Role == RoleCard && msg.Render == "email_pre_scan" {
			return true
		}
	}
	return false
}

func TestPreScanFetchResolvingMidTurnIsBufferedThenAppendedAfterReply(t *testing.T) {
	m, _ := newTestModel(t)

	// The user starts a turn before the on-open pre-scan fetch has resolved.
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)

	// The fetch resolves now, mid-turn -- it must not land between the
	// question just asked and its answer (#2639).
	updated2, _ := m.Update(preScanFetchedMsg{data: json.RawMessage(samplePreScanJSON)})
	m = updated2.(ChatModel)

	if len(m.messages) != 1 {
		t.Fatalf("pre-scan card landed mid-turn instead of being buffered; got %d messages: %+v", len(m.messages), m.messages)
	}
	if m.pendingPreScan == nil {
		t.Fatal("fetch result was dropped instead of buffered")
	}

	// The turn completes with no tool_result of its own -- the buffered
	// on-open snapshot is the only card data available.
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
	if m.messages[2].Render != "email_pre_scan" {
		t.Errorf("messages[2].Render = %q, want email_pre_scan -- the buffered card must not be lost", m.messages[2].Render)
	}
	if m.pendingPreScan != nil {
		t.Error("pendingPreScan was not cleared after draining")
	}
}

// #2743 checkpoint review -- the clobber fix. When the SAME turn's own
// typed tool_result already drew a fresher pre-scan card, the buffered
// on-open snapshot must be discarded, not drained over it: draining would
// silently replace fresher data with a shallower snapshot taken before the
// turn even started.
func TestPreScanBufferedFetchDiscardedWhenTurnAlreadyRenderedFresherCard(t *testing.T) {
	m, _ := newTestModel(t)

	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)

	// The on-open fetch resolves mid-turn (buffered, per #2639).
	staleData := json.RawMessage(`{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":1,"needs_you":[],"needs_you_total":0,"bulk":{"count":0,"filter_tests":[]}}`)
	updated2, _ := m.Update(preScanFetchedMsg{data: staleData})
	m = updated2.(ChatModel)
	if m.pendingPreScan == nil {
		t.Fatal("test setup: fetch result must be buffered before the turn's own tool_result arrives")
	}

	// The turn's OWN typed tool_result produces a fresher card first.
	freshData := json.RawMessage(`{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":9,"needs_you":[],"needs_you_total":0,"bulk":{"count":0,"filter_tests":[]}}`)
	m = feed(t, m, event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "pre_scan_inbox",
		Render: "email_pre_scan", Data: freshData,
	})
	if !m.preScanRenderedThisTurn {
		t.Fatal("test setup: preScanRenderedThisTurn must be set once the typed tool_result renders")
	}

	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "here is your triage"})

	var cards []Message
	for _, msg := range m.messages {
		if msg.Role == RoleCard && msg.Render == "email_pre_scan" {
			cards = append(cards, msg)
		}
	}
	if len(cards) != 1 {
		t.Fatalf("got %d pre-scan cards, want exactly 1 (updated in place): %+v", len(cards), cards)
	}
	if string(cards[0].Data) != string(freshData) {
		t.Errorf("surviving card data = %s, want the turn's own fresher data %s -- the buffered stale snapshot clobbered it", cards[0].Data, freshData)
	}
	if m.pendingPreScan != nil {
		t.Error("pendingPreScan was not cleared after being discarded")
	}
}

func TestPendingPreScanDrainedOnCtrlCCancel(t *testing.T) {
	// Ctrl+C ends the turn without ever reaching CanonicalFinalEvent or
	// doneMsg -- a drain hooked only on the happy path would orphan the
	// buffered card here (#2631 reflection C2).
	m, _ := newTestModel(t)
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	m.pendingPreScan = json.RawMessage(samplePreScanJSON)

	updated2, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC})
	m = updated2.(ChatModel)

	if !hasPreScanCard(m) {
		t.Errorf("buffered pre-scan card was lost on Ctrl+C cancel; messages: %+v", m.messages)
	}
	if m.pendingPreScan != nil {
		t.Error("pendingPreScan was not cleared after draining")
	}
}

func TestPendingPreScanDrainedOnEscCancel(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	m.pendingPreScan = json.RawMessage(samplePreScanJSON)

	updated2, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated2.(ChatModel)

	if !hasPreScanCard(m) {
		t.Errorf("buffered pre-scan card was lost on Esc cancel; messages: %+v", m.messages)
	}
}

func TestPendingPreScanDrainedOnErrMsg(t *testing.T) {
	// A transport-level error (e.g. the POST that starts a turn fails) also
	// ends the turn without reaching CanonicalFinalEvent or doneMsg.
	m, _ := newTestModel(t)
	updated, _ := m.Update(sendQueryMsg{query: "triage my inbox"})
	m = updated.(ChatModel)
	m.pendingPreScan = json.RawMessage(samplePreScanJSON)

	// turnSeq must match the live turn's (see errMsg's doc comment) or this
	// delivery is (correctly) dropped as stale.
	updated2, _ := m.Update(errMsg{err: errors.New("transport dropped"), turnSeq: m.turnSeq})
	m = updated2.(ChatModel)

	if !hasPreScanCard(m) {
		t.Errorf("buffered pre-scan card was lost on errMsg; messages: %+v", m.messages)
	}
}

func TestPreScanFetchAppendsImmediatelyWhenNotStreaming(t *testing.T) {
	// With no user query in flight (the #2743 on-open case), the fetch
	// resolving must still render right away -- it must not regress into
	// always buffering.
	m, _ := newTestModel(t)
	if m.streaming {
		t.Fatal("test setup: model must start out not streaming")
	}

	updated, _ := m.Update(preScanFetchedMsg{data: json.RawMessage(samplePreScanJSON)})
	m = updated.(ChatModel)

	if !hasPreScanCard(m) {
		t.Fatal("pre-scan card did not render immediately when no turn was in flight")
	}
	if m.pendingPreScan != nil {
		t.Error("nothing should be buffered when the fetch resolves outside a turn")
	}
}

func TestFetchPreScanFailedMsgAppendsStatusNotError(t *testing.T) {
	m, _ := newTestModel(t)
	updated, _ := m.Update(preScanFetchFailedMsg{err: errors.New("no mailbox connected")})
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
	want := fmt.Sprintf("[!] inbox pre-scan unavailable: %v", errors.New("no mailbox connected"))
	if got.Content != want {
		t.Errorf("Content = %q, want %q", got.Content, want)
	}
}

// ---------------------------------------------------------------------------
// #2743 -- update in place, by identity. Two successive results yield
// exactly one card message, carrying the newer data; an in-place update
// survives /clear without panicking or corrupting; cardCache invalidates
// on data change, not only on resize.
// ---------------------------------------------------------------------------

func TestTwoSuccessivePreScanResultsYieldOneCardWithNewerData(t *testing.T) {
	m, _ := newTestModel(t)

	first := json.RawMessage(`{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":1,"needs_you":[],"needs_you_total":0,"bulk":{"count":0,"filter_tests":[]}}`)
	updated, _ := m.Update(preScanFetchedMsg{data: first})
	m = updated.(ChatModel)

	second := json.RawMessage(`{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":9,"needs_you":[],"needs_you_total":0,"bulk":{"count":0,"filter_tests":[]}}`)
	updated2, _ := m.Update(preScanFetchedMsg{data: second})
	m = updated2.(ChatModel)

	var cards []Message
	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			cards = append(cards, msg)
		}
	}
	if len(cards) != 1 {
		t.Fatalf("got %d card messages, want exactly 1: %+v", len(cards), cards)
	}
	if string(cards[0].Data) != string(second) {
		t.Errorf("surviving card data = %s, want the newer %s", cards[0].Data, second)
	}
}

func TestPreScanInPlaceUpdateAfterClearDoesNotPanicOrCorrupt(t *testing.T) {
	m, _ := newTestModel(t)

	updated, _ := m.Update(preScanFetchedMsg{data: json.RawMessage(samplePreScanJSON)})
	m = updated.(ChatModel)
	if len(m.messages) != 1 {
		t.Fatalf("test setup: want 1 message before /clear, got %d", len(m.messages))
	}

	// /clear sets m.messages = nil (model.go's KeyEnter "/clear" handling).
	// A card update keyed on a stale INDEX would panic or silently corrupt
	// an unrelated message here; identity-based lookup must not.
	m.messages = nil

	func() {
		defer func() {
			if r := recover(); r != nil {
				t.Fatalf("upsertPreScanCard panicked after /clear: %v", r)
			}
		}()
		m.upsertPreScanCard(json.RawMessage(samplePreScanJSON))
	}()

	if len(m.messages) != 1 {
		t.Fatalf("got %d messages after re-upserting post-clear, want 1", len(m.messages))
	}
	if m.messages[0].Role != RoleCard || m.messages[0].Identity != preScanCardIdentity {
		t.Errorf("messages[0] = %+v, want the re-appended pre-scan card", m.messages[0])
	}
}

func TestPreScanCardCacheInvalidatesOnDataChangeNotOnlyOnResize(t *testing.T) {
	m, _ := newTestModel(t)

	first := json.RawMessage(`{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":1,"needs_you":[],"needs_you_total":0,"bulk":{"count":0,"filter_tests":[]}}`)
	updated, _ := m.Update(preScanFetchedMsg{data: first})
	m = updated.(ChatModel)

	// Render at a fixed width to populate cardCache.
	const w = 76
	rendered1 := m.messages[0].renderCard(w)
	if !strings.Contains(rendered1, "1 inbox messages scanned") {
		t.Fatalf("first render missing the first payload's scan count:\n%s", rendered1)
	}

	second := json.RawMessage(`{"kind":"email_pre_scan","urgent":[],"actionable":[],"informational_count":0,"suggested_archives":[],"suggested_drafts":[],"needs_review":[],"scanned":9,"needs_you":[],"needs_you_total":0,"bulk":{"count":0,"filter_tests":[]}}`)
	updated2, _ := m.Update(preScanFetchedMsg{data: second})
	m = updated2.(ChatModel)

	// SAME width as before -- if cardCache only invalidated on a width
	// change, this would still return rendered1's stale content.
	rendered2 := m.messages[0].renderCard(w)
	if strings.Contains(rendered2, "1 inbox messages scanned") || !strings.Contains(rendered2, "9 inbox messages scanned") {
		t.Errorf("render at the same width served stale cached content after an in-place data update:\n%s", rendered2)
	}
}
