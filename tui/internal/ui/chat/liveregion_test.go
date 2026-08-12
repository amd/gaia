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
	for _, tool := range []string{
		"list_inbox", "get_message", "classify", "apply_prefs",
		"pre_scan_inbox", "index_document", "read_file", "summarize",
	} {
		m = feed(t, m, event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool})
	}

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)

	lines := strings.Split(out, "\n")
	// Each entry is at most two lines (the action and its `└` outcome); no
	// outcome has landed here, so the window is one line per entry.
	if len(lines) > workLogLines {
		t.Errorf("live region is %d lines; must stay bounded at %d", len(lines), workLogLines)
	}
	if !strings.Contains(out, "Summarizing") {
		t.Errorf("live region dropped the most recent tool:\n%s", out)
	}
	// The oldest entry scrolls out rather than the newest being discarded.
	if strings.Contains(out, "Reading your inbox") {
		t.Errorf("oldest entry should have scrolled out of the bounded window:\n%s", out)
	}
}

// The whole point of the log: a person watching a 110s turn can read what the
// agent is doing FOR THEM, and what each step came back with — no tool names,
// no JSON, no "Step 4/50".
func TestLiveRegionNarratesToolsInPlainLanguage(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalStatusEvent{Type: "status", Message: "Processing with Gemma-4-E4B-it-GGUF..."},
		event.CanonicalStatusEvent{Type: "status", Message: "Step 1/50"},
		event.CanonicalStatusEvent{Type: "status", Message: "Thinking"},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_skills"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "list_skills",
			Data: []byte(`{"success":true,"summary":"18 skills","latency_ms":20.7}`),
		},
		event.CanonicalToolCallEvent{
			Type: "tool_call", Tool: "load_skill", Args: []byte(`{"name":"github-triage"}`),
		},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "load_skill",
			Data: []byte(`{"success":true,"summary":"success","security_tier":"experimental","latency_ms":1500}`),
		},
	)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)

	for _, want := range []string{
		"Checking your installed skills",  // list_skills, in words
		"18 skills",                       // its outcome, one line
		"21ms",                            // ...with how long it took
		"Loading the github-triage skill", // the argument that matters
		"experimental tier",               // the outcome worth knowing
		"1.5s",                            // seconds once it passes a second
	} {
		if !strings.Contains(out, want) {
			t.Errorf("live region never said %q:\n%s", want, out)
		}
	}
	// Harness internals stay out of the user's way unless --debug asked for them.
	for _, noise := range []string{"Step 1/50", "Gemma-4-E4B-it-GGUF", "list_skills", "load_skill"} {
		if strings.Contains(out, noise) {
			t.Errorf("live region leaked harness internals %q:\n%s", noise, out)
		}
	}
	// A bare "success" echoed under a tool call is a wasted line.
	if strings.Contains(out, "└ success") {
		t.Errorf("a bare status word was rendered as an outcome:\n%s", out)
	}
}

// For a shell tool the command IS the highest-signal thing on screen — no phrase
// wrapped round it beats showing it.
func TestLiveRegionShowsTheActualShellCommand(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m, event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "run_shell_command",
		Args: []byte(`{"command":"gh issue view 2924 --repo amd/gaia"}`),
	})

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	if !strings.Contains(out, "gh issue view 2924 --repo amd/gaia") {
		t.Errorf("the command a user could re-run themselves was not shown:\n%s", out)
	}
}

// A sidecar that narrates its own work always wins — it knows what the call is
// for; this client can only guess from the tool name.
func TestSidecarNarrationAndPreviewWin(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalToolCallEvent{
			Type: "tool_call", Tool: "run_shell_command",
			Args:      []byte(`{"command":"gh issue view 2924"}`),
			Narration: "Reading issue #2924",
		},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "run_shell_command",
			Preview: "3.7 KB returned (truncated)",
			Data:    []byte(`{"success":true,"summary":"a much longer summary nobody needs"}`),
		},
	)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	if !strings.Contains(out, "Reading issue #2924") {
		t.Errorf("the sidecar's own narration was ignored:\n%s", out)
	}
	if !strings.Contains(out, "3.7 KB returned (truncated)") {
		t.Errorf("the sidecar's own preview was ignored:\n%s", out)
	}
	if strings.Contains(out, "nobody needs") {
		t.Errorf("the composed fallback overrode the sidecar's preview:\n%s", out)
	}
}

// Harness mechanics are not deleted, they are demoted: whoever is debugging the
// wire has to be able to see them.
func TestDebugShowsTheHarnessStatusesUsersDoNotSee(t *testing.T) {
	m := newTestChat(t)
	m.debug = true
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Step 3/50"})

	if out := ansi.Strip(m.renderLiveRegion()); !strings.Contains(out, "Step 3/50") {
		t.Errorf("--debug must still surface agent-loop mechanics:\n%s", out)
	}
}

// The clock is proof the process is alive; on its own it says nothing. It must
// always sit next to a description of what is being timed.
func TestElapsedClockAlwaysAccompaniesADescription(t *testing.T) {
	m := newTestChat(t)
	m.queryStart = time.Now().Add(-95 * time.Second)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	clock := strings.Split(out, "\n")[0]
	if !strings.Contains(clock, "1:35") {
		t.Fatalf("live region missing an elapsed clock:\n%s", out)
	}
	// Strip the spinner rune and the clock; something readable must remain.
	rest := strings.TrimSpace(strings.ReplaceAll(clock, "1:35", ""))
	if len([]rune(rest)) < 8 {
		t.Errorf("the clock is standing alone with no description: %q", clock)
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

	if !strings.Contains(out, "Triaging message") || !strings.Contains(out, "x14") {
		t.Errorf("14 calls to one tool did not collapse to a counter:\n%s", out)
	}
	if n := strings.Count(out, "Triaging message"); n != 1 {
		t.Errorf("the tool appears %d times; repeats must fold into one line:\n%s", n, out)
	}
}

// The reassurance line lasts until something has actually COMPLETED. Once the
// log tells the story on its own the line is noise, and it costs the newest
// entry its screen row.
func TestStillWorkingHintLastsUntilRealWorkLands(t *testing.T) {
	m := newTestChat(t)
	m.queryStart = time.Now().Add(-stillWorkingAfter - time.Second)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("silent:\n%s", out)
	if !strings.Contains(out, "still working") || !strings.Contains(out, "60-90s") {
		t.Errorf("after %s of silence the wait must be called expected:\n%s", stillWorkingAfter, out)
	}

	// A stage line is NOT progress — the agent saying it is thinking IS the
	// wait, not the end of it. A live turn sat here for 1:47 with no hint.
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Working out how to answer"})
	out = ansi.Strip(m.renderLiveRegion())
	t.Logf("stage only:\n%s", out)
	if !strings.Contains(out, "still working") {
		t.Errorf("a long wait under a bare stage line still needs the hint:\n%s", out)
	}

	// A completed step is.
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "pre_scan_inbox", Preview: "31 messages"},
	)
	out = ansi.Strip(m.renderLiveRegion())
	t.Logf("work done:\n%s", out)
	if strings.Contains(out, "still working") {
		t.Errorf("the hint must give way once real work has landed:\n%s", out)
	}
	if !strings.Contains(out, "Scanning your inbox") || !strings.Contains(out, "31 messages") {
		t.Errorf("the completed action never appeared:\n%s", out)
	}
}

// No colour-only and no glyph-only signals: whether a step failed has to be
// readable in a terminal with no colour and no emoji font.
func TestLiveRegionOutcomesAreReadableWithoutColour(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "send_email"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "send_email", Data: []byte(`{"ok":false}`)},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_inbox"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "list_inbox",
			Data: []byte(`{"ok":true,"summary":"4 unread"}`),
		},
	)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	if !strings.Contains(out, "failed") {
		t.Errorf("a failed step must say so in words, not in red:\n%s", out)
	}
	if !strings.Contains(out, "4 unread") {
		t.Errorf("a succeeded step must report what it came back with:\n%s", out)
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

// The gaia sidecar re-sends the same stage line once per agent-loop step. A turn
// that spends three of its six log lines saying "Working out how to answer" has
// no room left for the work.
func TestARepeatedStageIsNotSaidTwice(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalStatusEvent{Type: "status", Message: "Working out how to answer"},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_skills"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "list_skills", Preview: "4 skills"},
		event.CanonicalStatusEvent{Type: "status", Message: "Working out how to answer"},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "load_skill", Args: []byte(`{"name":"github-triage"}`)},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "load_skill", Preview: "1 loaded skill"},
		event.CanonicalStatusEvent{Type: "status", Message: "Working out how to answer"},
	)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	if n := strings.Count(out, "Working out how to answer"); n != 1 {
		t.Errorf("the same stage was announced %d times:\n%s", n, out)
	}
	// A stage that genuinely changes still gets its own line.
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Checking the repository"})
	if out := ansi.Strip(m.renderLiveRegion()); !strings.Contains(out, "Checking the repository") {
		t.Errorf("a new stage was swallowed by the repeat filter:\n%s", out)
	}
}

// The live line sits directly under the sidecar's own stage text; wording it the
// same way reads as a stuck loop.
func TestTheLiveLineDoesNotEchoTheStageAboveIt(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalStatusEvent{Type: "status", Message: "Working out how to answer"},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_skills"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "list_skills", Preview: "4 skills"},
	)

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	lines := strings.Split(out, "\n")
	last := lines[len(lines)-1]
	if strings.Contains(last, "Working out how to answer") {
		t.Errorf("the live line repeats the stage line above it:\n%s", out)
	}
}

func TestSingleResultsAreNotPluralised(t *testing.T) {
	if got := toolResultDetail(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "list_skills",
		Data: []byte(`{"success":true,"skills":[{"name":"github-triage"}]}`),
	}); !strings.Contains(got, "1 skill") || strings.Contains(got, "1 skills") {
		t.Errorf("a single result read as %q", got)
	}
}
