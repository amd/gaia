// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// Copying the RENDERED answer would paste ANSI escape codes into the user's
// editor, which is never what "copy the answer" meant.
func TestCopyTakesTheSourceMarkdownNotTheRenderedAnswer(t *testing.T) {
	m := newTestChat(t)
	m.messages = []Message{
		{Role: RoleUser, Content: "how?"},
		{Role: RoleAssistant, Content: "Run **make**", Rendered: "\x1b[1mRun make\x1b[0m"},
	}

	got := m.lastAnswer()
	if got != "Run **make**" {
		t.Errorf("copied %q; want the markdown source", got)
	}
	if strings.Contains(got, "\x1b") {
		t.Error("escape codes would be pasted into the user's editor")
	}
}

// The newest answer wins, and a later status note must not shadow it.
func TestCopyPicksTheMostRecentAnswer(t *testing.T) {
	m := newTestChat(t)
	m.messages = []Message{
		{Role: RoleAssistant, Content: "first"},
		{Role: RoleAssistant, Content: "second"},
		{Role: RoleStatus, Content: "cancelled"},
	}
	if got := m.lastAnswer(); got != "second" {
		t.Errorf("copied %q; want the newest answer", got)
	}
}

func TestLastCodeBlock(t *testing.T) {
	for _, tc := range []struct {
		name, in, want string
	}{
		{
			name: "the last block of several",
			in:   "intro\n```sh\nfirst\n```\nmiddle\n```go\nsecond\n```\ntail",
			want: "second",
		},
		{
			name: "a multi-line block keeps its shape",
			in:   "```python\nimport os\n\nprint(os.getcwd())\n```",
			want: "import os\n\nprint(os.getcwd())",
		},
		{
			// A streamed answer cut off mid-block still has something worth
			// taking; refusing to copy it would be the less useful reading.
			name: "an unterminated fence yields what followed it",
			in:   "here you go\n```sh\nmake test",
			want: "make test",
		},
		{
			name: "prose alone has nothing to copy",
			in:   "no fences here at all",
			want: "",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := lastCodeBlock(tc.in); got != tc.want {
				t.Errorf("lastCodeBlock() = %q, want %q", got, tc.want)
			}
		})
	}
}

// OSC 52 is write-only: nothing reports back, so a terminal that ignores the
// sequence does so silently. Claiming "copied!" over an empty clipboard is
// worse than a hedged sentence.
func TestTheCopyMessageDoesNotPromiseWhatItCannotKnow(t *testing.T) {
	ok := copyHint("answer", nil)
	if !strings.Contains(ok, "OSC 52") {
		t.Errorf("the hint hides that support is not universal: %q", ok)
	}
	for _, bad := range []string{"copied!", "copied to clipboard."} {
		if strings.EqualFold(strings.TrimSpace(ok), bad) {
			t.Errorf("the hint claims success it cannot verify: %q", ok)
		}
	}

	failed := copyHint("answer", errors.New("stdout closed"))
	if !strings.Contains(failed, "stdout closed") {
		t.Errorf("a real failure lost its cause: %q", failed)
	}
}

// The sequence has to be the real OSC 52, or terminals ignore it.
func TestTheClipboardCommandEmitsOSC52(t *testing.T) {
	seq := ansi.SetClipboard(ansi.SystemClipboard, "hello")
	if !strings.HasPrefix(seq, "\x1b]52;c;") {
		t.Errorf("not an OSC 52 system-clipboard sequence: %q", seq)
	}
}

// Ctrl+B on an answer with no fenced block must say so, not fail silently.
func TestCopyingACodeBlockThatIsNotThereSaysSo(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m.messages = []Message{{Role: RoleAssistant, Content: "just prose"}}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlB})
	after := updated.(ChatModel)
	if cmd != nil {
		t.Error("a missing code block should not send anything to the clipboard")
	}
	last := after.messages[len(after.messages)-1]
	if !strings.Contains(last.Content, "no code block") {
		t.Errorf("nothing told the user why nothing happened: %q", last.Content)
	}
}

// An empty transcript must report rather than send an empty clipboard.
func TestCopyingWithNoAnswerYetReportsIt(t *testing.T) {
	m := newTestChat(t)
	cmd := copyToClipboard(m.lastAnswer(), "answer")
	res, ok := cmd().(clipboardResultMsg)
	if !ok || res.err == nil {
		t.Fatalf("copying an empty transcript reported success: %+v", res)
	}
}
