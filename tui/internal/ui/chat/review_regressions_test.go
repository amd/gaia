// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// A turn that dies while a step runs keeps that step on record — it is the one
// that explains how the turn ended — and does not paint it as done.
func TestATurnThatDiesMidStepKeepsThatStep(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "Run the tests")
	at(t, m, now, time.Second, call("run_shell_command", `{"command":"python -m pytest -q"}`))
	at(t, m, now, 30*time.Second, event.CanonicalErrorEvent{Type: "error", Detail: "the agent process exited"})

	var work []ActivityItem
	for _, msg := range m.messages {
		if msg.Role == RoleWork {
			work = msg.Work
		}
	}
	out := ansi.Strip(m.renderWork(&Message{Role: RoleWork, Work: work}))
	if !strings.Contains(out, "Run the tests") && !strings.Contains(out, "pytest") {
		t.Fatalf("the step that was running when the turn died is gone:\n%s", out)
	}
	if !strings.Contains(out, "did not finish") {
		t.Errorf("an interrupted step reads as finished:\n%s", out)
	}
	if state := entryState(buildEntries(work, false, true)[0], true); state == "ok" {
		t.Error("an interrupted step got a done dot")
	}
}

// Two calls open at once: the first one is not "done" just because it is not
// the newest.
func TestAnOverlappedOpenCallIsNotDone(t *testing.T) {
	e := workEntry{kind: "act", items: []ActivityItem{{Kind: "tool", Tool: "search"}}}
	if state := entryState(e, true); state != "open" {
		t.Errorf("a call with no result is %q, want open", state)
	}
}

// A path the model only NAMES is looked at under the start folder or home, and
// never on the network. A path a call was asked to write is a deliverable
// wherever it is.
func TestDeliverablesOutsideTheLocalRootsAreNotTouched(t *testing.T) {
	base, home, elsewhere := t.TempDir(), t.TempDir(), t.TempDir()
	withPaths(t, base, home)
	start := time.Now()
	outside := filepath.Join(elsewhere, "secret.xlsx")
	writeWorkbook(t, outside, start.Add(time.Second))

	if got := findDeliverables(nil, "see "+outside, start); len(got) != 0 {
		t.Errorf("a path the answer only named, outside base and home, was statted: %+v", got)
	}
	written := []ActivityItem{{Kind: "tool", Tool: "write_file", Done: true, Call: callInfo{Category: catWrite, Writes: []string{outside}}}}
	if got := findDeliverables(written, "", start); len(got) != 1 {
		t.Errorf("a file the agent was asked to write is missing: %+v", got)
	}
	for _, p := range []string{`\\server\share\leak.xlsx`, `//server/share/leak.xlsx`, `\\?\C:\x\leak.xlsx`} {
		if !remotePath(p) {
			t.Errorf("%q is not treated as remote", p)
		}
	}
	unc := []ActivityItem{{Kind: "tool", Tool: "write_file", Done: true, Call: callInfo{Category: catWrite, Writes: []string{`\\server\share\leak.xlsx`}}}}
	if got := findDeliverables(unc, `\\server\share\leak.xlsx`, start); len(got) != 0 {
		t.Errorf("a network path was looked at: %+v", got)
	}
}

// A file name is model-made text; it reaches the screen scrubbed. The link
// target is percent-encoded, so no raw escape survives anywhere in the row.
func TestADeliverableNameIsScrubbed(t *testing.T) {
	m, _ := frameChat(t, 100, 20)
	d := deliverable{Path: filepath.Join(os.TempDir(), "evil\x1b[2J\u202ename.xlsx"), Size: 10}
	rows := m.renderDeliverables([]deliverable{d})
	for _, r := range rows {
		if strings.Contains(r, "\x1b[2J") || strings.Contains(r, "\u202e") {
			t.Errorf("a control sequence from the file name reached the row: %q", r)
		}
	}
}

// A prompt that times out gives its rows back to the status line; the frame is
// never taller than the terminal while it does.
func TestATimedOutPromptKeepsTheFrameInsideTheTerminal(t *testing.T) {
	m, _ := liveModel(t)
	m.width, m.height = 100, 24
	m.resize()
	m.streaming = true
	m.queryStart = time.Now()
	m = feed(t, m, shellCall("pwd"), gatedShellCall())
	updated, _ := m.Update(components.ConfirmationTimeoutMsg{RunID: "run-1"})
	m = updated.(ChatModel)
	if rows := len(strings.Split(m.View(), "\n")); rows > m.height {
		t.Errorf("the frame is %d rows on a %d-row terminal after the prompt timed out", rows, m.height)
	}
}
