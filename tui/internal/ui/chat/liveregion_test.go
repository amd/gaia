package chat

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// A triage turn touches dozens of messages over 60-90s on a local model. Two
// static lines are indistinguishable from a hang, so the work log keeps a
// bounded window of what actually happened.
func TestLiveRegionKeepsBoundedHistory(t *testing.T) {
	m := newTestChat(t)
	for _, tool := range []string{"list_inbox", "get_message", "classify", "apply_prefs", "pre_scan_inbox", "summarize"} {
		m = feed(t, m, event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool})
	}

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)

	lines := strings.Split(out, "\n")
	if len(lines) > workLogLines+1 { // +1 for the header line
		t.Errorf("live region is %d lines; must stay bounded at %d + header", len(lines), workLogLines)
	}
	if !strings.Contains(out, "summarize") {
		t.Errorf("live region dropped the most recent tool:\n%s", out)
	}
	// The oldest entry scrolls out rather than the newest being discarded.
	if strings.Contains(out, "list_inbox") {
		t.Errorf("oldest entry should have scrolled out of the bounded window:\n%s", out)
	}
}

// Twenty identical calls collapsed onto one flickering line read as a single
// slow call; the counter is what makes repetition legible as progress.
func TestLiveRegionCollapsesRepeatsWithACounter(t *testing.T) {
	m := newTestChat(t)
	for i := 0; i < 14; i++ {
		m = feed(t, m, event.CanonicalToolCallEvent{
			Type: "tool_call",
			Tool: "triage_message",
			Args: []byte(`{"message_id":"m` + string(rune('a'+i)) + `"}`),
		})
	}

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)

	if !strings.Contains(out, "triage_message") || !strings.Contains(out, "x14") {
		t.Errorf("14 calls to one tool did not collapse to a counter:\n%s", out)
	}
	if n := strings.Count(out, "triage_message"); n != 1 {
		t.Errorf("triage_message appears %d times; repeats must fold into one line:\n%s", n, out)
	}
}

func TestLiveRegionShowsStillWorkingAfterTwentySeconds(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m, event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"})

	if strings.Contains(ansi.Strip(m.renderLiveRegion()), "still working") {
		t.Error("the still-working line must not appear immediately")
	}

	m.queryStart = time.Now().Add(-stillWorkingAfter - time.Second)
	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	if !strings.Contains(out, "still working") || !strings.Contains(out, "60-90s") {
		t.Errorf("after %s the live region must say the wait is expected:\n%s", stillWorkingAfter, out)
	}
}

func TestLiveRegionShowsElapsedTime(t *testing.T) {
	m := newTestChat(t)
	m.queryStart = time.Now().Add(-95 * time.Second)
	m = feed(t, m, event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"})

	if out := ansi.Strip(m.renderLiveRegion()); !strings.Contains(out, "1:35") {
		t.Errorf("live region missing an elapsed clock:\n%s", out)
	}
}

// No colour-only signals, and no reliance on an emoji font: success and failure
// have to be readable as plain ASCII.
func TestLiveRegionStatusMarkersAreAsciiNotColourOnly(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "send_email"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "send_email", Data: []byte(`{"ok":false}`)},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_inbox"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "list_inbox", Data: []byte(`{"ok":true}`)},
	)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	if !strings.Contains(out, "[x] send_email") {
		t.Errorf("a failed tool must be marked without relying on colour:\n%s", out)
	}
	if !strings.Contains(out, "[ok] list_inbox") {
		t.Errorf("a succeeded tool must be marked without relying on colour:\n%s", out)
	}
	for _, emoji := range []string{"🔧", "🧠", "🎯", "✓", "✗", "⚠️"} {
		if strings.Contains(out, emoji) {
			t.Errorf("live region still depends on %q rendering correctly:\n%s", emoji, out)
		}
	}
}

func TestCollapseActivityFoldsByToolNameNotArguments(t *testing.T) {
	got := collapseActivity([]ActivityItem{
		{Kind: "step", Content: "Step 1/3"},
		{Kind: "tool", Content: "get_message: m1"},
		{Kind: "tool", Content: "get_message: m2"},
		{Kind: "tool", Content: "get_message: m3"},
		{Kind: "status", Content: "classifying"},
		{Kind: "tool", Content: "get_message: m4"},
	})

	if len(got) != 3 {
		t.Fatalf("collapsed to %d items, want 3: %+v", len(got), got)
	}
	if got[0].Repeat != 2 {
		t.Errorf("first run folded %d repeats, want 2", got[0].Repeat)
	}
	// A non-adjacent recurrence starts a new line — it happened after other work.
	if got[2].Repeat != 0 {
		t.Errorf("the post-status call should start a fresh line, got repeat=%d", got[2].Repeat)
	}
	for _, item := range got {
		if item.Kind == "step" {
			t.Error("step markers belong in the header, not the work log")
		}
	}
}

func TestInitSlashCommandIsGone(t *testing.T) {
	// A slash command that prints "Initializing X..." and does nothing is worse
	// than a missing one. There is no /v1/<agent>/init call wired into the TUI
	// client, so the command is removed rather than left lying.
	m := newTestChat(t)
	m.streaming = false
	m.input.SetValue("/init")

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	after := updated.(ChatModel)

	for _, msg := range after.messages {
		if strings.Contains(msg.Content, "Initializing") {
			t.Errorf("/init still prints a fake progress message: %q", msg.Content)
		}
	}
	// It falls through to a normal query instead of being silently swallowed.
	if len(after.messages) == 0 || after.messages[0].Role != RoleUser {
		t.Error("/init should now be treated as ordinary input, not a special case")
	}
}
