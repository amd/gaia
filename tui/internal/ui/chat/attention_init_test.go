package chat

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
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
