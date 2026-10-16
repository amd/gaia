// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// Golden frames for the chat view: the plain text of a whole screen, exactly as
// the control API's /screen?format=plain serves it. A redesign shows up here as
// a reviewable diff of what a person sees, not as a pile of substring checks.
//
//	go test ./internal/ui/chat -run TestFrame -update
//
// rewrites testdata/frames/*.txt from the current renderer.
var updateFrames = flag.Bool("update", false, "rewrite golden frames under testdata/frames")

// frameClock is the time every frame is drawn at, so clocks are stable.
var frameEpoch = time.Date(2026, 10, 15, 14, 0, 0, 0, time.UTC)

// frameChat is a chat at a fixed size, on a still clock, in a project folder.
func frameChat(t *testing.T, w, h int) (*ChatModel, *time.Time) {
	t.Helper()
	m := NewChatModel(&nullClient{}, "gaia", "")
	now := frameEpoch
	m.clock = func() time.Time { return now }
	m.motion = motionState{} // no shimmer in a text golden; motion has its own tests
	m.width, m.height = w, h
	m.resize()
	return &m, &now
}

// at advances the frame clock and feeds events at that instant.
func at(t *testing.T, m *ChatModel, now *time.Time, d time.Duration, events ...interface{}) {
	t.Helper()
	*now = now.Add(d)
	*m = feed(t, *m, events...)
}

func call(tool, args string) event.CanonicalToolCallEvent {
	return event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool, Args: json.RawMessage(args)}
}

func result(tool, data string) event.CanonicalToolResultEvent {
	return event.CanonicalToolResultEvent{Type: "tool_result", Tool: tool, Data: json.RawMessage(data)}
}

func goal(text string) event.CanonicalStatusEvent {
	return event.CanonicalStatusEvent{Type: "status", Message: text}
}

func say(text string) event.CanonicalTokenEvent {
	return event.CanonicalTokenEvent{Type: "token", Delta: text}
}

// startTurn puts the user's message up and starts the clock, the way submit
// does, without a transport.
func startTurn(m *ChatModel, now time.Time, text string) {
	m.messages = append(m.messages, Message{Role: RoleUser, Content: text})
	m.streaming = true
	m.queryStart = now
	m.updateViewport()
}

// screen is the frame as plain text, trailing spaces trimmed per row.
func screen(m ChatModel) string {
	m.updateViewport()
	rows := strings.Split(ansi.Strip(m.View()), "\n")
	for i, r := range rows {
		rows[i] = strings.TrimRight(r, " ")
	}
	return strings.Join(rows, "\n") + "\n"
}

func golden(t *testing.T, name, got string) {
	t.Helper()
	path := filepath.Join("testdata", "frames", name+".txt")
	if *updateFrames {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(got), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("no golden frame %s (run with -update): %v\n%s", path, err, got)
	}
	if w := strings.ReplaceAll(string(want), "\r\n", "\n"); w != got {
		t.Errorf("frame %s changed.\n--- want\n%s\n--- got\n%s", name, w, got)
	}
}

// contractTurn is the knowledge-work turn the redesign was drawn against: eight
// contracts read, a failure the agent carries on past, a workbook written.
func contractTurn(t *testing.T, m *ChatModel, now *time.Time, dir string) {
	startTurn(m, *now, "Review the 8 contracts in ./contracts and build a risk register in contract_review.xlsx")
	at(t, m, now, 3*time.Second, say("I'll read each contract first, then build the register."))
	for i, name := range []string{"msa_acme.docx", "nda_beta.docx", "sow_gamma.docx", "lease_delta.docx"} {
		path := filepath.Join(dir, "contracts", name)
		at(t, m, now, time.Second, call("read_file", `{"file_path":`+jsonString(path)+`}`))
		at(t, m, now, time.Duration(2+i)*time.Second, result("read_file", `{"success":true}`))
	}
	at(t, m, now, 2*time.Second,
		goal("Extract clause terms into a table"),
		call("run_python", `{"code":"import docx, pandas as pd\nrows = []\nfor f in files:\n    rows.append(parse(f))\ndf = pd.DataFrame(rows)\nprint(df['Term'])"}`))
	at(t, m, now, 4*time.Second, event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "run_python",
		Data: json.RawMessage(`{"success":false,"error":"KeyError: 'Term'"}`),
	})
}

func argsJSON(v map[string]string) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func TestFrameRunningTurn(t *testing.T) {
	dir := t.TempDir()
	withPathBase(t, dir)
	m, now := frameChat(t, 100, 30)
	contractTurn(t, m, now, dir)
	at(t, m, now, 2*time.Second, call("run_shell_command", `{"command":"python -m pip install openpyxl --quiet","description":"Install the spreadsheet library"}`))
	*now = now.Add(95 * time.Second)
	golden(t, "running-turn", screen(*m))
}

func TestFrameFinishedTurn(t *testing.T) {
	dir := t.TempDir()
	withPathBase(t, dir)
	m, now := frameChat(t, 100, 40)
	contractTurn(t, m, now, dir)
	out := filepath.Join(dir, "contract_review.xlsx")
	at(t, m, now, 2*time.Second, call("run_python", argsJSON(map[string]string{
		"code": "import pandas as pd\ndf = build()\ndf.to_excel(" + jsonString(out) + ")\nprint('ok')",
	})))
	writeWorkbook(t, out, *now)
	at(t, m, now, 9*time.Second, result("run_python", `{"success":true,"stdout":"ok\n"}`))
	at(t, m, now, time.Second, call("write_file", `{"file_path":`+jsonString(filepath.Join(dir, "summary.md"))+
		`,"content":"# Contract review\n\nEight contracts reviewed.\nThree carry uncapped liability.\n\n- msa_acme\n- lease_delta\n- sow_gamma\n"}`))
	writeFile(t, filepath.Join(dir, "summary.md"), "# Contract review\n\nEight contracts reviewed.\n")
	touch(t, filepath.Join(dir, "summary.md"), *now)
	at(t, m, now, time.Second, result("write_file", `{"success":true}`))
	at(t, m, now, 3*time.Second, event.CanonicalFinalEvent{
		Type: "final", Answer: "Eight contracts reviewed. **Three** carry uncapped liability; the register is in contract_review.xlsx.",
		Usage: json.RawMessage(`{"steps":9,"tools_used":7,"tokens":9840}`),
	})
	golden(t, "finished-turn", screen(*m))
}

// The failure that mattered: nothing after it went through, and the turn ended.
func TestFrameRealFailure(t *testing.T) {
	m, now := frameChat(t, 100, 24)
	startTurn(m, *now, "Install formulas and verify the workbook")
	at(t, m, now, time.Second, call("run_shell_command", `{"command":"python -m pip install formulas --quiet"}`))
	at(t, m, now, 12*time.Second, event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "run_shell_command",
		Data: json.RawMessage(`{"success":false,"error":"Command failed (exit 1): No module named pip. Install pip into that venv with python -m ensurepip, or run the script with the system Python."}`),
	})
	at(t, m, now, time.Second, event.CanonicalErrorEvent{Type: "error", Detail: "the agent stopped: the step limit was reached"})
	golden(t, "real-failure", screen(*m))
}

// A recovered error is one calm line: the agent carried on and the turn
// answered, so nothing on screen is red.
func TestFrameRecoveredError(t *testing.T) {
	m, now := frameChat(t, 100, 24)
	startTurn(m, *now, "Install formulas")
	at(t, m, now, time.Second, call("run_shell_command", `{"command":"python -m pip install formulas --quiet"}`))
	at(t, m, now, 2*time.Second, event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "run_shell_command",
		Data: json.RawMessage(`{"success":false,"error":"No module named pip"}`),
	})
	at(t, m, now, time.Second, call("run_shell_command", `{"command":"uv pip install formulas"}`))
	at(t, m, now, 4*time.Second, result("run_shell_command", `{"success":true,"stdout":"Installed 1 package\n"}`))
	at(t, m, now, time.Second, event.CanonicalFinalEvent{Type: "final", Answer: "Installed formulas with uv."})
	got := screen(*m)
	golden(t, "recovered-error", got)
	var work []ActivityItem
	for _, msg := range m.messages {
		if msg.Role == RoleWork {
			work = msg.Work
		}
	}
	entries := buildEntries(work, false, true)
	if len(entries) < 2 {
		t.Fatalf("want the failed and the retried call as two bullets, got %d", len(entries))
	}
	if state := entryState(entries[0], laterSuccess(entries[1:])); state != "calm" {
		t.Errorf("a failure the agent carried on past reads as %q, want calm:\n%s", state, got)
	}
}

// A write shows as a compact diff: line numbers, +, and a tinted band.
func TestFrameDiff(t *testing.T) {
	dir := t.TempDir()
	withPathBase(t, dir)
	m, now := frameChat(t, 90, 30)
	path := filepath.Join(dir, "dates.py")
	writeFile(t, path, "import datetime\n\n\ndef parse(s):\n    return datetime.date.fromisoformat(s.strip())\n")
	startTurn(m, *now, "Fix the date parser")
	at(t, m, now, time.Second, call("edit_file", `{"file_path":`+jsonString(path)+
		`,"old_content":"def parse(s):\n    return datetime.date.fromisoformat(s)\n","new_content":"def parse(s):\n    return datetime.date.fromisoformat(s.strip())\n"}`))
	at(t, m, now, time.Second, result("edit_file", `{"success":true}`))
	at(t, m, now, time.Second, call("write_file", `{"file_path":`+jsonString(filepath.Join(dir, "test_dates.py"))+
		`,"content":"from dates import parse\n\n\ndef test_strips():\n    assert parse(' 2026-01-02 ').day == 2\n\n\ndef test_plain():\n    assert parse('2026-01-02').month == 1\n"}`))
	at(t, m, now, time.Second, result("write_file", `{"success":true}`))
	at(t, m, now, time.Second, event.CanonicalFinalEvent{Type: "final", Answer: "Fixed: `parse` now strips whitespace."})
	golden(t, "diff", screen(*m))

	// The tint and the syntax colour are there in the styled frame.
	styled := m.View()
	if !strings.Contains(styled, components.SyntaxLine(path, "    return datetime.date.fromisoformat(s.strip())", components.DiffAdded, 10)[:8]) {
		t.Errorf("the added line is not drawn as a styled diff row")
	}
}

// The permission prompt is never folded: badge, question, the exact command
// and the keys, in every frame.
func TestFramePermissionPrompt(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "Clean the build folder")
	at(t, m, now, time.Second,
		call("run_shell_command", `{"command":"rm -rf build/"}`),
		event.CanonicalNeedsConfirmationEvent{
			Type: "needs_confirmation", Action: "run_shell_command", Summary: "rm -rf build/",
			Risk: "destructive", ConfirmID: "c1", AlwaysScope: "rm",
		})
	*now = now.Add(4 * time.Second)
	got := screen(*m)
	golden(t, "permission-prompt", got)
	for _, want := range []string{"DESTRUCTIVE", "Run this command?", "rm -rf build/", "once", "n/esc"} {
		if !strings.Contains(got, want) {
			t.Errorf("the prompt lost %q:\n%s", want, got)
		}
	}
}

func TestFrameNarrowTerminal(t *testing.T) {
	dir := t.TempDir()
	withPathBase(t, dir)
	m, now := frameChat(t, 40, 24)
	contractTurn(t, m, now, dir)
	at(t, m, now, time.Second, call("run_python", `{"code":"import pandas as pd\ndf = pd.read_excel('contracts.xlsx')\ndf.to_excel('contract_review.xlsx')"}`))
	*now = now.Add(123 * time.Second)
	got := screen(*m)
	golden(t, "narrow-40", got)
	for i, row := range strings.Split(got, "\n") {
		if w := ansi.StringWidth(row); w > 40 {
			t.Errorf("row %d is %d columns on a 40-column terminal: %q", i, w, row)
		}
	}
}

// Over an hour, with six-figure token counts: the clock reads in hours and the
// count in thousands, on one row each.
func TestFrameLongTurn(t *testing.T) {
	m, now := frameChat(t, 100, 24)
	startTurn(m, *now, "Reconcile the AP ledger against every invoice")
	for i := 0; i < 30; i++ {
		at(t, m, now, 2*time.Minute, call("run_shell_command", fmt.Sprintf(
			`{"command":"python reconcile.py --batch %d","description":"Reconcile batch %d against its invoices"}`, i, i)))
		at(t, m, now, 10*time.Second, result("run_shell_command", `{"success":true}`))
	}
	*now = now.Add(13*time.Minute + 3*time.Second)
	at(t, m, now, 0, call("run_python", `{"code":"print(1)","description":"Total the reconciled batches"}`))
	golden(t, "long-turn-running", screen(*m))

	at(t, m, now, 4*time.Second, result("run_python", `{"success":true}`))
	at(t, m, now, time.Second, event.CanonicalFinalEvent{
		Type: "final", Answer: "All 30 batches reconcile.",
		Usage: json.RawMessage(`{"tokens":216100,"input_tokens":4100000}`),
	})
	got := screen(*m)
	golden(t, "long-turn-finished", got)
	for _, want := range []string{"1h 18m", "↓ 216.1k tokens", "more actions · Ctrl+O"} {
		if !strings.Contains(got, want) {
			t.Errorf("a long turn lost %q:\n%s", want, got)
		}
	}
}

func withPathBase(t *testing.T, dir string) {
	t.Helper()
	old := pathBase
	pathBase = dir
	t.Cleanup(func() { pathBase = old })
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// touch sets a file's mtime to the frame clock, so "changed during this turn"
// is decided by the same clock the turn runs on.
func touch(t *testing.T, path string, when time.Time) {
	t.Helper()
	if err := os.Chtimes(path, when, when); err != nil {
		t.Fatal(err)
	}
}
