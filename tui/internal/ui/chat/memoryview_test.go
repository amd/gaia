package chat

import (
	"context"
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
)

// fakeMemoryClient implements client.AgentClient + client.MemoryProvider
// without spawning a subprocess, so submit()/Update() can be driven directly.
type fakeMemoryClient struct {
	dump    client.MemoryDump
	err     error
	fetched int
}

func (f *fakeMemoryClient) Send(context.Context, string) (<-chan interface{}, error) {
	ch := make(chan interface{})
	close(ch)
	return ch, nil
}
func (f *fakeMemoryClient) Close() error { return nil }
func (f *fakeMemoryClient) FetchMemory(context.Context) (client.MemoryDump, error) {
	f.fetched++
	return f.dump, f.err
}

// findMemoryDumpMsg unwraps startMemoryFetch's tea.Batch(spinner.Tick, fetch)
// to find the fetch's own message, ignoring the spinner tick sitting beside it.
func findMemoryDumpMsg(msg tea.Msg) (memoryDumpMsg, bool) {
	if m, ok := msg.(memoryDumpMsg); ok {
		return m, true
	}
	batch, ok := msg.(tea.BatchMsg)
	if !ok {
		return memoryDumpMsg{}, false
	}
	for _, c := range batch {
		if c == nil {
			continue
		}
		if m, ok := findMemoryDumpMsg(c()); ok {
			return m, true
		}
	}
	return memoryDumpMsg{}, false
}

func sampleMemoryDump() client.MemoryDump {
	return client.MemoryDump{
		Available: true,
		Stats: client.MemoryStats{
			TotalKnowledge: 3,
			ByCategory:     map[string]int{"fact": 2, "preference": 1},
			ByContext:      map[string]int{"global": 3},
			SensitiveCount: 1,
			EntityCount:    1,
			AvgConfidence:  0.71,
		},
		Contexts: []client.MemoryContext{{Context: "global", Count: 3}},
		Shown:    3,
		Total:    3,
		Items: []client.MemoryItem{
			{ID: "1", Category: "fact", Content: "likes go", Context: "global", Confidence: 0.8},
			{ID: "2", Category: "fact", Content: "works at AMD", Context: "global", Confidence: 0.6},
			{
				ID: "3", Category: "preference", Content: "wifi password is hunter2",
				Context: "global", Confidence: 0.9, Sensitive: true,
			},
		},
	}
}

// ---------------------------------------------------------------------------
// /memory is dispatched as a command, never sent to the agent as a literal
// question — the switch in submit() takes it before falling through to
// sendQuery, mirroring how /clear and /bypass are tested.
// ---------------------------------------------------------------------------

func TestSlashMemoryIsDispatchedNotSentAsAQuestion(t *testing.T) {
	fc := &fakeMemoryClient{dump: sampleMemoryDump()}
	m := NewChatModel(fc, "gaia", "", false)
	m.width, m.height = 80, 24
	m.resize()

	updated, cmd := m.submit("/memory")
	m = updated.(ChatModel)

	if cmd == nil {
		t.Fatal("/memory returned no command — the fetch never runs")
	}
	dumpMsg, ok := findMemoryDumpMsg(cmd())
	if !ok {
		t.Fatalf("/memory did not fetch memory: got %T", cmd())
	}
	if dumpMsg.err != nil {
		t.Fatalf("unexpected fetch error: %v", dumpMsg.err)
	}
	if fc.fetched != 1 {
		t.Errorf("FetchMemory called %d times, want 1", fc.fetched)
	}
	for _, msg := range m.messages {
		if msg.Role == RoleUser && msg.Content == "/memory" {
			t.Error("/memory was appended to the transcript as a user question")
		}
	}
	if !m.memoryLoading {
		t.Error("submitting /memory did not enter the loading state")
	}
}

func TestMemoryDumpResultIsShownNotSentToChat(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.update(memoryDumpMsg{dump: sampleMemoryDump()})
	m = updated.(ChatModel)

	if m.memoryLoading {
		t.Error("memoryLoading stayed true after the dump arrived")
	}
	if m.memoryView == nil {
		t.Fatal("a successful dump did not populate memoryView")
	}
	if len(m.messages) != 0 {
		t.Errorf("the dump was appended to the chat transcript: %+v", m.messages)
	}
}

func TestMemoryFetchFailureIsReportedNotSilent(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.update(memoryDumpMsg{err: errors.New("agent closed the connection")})
	m = updated.(ChatModel)

	if m.memoryView != nil {
		t.Error("a failed fetch still populated memoryView")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || !strings.Contains(last.Content, "agent closed the connection") {
		t.Errorf("failure was not reported: %+v", last)
	}
}

func TestMemoryUnsupportedClientReportsPlainly(t *testing.T) {
	m := newTestChat(t) // nullClient: no MemoryProvider

	updated, cmd := m.submit("/memory")
	m = updated.(ChatModel)

	if cmd != nil {
		t.Error("an unsupported client should not start a fetch")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || !strings.Contains(last.Content, "/memory") {
		t.Errorf("no plain explanation for an unsupported client: %+v", last)
	}
}

// ---------------------------------------------------------------------------
// /help lists it
// ---------------------------------------------------------------------------

func TestHelpListsMemoryCommand(t *testing.T) {
	// helpoverlay.go's chatHelpText is exercised end to end via the root
	// package's RenderHelpOverlay in its own tests; here we only need proof
	// that /help stays a recognised local command (it must not become a turn).
	m := newTestChat(t)
	m.streaming = false

	_, cmd := m.submit("/help")
	if cmd == nil {
		t.Fatal("/help returned no command")
	}
	if _, ok := cmd().(ToggleHelpMsg); !ok {
		t.Fatalf("/help no longer toggles the help overlay: %T", cmd())
	}
}

// ---------------------------------------------------------------------------
// Rendering stays inside the pane width
// ---------------------------------------------------------------------------

func TestMemoryViewNeverExceedsThePaneWidth(t *testing.T) {
	dump := sampleMemoryDump()
	dump.Items = append(dump.Items, client.MemoryItem{
		ID: "4", Category: "note", Entity: "person:kalin", Context: "work", Confidence: 0.55,
		Content: strings.Repeat("a very long note about the user that keeps going ", 20),
	})

	for _, width := range []int{40, 80, 120} {
		out := ansi.Strip(renderMemoryView(dump, width))
		for i, line := range strings.Split(out, "\n") {
			if w := ansi.StringWidth(line); w > width {
				t.Errorf("width=%d: line %d is %d cols wide: %q", width, i, w, line)
			}
		}
	}
}

func TestMemoryViewReportsUnavailablePlainly(t *testing.T) {
	dump := client.MemoryDump{Available: false, Reason: "Lemonade is not reachable at 127.0.0.1:13305."}

	// Case-insensitive: the panel's band sets its title in caps.
	out := strings.ToLower(ansi.Strip(renderMemoryView(dump, 80)))
	if !strings.Contains(out, "unavailable") {
		t.Errorf("does not say memory is unavailable: %q", out)
	}
	if !strings.Contains(out, "lemonade is not reachable") {
		t.Errorf("dropped the actionable reason: %q", out)
	}
}

func TestMemoryViewGroupsByCategory(t *testing.T) {
	out := ansi.Strip(renderMemoryView(sampleMemoryDump(), 80))
	if !strings.Contains(out, "fact") || !strings.Contains(out, "preference") {
		t.Errorf("categories are not visibly grouped: %q", out)
	}
	if !strings.Contains(out, "hunter2") {
		t.Error("a sensitive item was hidden instead of shown — this view exists to surface it")
	}
}

// ---------------------------------------------------------------------------
// Esc dismisses without quitting (idleesc_test.go pins the same invariant for
// the ordinary idle case — this pins it for the /memory overlay specifically).
// ---------------------------------------------------------------------------

func TestEscDismissesMemoryViewWithoutQuitting(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	dump := sampleMemoryDump()
	m.memoryView = &dump

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if quits(cmd) {
		t.Fatal("Esc on the memory view quit the app")
	}
	if m.memoryView != nil {
		t.Error("Esc did not dismiss the memory view")
	}
}

func TestEscCancelsAnInFlightMemoryFetch(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m.memoryLoading = true
	cancelled := false
	m.memoryCancelFn = func() { cancelled = true }

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if quits(cmd) {
		t.Fatal("Esc while loading memory quit the app")
	}
	if !cancelled {
		t.Error("Esc did not cancel the in-flight fetch")
	}
	if m.memoryLoading {
		t.Error("Esc did not clear the loading state")
	}
}

// A second, ordinary idle Esc (nothing memory-related active) must still not
// quit — regression guard alongside idleesc_test.go for this file's changes
// to handleKey's top.
func TestOrdinaryIdleEscStillDoesNotQuitAfterMemoryChanges(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m.fromHub = false

	_, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})

	if quits(cmd) {
		t.Fatal("idle Esc quit the app")
	}
}
