package chat

import (
	"fmt"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// A triage turn touches dozens of messages over 60-90s on a local model. The
// region keeps a bounded window of what finished, newest last; what is running
// now is the status line.
func TestLiveRegionKeepsBoundedHistory(t *testing.T) {
	m := newTestChat(t)
	tools := []string{
		"list_inbox", "get_message", "classify", "apply_prefs", "pre_scan_inbox",
		"index_document", "fetch_page", "list_directory", "search_web", "summarize",
		"query_documents", "load_skill",
	}
	for i, tool := range tools {
		m = feed(t, m, event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool})
		if i < len(tools)-1 {
			m = feed(t, m, event.CanonicalToolResultEvent{Type: "tool_result", Tool: tool, Preview: "done " + tool})
		}
	}

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)

	if lines := strings.Split(out, "\n"); len(lines) > m.logRows() {
		t.Errorf("live region is %d lines; must stay inside its %d-row budget", len(lines), m.logRows())
	}
	if !strings.Contains(out, "done query_documents") {
		t.Errorf("live region dropped the most recent finished call:\n%s", out)
	}
	// The oldest scrolls out, and the region says so rather than dropping it silently.
	if strings.Contains(out, "done list_inbox") || !strings.Contains(out, "earlier actions") {
		t.Errorf("the oldest entry should have scrolled out, counted:\n%s", out)
	}
	if line := ansi.Strip(m.statusLine(m.now())); !strings.Contains(line, "Load") {
		t.Errorf("the running call is not the status line: %q", line)
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
		"Check your installed skills",  // list_skills, in words
		"18 skills",                    // its outcome, one line
		"Load the github-triage skill", // the argument that matters
		"experimental tier",            // the outcome worth knowing
	} {
		if !strings.Contains(out, want) {
			t.Errorf("live region never said %q:\n%s", want, out)
		}
	}
	// How long a step took is telemetry: folded away, there on Ctrl+O.
	if strings.Contains(out, "21ms") {
		t.Errorf("the folded log carries latency:\n%s", out)
	}
	m.expandWork = true
	expanded := ansi.Strip(m.renderLiveRegion())
	for _, want := range []string{"21ms", "1.5s"} {
		if !strings.Contains(expanded, want) {
			t.Errorf("the expanded log never said %q:\n%s", want, expanded)
		}
	}
	// Harness internals stay out of the user's way in every mode.
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

// A shell call leads with what it does, and the command itself — the thing a
// user could re-run — is the one dim line of evidence under it.
func TestAShellCallShowsItsCommandUnderItsHeadline(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalToolCallEvent{
			Type: "tool_call", Tool: "run_shell_command",
			Args: []byte(`{"command":"gh issue view 2924 --repo amd/gaia"}`),
		},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "run_shell_command", Data: []byte(`{"success":true}`)},
	)

	rows := strings.Split(ansi.Strip(m.renderLiveRegion()), "\n")
	t.Logf("\n%s", strings.Join(rows, "\n"))
	if len(rows) < 2 || !strings.Contains(rows[0], "Run gh issue") {
		t.Fatalf("the headline does not say what ran:\n%s", strings.Join(rows, "\n"))
	}
	if !strings.Contains(rows[1], "└ gh issue view 2924 --repo amd/gaia") {
		t.Errorf("the command a user could re-run is not under the headline:\n%s", strings.Join(rows, "\n"))
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
	if !strings.Contains(out, "Read issue #2924") {
		t.Errorf("the sidecar's own narration was ignored:\n%s", out)
	}
	if !strings.Contains(out, "3.7 KB returned (truncated)") {
		t.Errorf("the sidecar's own preview was ignored:\n%s", out)
	}
	if strings.Contains(out, "nobody needs") {
		t.Errorf("the composed fallback overrode the sidecar's preview:\n%s", out)
	}
}

// Harness mechanics never reach the screen, in any mode: the debugging view of
// them is the trace (--trace), not the user's frame.
func TestHarnessStatusesNeverReachTheScreen(t *testing.T) {
	for _, expanded := range []bool{false, true} {
		m := newTestChat(t)
		m.expandWork = expanded
		m.streaming = true
		m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Step 3/50"})
		out := ansi.Strip(m.renderLiveRegion() + "\n" + m.statusLine(m.now()))
		if strings.Contains(out, "Step 3/50") || strings.Contains(out, "/50") {
			t.Errorf("expanded=%v: agent-loop mechanics reached the screen:\n%s", expanded, out)
		}
	}
}

// The clock is proof the process is alive; on its own it says nothing. It must
// always sit next to a description of what is being timed.
func TestElapsedClockAlwaysAccompaniesADescription(t *testing.T) {
	m := newTestChat(t)
	m.queryStart = time.Now().Add(-95 * time.Second)

	line := ansi.Strip(m.statusLine(time.Now()))
	if !strings.Contains(line, "(1m 35s)") {
		t.Fatalf("the status line is missing an elapsed clock: %q", line)
	}
	// Strip the spinner rune and the clock; something readable must remain.
	rest := strings.TrimSpace(strings.ReplaceAll(line, "(1m 35s)", ""))
	if len([]rune(rest)) < 8 {
		t.Errorf("the clock is standing alone with no description: %q", line)
	}
}

// Twenty identical calls read as a single slow call; the counter is what makes
// repetition legible as progress, and every target is still named.
func TestLiveRegionCollapsesRepeatsWithACounter(t *testing.T) {
	m := newTestChat(t)
	for i := 0; i < 14; i++ {
		m = feed(t, m,
			event.CanonicalToolCallEvent{
				Type: "tool_call", Tool: "triage_message",
				Args: []byte(`{"message_id":"m` + string(rune('a'+i)) + `"}`),
			},
			event.CanonicalToolResultEvent{Type: "tool_result", Tool: "triage_message", Data: []byte(`{"ok":true}`)},
		)
	}

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)

	if !strings.Contains(out, "Triage message") || !strings.Contains(out, "x14") {
		t.Errorf("14 calls to one tool did not collapse to a counter:\n%s", out)
	}
	if n := strings.Count(out, "●"); n != 1 {
		t.Errorf("the tool has %d bullets; repeats must fold into one:\n%s", n, out)
	}
}

// The reassurance tip lasts until something has actually COMPLETED. Once the
// log tells the story on its own the tip is noise.
func TestStillWorkingHintLastsUntilRealWorkLands(t *testing.T) {
	m := newTestChat(t)
	m.queryStart = time.Now().Add(-stillWorkingAfter - time.Second)

	tip := ansi.Strip(m.statusTip(time.Now()))
	if !strings.Contains(tip, "still working") || !strings.Contains(tip, "60-90s") {
		t.Errorf("after %s of silence the wait must be called expected: %q", stillWorkingAfter, tip)
	}

	// A stage line is NOT progress — the agent saying it is thinking IS the
	// wait, not the end of it. A live turn sat here for 1:47 with no hint.
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Working out how to answer"})
	if tip := ansi.Strip(m.statusTip(time.Now())); !strings.Contains(tip, "still working") {
		t.Errorf("a long wait under a bare stage line still needs the tip: %q", tip)
	}

	// A completed step is.
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "pre_scan_inbox", Preview: "31 messages"},
	)
	if tip := ansi.Strip(m.statusTip(time.Now())); strings.Contains(tip, "still working") {
		t.Errorf("the tip must give way once real work has landed: %q", tip)
	}
	out := ansi.Strip(m.renderLiveRegion())
	if !strings.Contains(out, "Scan your inbox") || !strings.Contains(out, "31 messages") {
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
	// ✗ is decoration beside the word, never the signal on its own.
	if !strings.Contains(out, "✗ failed") {
		t.Errorf("the failure mark is not next to the word:\n%s", out)
	}
	for _, emoji := range []string{"🔧", "🧠", "🎯", "⚠️"} {
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
	// Stage lines are narration, folded away by default — this pins the
	// Ctrl+O view, where they are all on screen.
	m.expandWork = true
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

// #5089: a plan run re-announces its headline — the user's whole request —
// before every step, with "Plan: a → b" and "Executing step N" around it. None
// of that is the work: it is never shown, folded or expanded, and the step line
// carries no "[harness]". The steps themselves still are.
func TestPlanNoiseIsNeverShown(t *testing.T) {
	for _, expanded := range []bool{false, true} {
		m := newTestChat(t)
		m.expandWork = expanded
		const headline = "Following the plan to compare the term sheet"
		for step := 1; step <= 3; step++ {
			m = feed(t, m,
				event.CanonicalStatusEvent{Type: "status", Message: fmt.Sprintf("Step %d/50", step)},
				event.CanonicalStatusEvent{Type: "status", Message: "Plan: query_specific_file → query_specific_file → write_file"},
				event.CanonicalStatusEvent{Type: "status", Message: fmt.Sprintf("Executing step %d of the plan", step)},
				event.CanonicalStatusEvent{Type: "status", Message: headline},
				event.CanonicalToolCallEvent{Type: "tool_call", Tool: "query_specific_file"},
				event.CanonicalToolResultEvent{Type: "tool_result", Tool: "query_specific_file", Preview: "10 chunks"},
			)
		}

		out := ansi.Strip(m.renderLiveRegion())
		for _, noise := range []string{headline, "Plan:", "Executing step", "[harness]", "/50"} {
			if strings.Contains(out, noise) {
				t.Errorf("expanded=%v: %q reached the screen:\n%s", expanded, noise, out)
			}
		}
		if !strings.Contains(out, "10 chunks") && !strings.Contains(out, "x3") && !strings.Contains(out, "3 times") {
			t.Errorf("expanded=%v: the steps themselves went missing:\n%s", expanded, out)
		}
	}
}

// Narration the model repeats word for word before the next step adds nothing.
func TestRepeatedNarrationIsKeptOnce(t *testing.T) {
	m := newTestChat(t)
	m.expandWork = true
	for i := 0; i < 3; i++ {
		m = feed(t, m,
			event.CanonicalTokenEvent{Type: "token", Delta: "Checking the next datasheet."},
			event.CanonicalToolCallEvent{Type: "tool_call", Tool: "query_specific_file"},
			event.CanonicalToolResultEvent{Type: "tool_result", Tool: "query_specific_file", Preview: "3 chunks"},
		)
	}
	notes := 0
	for _, item := range m.activity {
		if item.Kind == "note" {
			notes++
		}
	}
	if notes != 1 {
		t.Errorf("identical narration was logged %d times: %+v", notes, m.activity)
	}
}
