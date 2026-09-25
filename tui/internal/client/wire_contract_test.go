package client

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// stdioWireFixture mirrors tests/fixtures/stdio/gaia_stdio_wire.json, the one
// checked-in copy of the TUI <-> flagship agent stdio protocol. The Python side
// checks the same file (hub/agents/gaia/python/tests/test_stdio_wire_contract.py),
// so a rename on either side fails a test instead of silently dropping answers.
type stdioWireFixture struct {
	Stdin struct {
		QueryKey     string `json:"query_key"`
		ControlKey   string `json:"control_key"`
		ControlVerbs map[string]struct {
			Fields    []string `json:"fields"`
			SentByTUI bool     `json:"sent_by_tui"`
		} `json:"control_verbs"`
		Decisions      []string `json:"decisions"`
		QuerySentinels struct {
			ClearConversation struct {
				Query     string `json:"query"`
				AckAnswer string `json:"ack_answer"`
			} `json:"clear_conversation"`
			MemoryDump struct {
				Query string `json:"query"`
			} `json:"memory_dump"`
		} `json:"query_sentinels"`
	} `json:"stdin"`
}

// loadStdioWireFixture reads the shared fixture from the repo root, three
// levels above tui/internal/client.
func loadStdioWireFixture(t *testing.T) stdioWireFixture {
	t.Helper()
	path := filepath.Join("..", "..", "..", "tests", "fixtures", "stdio", "gaia_stdio_wire.json")
	raw, err := os.ReadFile(path) // #nosec G304 -- fixed relative path to a checked-in fixture
	if err != nil {
		t.Fatalf("could not read the shared stdio wire fixture at %s: %v", path, err)
	}
	var fixture stdioWireFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("could not parse the shared stdio wire fixture: %v", err)
	}
	return fixture
}

type recordingStdin struct{ bytes.Buffer }

func (*recordingStdin) Close() error { return nil }

// startedWithRecorder is a client that believes its child is running and a
// turn is in flight, writing control lines into a buffer instead of a pipe.
func startedWithRecorder() (*SubprocessClient, *recordingStdin) {
	c := NewSubprocessClient("agent", nil, false)
	stdin := &recordingStdin{}
	c.started = true
	c.stdin = stdin
	c.turnDone = make(chan struct{})
	return c, stdin
}

func sortedKeys(m map[string]interface{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func TestStdioEnvelopeKeysMatchTheSharedFixture(t *testing.T) {
	f := loadStdioWireFixture(t)
	if controlKey != f.Stdin.ControlKey {
		t.Errorf("controlKey = %q, fixture says %q", controlKey, f.Stdin.ControlKey)
	}
	if queryKey != f.Stdin.QueryKey {
		t.Errorf("queryKey = %q, fixture says %q", queryKey, f.Stdin.QueryKey)
	}
}

func TestPermissionDecisionsMatchTheSharedFixture(t *testing.T) {
	f := loadStdioWireFixture(t)
	got := []string{string(PermissionAllow), string(PermissionDeny), string(PermissionAlways)}
	want := append([]string(nil), f.Stdin.Decisions...)
	sort.Strings(got)
	sort.Strings(want)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("decisions = %v, fixture says %v", got, want)
	}
}

func TestQuerySentinelsMatchTheSharedFixture(t *testing.T) {
	f := loadStdioWireFixture(t)
	if clearConversationQuery != f.Stdin.QuerySentinels.ClearConversation.Query {
		t.Errorf("clearConversationQuery = %q, fixture says %q",
			clearConversationQuery, f.Stdin.QuerySentinels.ClearConversation.Query)
	}
	if clearConversationAck != f.Stdin.QuerySentinels.ClearConversation.AckAnswer {
		t.Errorf("clearConversationAck = %q, fixture says %q",
			clearConversationAck, f.Stdin.QuerySentinels.ClearConversation.AckAnswer)
	}
	if memoryDumpQuery != f.Stdin.QuerySentinels.MemoryDump.Query {
		t.Errorf("memoryDumpQuery = %q, fixture says %q",
			memoryDumpQuery, f.Stdin.QuerySentinels.MemoryDump.Query)
	}
}

// Validity of the call, not just that one was made: every control line the
// client actually writes must use a verb and field names the agent reads.
func TestEveryControlLineTheClientWritesMatchesTheSharedFixture(t *testing.T) {
	f := loadStdioWireFixture(t)
	senders := map[string]func(*SubprocessClient) error{
		"tool_decision": func(c *SubprocessClient) error {
			return c.RespondToolPermission("confirm-7", PermissionAlways)
		},
		"bypass": func(c *SubprocessClient) error { return c.SetBypassPermissions(true) },
		"cancel": func(c *SubprocessClient) error { return c.Cancel(context.Background()) },
	}

	for verb, spec := range f.Stdin.ControlVerbs {
		if _, ok := senders[verb]; ok != spec.SentByTUI {
			t.Errorf("fixture says sent_by_tui=%t for %q, but this test sends it: %t", spec.SentByTUI, verb, ok)
		}
	}

	for verb, send := range senders {
		t.Run(verb, func(t *testing.T) {
			spec, ok := f.Stdin.ControlVerbs[verb]
			if !ok {
				t.Fatalf("the client sends %q, which the fixture (and so the agent) does not know", verb)
			}
			c, stdin := startedWithRecorder()
			if err := send(c); err != nil {
				t.Fatalf("send %q: %v", verb, err)
			}
			var msg map[string]interface{}
			if err := json.Unmarshal(bytes.TrimSpace(stdin.Bytes()), &msg); err != nil {
				t.Fatalf("control line is not one JSON object: %q: %v", stdin.String(), err)
			}
			if msg[f.Stdin.ControlKey] != verb {
				t.Fatalf("wrote %v under %q, want verb %q", msg, f.Stdin.ControlKey, verb)
			}
			want := append([]string{f.Stdin.ControlKey}, spec.Fields...)
			sort.Strings(want)
			if got := sortedKeys(msg); strings.Join(got, ",") != strings.Join(want, ",") {
				t.Errorf("%q carries fields %v, fixture says %v", verb, got, want)
			}
		})
	}
}
