// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// A turn is read one small piece at a time (#4445). One bugfix turn used to
// paint a wall: model messages run together ("first.Reproduced: …"), narration
// rows, three transcript lines per confirmation, and 100-column temp paths.
// Every test here asserts on the FRAME — what the terminal paints — because
// the model can hold the right data while the screen shows the wrong thing.

func ctrlO(t *testing.T, m ChatModel) ChatModel {
	t.Helper()
	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlO})
	return updated.(ChatModel)
}

func frameOf(m ChatModel) string {
	m.updateViewport()
	return ansi.Strip(visibleFrame(m))
}

// withPaths points path shortening at base and home for one test.
func withPaths(t *testing.T, base, home string) {
	t.Helper()
	oldBase, oldHome := pathBase, homeDir
	pathBase, homeDir = base, home
	t.Cleanup(func() { pathBase, homeDir = oldBase, oldHome })
}

func tok(s string) event.CanonicalTokenEvent {
	return event.CanonicalTokenEvent{Type: "token", Delta: s}
}

func toolCall(tool, args string) event.CanonicalToolCallEvent {
	return event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool, Args: json.RawMessage(args)}
}

func toolDone(tool, preview string) event.CanonicalToolResultEvent {
	return event.CanonicalToolResultEvent{Type: "tool_result", Tool: tool, Preview: preview}
}

// The headline defect: every model message in a turn streamed into ONE buffer,
// so the screen read "Let me reproduce the bug first.Reproduced: …Now the
// fix.Fixed and verified". Text before a tool call is that step's preamble;
// only what comes after the last one is the answer.
func TestSuccessiveModelMessagesAreNeverConcatenated(t *testing.T) {
	m := sizedChat(t, 120, 40)
	m = feed(t, m,
		tok("Let me reproduce the bug first."),
		toolCall("run_python", `{"code":"parse('z')"}`),
		toolDone("run_python", "ValueError: unconverted data remains: z"),
		tok("Reproduced: `z` raises. Now the fix."),
		toolCall("edit_file", `{"file_path":"toybox/dates.py"}`),
		toolDone("edit_file", "1 edit"),
		tok("Fixed and verified — all 4 tests pass."),
	)
	m = repaint(t, m)

	frame := frameOf(m)
	for _, glued := range []string{"first.Reproduced", "fix.Fixed"} {
		if strings.Contains(frame, glued) {
			t.Errorf("two model messages ran together (%q):\n%s", glued, frame)
		}
	}
	if !strings.Contains(frame, "Fixed and verified") {
		t.Errorf("the answer being written is not on screen:\n%s", frame)
	}
	if strings.Contains(frame, "Let me reproduce") {
		t.Errorf("a step's preamble must be folded away by default:\n%s", frame)
	}

	// No `answer` on the final: the streamed text is the answer — and only the
	// text after the last step.
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final"})
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleAssistant || last.Content != "Fixed and verified — all 4 tests pass." {
		t.Errorf("the answer carried the preamble with it: %+v", last)
	}

	// The preamble is folded, not thrown away.
	if frame := frameOf(ctrlO(t, m)); !strings.Contains(frame, "Let me reproduce the bug first.") {
		t.Errorf("Ctrl+O must show the folded narration:\n%s", frame)
	}
}

// ✻ rows were the model thinking aloud ("Let me do it.", "Hmm, should I fix it
// too?"), all turn long. Folded, none of it is on screen — not even as the live
// line, which names the state instead — and Ctrl+O brings every word back.
func TestNarrationIsFoldedUntilAskedFor(t *testing.T) {
	m := sizedChat(t, 120, 40)
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Hmm, should I fix it too?"})
	frame := frameOf(m)
	if strings.Contains(frame, "should I fix it") {
		t.Errorf("the model's reasoning reached the live line:\n%s", frame)
	}
	if !strings.Contains(frame, "Getting started") {
		t.Errorf("the live line must still say what state the turn is in:\n%s", frame)
	}

	m = feed(t, m,
		toolCall("list_directory", `{"directory":"toybox"}`),
		toolDone("list_directory", "2 files"),
		event.CanonicalStatusEvent{Type: "status", Message: "Let me do it."},
		toolCall("read_file", `{"file_path":"toybox/dates.py"}`),
	)
	frame = frameOf(m)
	for _, said := range []string{"should I fix it", "Let me do it."} {
		if strings.Contains(frame, said) {
			t.Errorf("narration %q is on screen:\n%s", said, frame)
		}
	}
	for _, step := range []string{"Listing toybox", "Reading toybox"} {
		if !strings.Contains(frame, step) {
			t.Errorf("step %q is missing:\n%s", step, frame)
		}
	}
	if frame := frameOf(ctrlO(t, m)); !strings.Contains(frame, "Let me do it.") {
		t.Errorf("Ctrl+O must bring the narration back:\n%s", frame)
	}
}

// A confirmation used to reach the screen four times: "[!] confirmation needed",
// "[!] … resolved: approved — running it", "[!] approved decision … delivered",
// plus the modal. Now: the modal asks, and the step it gated says one word.
func TestAConfirmationIsShownOnceThenOneWord(t *testing.T) {
	m, _ := liveModel(t)
	m.width, m.height = 120, 40
	m.resize()
	m.streaming = true
	m = feed(t, m, shellCall("pwd"), gatedShellCall())

	frame := frameOf(m)
	if !strings.Contains(frame, "esc deny") {
		t.Fatalf("the modal is the prompt and must be on screen:\n%s", frame)
	}
	if !strings.Contains(frame, "pwd · "+approvalWaiting) {
		t.Errorf("the gated step must say it is waiting on the user:\n%s", frame)
	}

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")})
	m = updated.(ChatModel)
	decided := cmd().(components.ConfirmationDecidedMsg)
	updated, deliver := m.Update(decided)
	m = updated.(ChatModel)
	updated, _ = m.Update(deliver())
	m = updated.(ChatModel)
	m = feed(t, m,
		toolDone("run_shell_command", "/home/u/proj"),
		event.CanonicalFinalEvent{Type: "final", Answer: "You are in /home/u/proj."},
	)

	frame = frameOf(m)
	for _, noise := range []string{"[!]", "confirmation needed", "resolved:", "delivered", "running it"} {
		if strings.Contains(frame, noise) {
			t.Errorf("the confirmation lifecycle leaked %q into the transcript:\n%s", noise, frame)
		}
	}
	if n := strings.Count(frame, "approved"); n != 1 {
		t.Errorf("the outcome must be said exactly once, said %d times:\n%s", n, frame)
	}
	if !strings.Contains(frame, "pwd · approved") {
		t.Errorf("the outcome belongs on the step it gated:\n%s", frame)
	}
}

// The issue's own frame: a browse step printed a 100-column temp path and a
// "success: … · 0ms" outcome. Folded, it is one row relative to the project,
// and the outcome says only what was found.
func TestAStepIsOneShortRowRelativeToTheProject(t *testing.T) {
	base := filepath.Join(t.TempDir(), "scratchpad", "newuser", "proj")
	withPaths(t, base, filepath.Dir(base))
	pycache := filepath.Join(base, "toybox", "__pycache__")

	m := sizedChat(t, 120, 40)
	m = feed(t, m,
		event.CanonicalToolCallEvent{
			Type: "tool_call", Tool: "browse_directory",
			Args:      json.RawMessage(`{"path":` + quote(pycache) + `}`),
			Narration: "Running browse directory: " + pycache,
		},
		toolDone("browse_directory", "success: Listing 1 items in __pycache__ (0 folders, 1 files) · 0ms"),
	)
	frame := frameOf(m)
	if strings.Contains(frame, base) {
		t.Errorf("an absolute path inside the project reached the screen:\n%s", frame)
	}
	if want := "Running browse directory: " + filepath.Join("toybox", "__pycache__"); !strings.Contains(frame, want) {
		t.Errorf("want %q on screen:\n%s", want, frame)
	}
	if !strings.Contains(frame, "└ Listing 1 items in __pycache__ (0 folders, 1 files)") {
		t.Errorf("the outcome must say what was found:\n%s", frame)
	}
	for _, noise := range []string{"success", "0ms"} {
		if strings.Contains(frame, noise) {
			t.Errorf("the folded outcome still carries %q:\n%s", noise, frame)
		}
	}
}

// The live drive: the sidecar clips its narration before anything can shorten
// the path, leaving "Reading file: C:\Users\…\workt…" — the one useless part.
// The full value is still in the call's args, so it is put back and shortened.
func TestASidecarClippedPathIsRestoredThenShortened(t *testing.T) {
	base := filepath.Join(t.TempDir(), "scratchpad", "proj")
	withPaths(t, base, "")
	full := filepath.Join(base, "toybox", "dates.py")
	clipped := "Reading file: " + full[:30] + "…"

	m := feed(t, sizedChat(t, 120, 40), event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "read_file",
		Args: json.RawMessage(`{"file_path":` + quote(full) + `}`), Narration: clipped,
	})
	want := "Reading file: " + filepath.Join("toybox", "dates.py")
	if frame := frameOf(m); !strings.Contains(frame, want) {
		t.Errorf("want %q on screen:\n%s", want, frame)
	}
}

// Folded, a step costs one row however long its command is; Ctrl+O wraps it.
func TestAFoldedStepIsOneRow(t *testing.T) {
	m := feed(t, chatAt(t, term{"standard", 80, 24}), shellCall(longShell))
	rowsWith := func(m ChatModel) int {
		n := 0
		for _, r := range rowsOf(m.renderLiveRegion()) {
			if strings.Contains(r, "gh issue") || strings.Contains(r, "--json") || strings.Contains(r, "--jq") {
				n++
			}
		}
		return n
	}
	if n := rowsWith(m); n != 1 {
		t.Errorf("a folded step took %d rows:\n%s", n, ansi.Strip(m.renderLiveRegion()))
	}
	if !strings.Contains(ansi.Strip(m.renderLiveRegion()), "…") {
		t.Errorf("a cut must be marked:\n%s", ansi.Strip(m.renderLiveRegion()))
	}
	if n := rowsWith(ctrlO(t, m)); n < 2 {
		t.Errorf("Ctrl+O must show the whole command, got %d rows", n)
	}
}

// A failure is the one outcome a user has to act on, and its remedy is its
// tail — so folding never cuts it.
func TestAFoldedFailureKeepsItsRemedy(t *testing.T) {
	m := feed(t, chatAt(t, term{"standard", 80, 24}),
		toolCall("write_file", `{}`),
		toolDone("write_file", "failed - permission denied; run gaia from a shell with write access to that folder"),
	)
	got := flat(rowsOf(m.renderLiveRegion()))
	if !strings.Contains(got, "run gaia from a shell with write access to that folder") {
		t.Errorf("the folded log cut a failure's remedy: %s", got)
	}
}

// Narration between two calls of the same tool is hidden, so it must not keep
// them apart either: the live drive printed "Editing file" twice in a row.
func TestRepeatsFoldAcrossHiddenNarration(t *testing.T) {
	m := feed(t, sizedChat(t, 120, 40),
		toolCall("edit_file", `{"file_path":"toybox/dates.py"}`),
		toolDone("edit_file", "1 edit"),
		event.CanonicalStatusEvent{Type: "status", Message: "Now the test."},
		toolCall("edit_file", `{"file_path":"tests/test_dates.py"}`),
		toolDone("edit_file", "1 edit"),
		event.CanonicalFinalEvent{Type: "final", Answer: "Done."},
	)
	frame := frameOf(m)
	if n := strings.Count(frame, "Editing"); n != 1 || !strings.Contains(frame, "x2") {
		t.Errorf("two consecutive edits must fold to one row with x2, got %d rows:\n%s", n, frame)
	}
}

// The repeat counter is what turns thirteen identical rows into one.
func TestAFoldedRowKeepsItsCounter(t *testing.T) {
	m := chatAt(t, term{"standard", 80, 24})
	for i := 0; i < 13; i++ {
		m = feed(t, m, shellCall(longShell))
	}
	if got := flat(rowsOf(m.renderLiveRegion())); !strings.Contains(got, "x13") {
		t.Errorf("the repeat count was cut off the folded row: %s", got)
	}
}

// The steps outlive the turn as one compact record directly above the answer,
// with a blank row between them so the answer does not read as one more step.
func TestTheWorkOutlivesTheTurnAboveTheAnswer(t *testing.T) {
	m := sizedChat(t, 120, 40)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "fix the z bug"})
	m = feed(t, m,
		toolCall("read_file", `{"file_path":"toybox/dates.py"}`),
		toolDone("read_file", "success · 328ms"),
		toolCall("run_shell_command", `{"command":"python -m pytest -q"}`),
		toolDone("run_shell_command", "4 passed"),
		event.CanonicalFinalEvent{Type: "final", Answer: "Fixed — all 4 tests pass."},
	)

	rows := strings.Split(frameOf(m), "\n")
	step, answer := -1, -1
	for i, r := range rows {
		if strings.Contains(r, "Reading toybox/dates.py") && step < 0 {
			step = i
		}
		if strings.Contains(r, "Fixed — all 4 tests pass.") {
			answer = i
		}
	}
	if step < 0 || answer < 0 || step > answer {
		t.Fatalf("the work record must sit above the answer (step row %d, answer row %d):\n%s",
			step, answer, strings.Join(rows, "\n"))
	}
	if strings.TrimSpace(rows[answer-1]) != "" {
		t.Errorf("no blank row separates the answer from the steps:\n%s", strings.Join(rows, "\n"))
	}
	frame := strings.Join(rows, "\n")
	if !strings.Contains(frame, "└ 4 passed") {
		t.Errorf("a step's result is missing from the record:\n%s", frame)
	}
	if strings.Contains(frame, "success") || strings.Contains(frame, "328ms") {
		t.Errorf("a bare success outcome earned a row:\n%s", frame)
	}
	if !strings.Contains(frame, "Ctrl+O details") {
		t.Errorf("once idle, the status bar must say how to open the detail:\n%s", frame)
	}
}

func TestShortenPaths(t *testing.T) {
	type tc struct{ in, want string }
	run := func(t *testing.T, base, home string, cases []tc) {
		withPaths(t, base, home)
		for _, c := range cases {
			if got := shortenPaths(c.in); got != c.want {
				t.Errorf("shortenPaths(%q) = %q, want %q", c.in, got, c.want)
			}
		}
	}
	t.Run("posix", func(t *testing.T) {
		run(t, "/home/u/proj", "/home/u", []tc{
			{"Reading /home/u/proj/toybox/dates.py", "Reading toybox/dates.py"},
			{"ls /home/u/notes/a.txt", "ls ~/notes/a.txt"},
			{"cd /home/u/proj", "cd ."},
			{"see /home/u/proj/a.py.", "see a.py."},
			{"fetch https://example.com/a/b", "fetch https://example.com/a/b"},
			{"/model fireworks.x", "/model fireworks.x"},
			{"cat /opt/vendor/really/quite/deeply/nested/tree/of/folders/file.txt",
				"cat …/quite/deeply/nested/tree/of/folders/file.txt"},
		})
	})
	t.Run("windows", func(t *testing.T) {
		run(t, `C:\Users\K\proj`, `C:\Users\K`, []tc{
			// Tool errors echo the path lower-cased; it is still the project.
			{`failed: c:\users\k\proj\toybox\x.pyc: denied`, `failed: toybox\x.pyc: denied`},
			{`Reading C:\Users\K\AppData\Local\Temp\x.py`, `Reading ~\AppData\Local\Temp\x.py`},
			{`Reading C:/Users/K/proj/a.py`, `Reading a.py`},
			{`C:\USERS\K\PROJ\b.py`, `b.py`},
		})
	})
}

func quote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

// Review finding: two calls to one tool, approved then denied, must not fold
// into a row that says only "approved".
func TestDifferentAnswersNeverFoldTogether(t *testing.T) {
	m := sizedChat(t, 120, 40)
	m.activity = replySteps("approved", "denied")
	frame := frameOf(m)
	if !strings.Contains(frame, "· approved") || !strings.Contains(frame, "· denied") {
		t.Errorf("each answer must stay on its own step:\n%s", frame)
	}
	if strings.Contains(frame, "x2") {
		t.Errorf("steps with different answers were folded:\n%s", frame)
	}
}

// replySteps is one closed send_reply step per answer, as the live log holds them.
func replySteps(approvals ...string) []ActivityItem {
	var out []ActivityItem
	for _, a := range approvals {
		ok := a == "approved"
		out = append(out, ActivityItem{
			Kind: "tool", Tool: "send_reply", Content: "Sending a reply",
			Done: true, Success: &ok, Approval: a,
		})
	}
	return out
}

// Review finding: a denied step the sidecar never closes must not sit in the
// live slot with a ticking clock, as if it were running.
func TestADeniedStepIsNeverTheLiveLine(t *testing.T) {
	m := sizedChat(t, 120, 40)
	m.activity = []ActivityItem{{Kind: "tool", Tool: "send_reply", Content: "Sending a reply", Approval: "denied"}}
	region := ansi.Strip(m.renderLiveRegion())
	if !strings.Contains(region, "▪ Sending a reply · denied") {
		t.Errorf("a denied step must render as a finished row:\n%s", region)
	}
}

// Review finding: agent-supplied scope text reaches the row through clean().
func TestTheAlwaysScopeIsSanitized(t *testing.T) {
	text, _ := confirmationOutcomeText(components.ConfirmationDecidedMsg{
		Approved: true, Always: true, AlwaysScope: "pwd\x1b[2J\x07",
	})
	if strings.ContainsAny(text, "\x1b\x07") {
		t.Errorf("control bytes survived into the outcome: %q", text)
	}
}

// Review finding: answer, then one more tool, then an empty `final`. The
// answer was stashed as narration by the tool call and must still be shown.
func TestAnAnswerBeforeATrailingToolCallSurvivesAnEmptyFinal(t *testing.T) {
	m := feed(t, sizedChat(t, 120, 40),
		tok("All 4 tests pass."),
		toolCall("remember", `{"fact":"project uses pytest"}`),
		toolDone("remember", "saved"),
		event.CanonicalFinalEvent{Type: "final"},
	)
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleAssistant || last.Content != "All 4 tests pass." {
		t.Errorf("the answer was lost to the trailing tool call: %+v", last)
	}
}

// Found reading the live frames: an edit to dates.py and one to test_dates.py
// folded into "Editing file: toybox/dates.py x2" — a row naming the wrong file.
func TestAFoldedRowNamesEveryTargetItTouched(t *testing.T) {
	m := feed(t, sizedChat(t, 120, 40),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "edit_file", Narration: "Editing file: toybox/dates.py"},
		toolDone("edit_file", "1 edit"),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "edit_file", Narration: "Editing file: tests/test_dates.py"},
		toolDone("edit_file", "1 edit"),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "edit_file", Narration: "Editing file: toybox/dates.py"},
		toolDone("edit_file", "1 edit"),
	)
	want := "Editing file: toybox/dates.py, tests/test_dates.py x3"
	if frame := frameOf(m); !strings.Contains(frame, want) {
		t.Errorf("want %q on screen:\n%s", want, frame)
	}
}

// Esc mid-turn clears the live log; what already ran must stay on record.
func TestCancellingKeepsTheStepsThatRan(t *testing.T) {
	m := feed(t, sizedChat(t, 120, 40),
		toolCall("read_file", `{"file_path":"toybox/dates.py"}`),
		toolDone("read_file", "3 symbols"),
	)
	m.cancelFn = func() {}
	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if len(m.activity) != 0 {
		t.Errorf("the live log must start over on cancel: %+v", m.activity)
	}
	if frame := frameOf(m); !strings.Contains(frame, "Reading toybox/dates.py") {
		t.Errorf("the steps that ran before the cancel vanished:\n%s", frame)
	}
}

// The bar thinned its hints against a guessed width — the bare agent name plus
// " connected" — while it drew "agent gaia streaming", so from 38 to 150
// columns the last hint came out cut mid-word ("Ctrl+C q…"). Swept across every
// width, busy and idle: every hint whole, no row wider than the terminal, and
// never taller than the window.
func TestTheFrameFitsEveryWidth(t *testing.T) {
	for _, streaming := range []bool{true, false} {
		for w := 38; w <= 200; w++ {
			m := sizedChat(t, w, 30)
			m.streaming = streaming
			m.updateViewport()
			rows := strings.Split(m.View(), "\n")
			if len(rows) > 30 {
				t.Errorf("w=%d streaming=%v: %d rows on a 30-row terminal", w, streaming, len(rows))
			}
			for i, r := range rows {
				if got := ansi.StringWidth(ansi.Strip(r)); got > w {
					t.Errorf("w=%d streaming=%v: row %d is %d columns", w, streaming, i, got)
				}
			}
			if bar := strings.TrimSpace(ansi.Strip(rows[len(rows)-1])); strings.HasSuffix(bar, "…") {
				t.Errorf("w=%d streaming=%v: a hint was cut mid-word: %q", w, streaming, bar)
			}
		}
	}
}

// An answer's markdown was laid out once, at the width it arrived at. Narrow the
// window afterwards and its rows stayed that wide — the viewport clipped the
// right-hand side off tables, code and prose. Shrink and grow again: every row
// of the transcript fits the pane it is drawn in.
func TestAnAnswerReflowsWhenTheWindowNarrows(t *testing.T) {
	answer := "Fixed.\n\n| file | change | notes |\n|---|---|---|\n" +
		"| toybox/dates.py | endswith | a long note that keeps going past any sensible column |\n\n" +
		"```python\nif v.endswith((\"Z\", \"z\")):  # a trailing comment that runs well past eighty columns\n```\n\n" +
		strings.Repeat("Prose that wraps. ", 20)
	m := feed(t, sizedChat(t, 200, 40), event.CanonicalFinalEvent{Type: "final", Answer: answer})
	for _, w := range []int{100, 60, 160} {
		m.width = w
		m.resize()
		content := m.renderMessage(&m.messages[len(m.messages)-1], nil)
		for i, row := range strings.Split(content, "\n") {
			if got := ansi.StringWidth(row); got > m.viewport.Width {
				t.Fatalf("at %d columns, row %d is %d wide — laid out for the old width: %q",
					w, i, got, ansi.Strip(row))
			}
		}
	}
}

// The chrome budget reserved two rows View never drew, so every frame stopped
// two rows short of the window. Those rows were never repainted — after a
// resize the terminal reflowed a stale status bar into them and it stayed. The
// frame fills the window exactly, full access banner and confirmation included.
func TestTheFrameFillsTheWindowExactly(t *testing.T) {
	for _, tc := range []struct {
		name string
		set  func(*ChatModel)
	}{
		{"idle", func(*ChatModel) {}},
		{"streaming", func(m *ChatModel) { m.streaming = true }},
		{"full access", func(m *ChatModel) { m.fullAccess = true }},
	} {
		for _, h := range []int{12, 24, 30, 50} {
			m := sizedChat(t, 100, h)
			m.streaming = false
			tc.set(&m)
			m.resize()
			if got := len(strings.Split(m.View(), "\n")); got != h {
				t.Errorf("%s at %d rows: the frame is %d rows", tc.name, h, got)
			}
		}
	}
	m, _ := liveModel(t)
	m.width, m.height = 100, 30
	m.resize()
	m.streaming = true
	m = feed(t, m, gatedShellCall())
	if got := len(strings.Split(m.View(), "\n")); got != 30 {
		t.Errorf("with a confirmation up the frame is %d rows of 30", got)
	}
}
