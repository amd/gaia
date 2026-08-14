// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// Bubble Tea v1.3.10 enables bracketed paste by default, so a real terminal
// paste arrives as exactly this: one KeyMsg, Paste true, the whole block —
// newlines included — in Runes, never a burst of individual keystrokes.
func TestBracketedPasteLandsWholeWithoutSubmitting(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	pasted := "first line\nsecond line\n  third line indented"
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(pasted), Paste: true})
	after := updated.(ChatModel)

	if got := after.input.Value(); got != pasted {
		t.Fatalf("paste landed as %q, want the block intact: %q", got, pasted)
	}
	if len(after.messages) != 0 {
		t.Errorf("a multi-line paste submitted the turn instead of composing it: %+v", after.messages)
	}
	if after.input.Height() != 3 {
		t.Errorf("composer height did not resync to the pasted line count: got %d, want 3", after.input.Height())
	}
	if cmd != nil {
		t.Error("a paste that only fills the composer should not also send a command")
	}
}

// Windows clipboard text carries \r\n. The textarea's own sanitizer treats \r
// and \n as two separate newlines, so passing that straight through doubles
// every line break into a blank one.
func TestBracketedPasteNormalizesWindowsLineEndings(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("one\r\ntwo\r\nthree"), Paste: true})
	after := updated.(ChatModel)

	if got := after.input.Value(); got != "one\ntwo\nthree" {
		t.Errorf("CRLF paste produced %q, want CRLF collapsed to a single newline", got)
	}
}

// A paste never submits, even one ending in what looks like a trailing
// newline — Enter is what submits, not the shape of what landed.
func TestBracketedPasteEndingInNewlineDoesNotSubmit(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("draft this\n"), Paste: true})
	after := updated.(ChatModel)

	if len(after.messages) != 0 {
		t.Errorf("a trailing newline in the paste was read as Enter: %+v", after.messages)
	}
}

func TestNormalizePastedText(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"a\r\nb", "a\nb"},
		{"a\rb", "a\nb"},
		{"a\nb", "a\nb"},
		{"no newlines", "no newlines"},
	} {
		if got := normalizePastedText(tc.in); got != tc.want {
			t.Errorf("normalizePastedText(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// Ctrl+V is wired to a real clipboard read, not left to the textarea's own
// binding, whose failures land on a field ChatModel never reads.
func TestCtrlVReadsTheClipboard(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	_, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlV})
	if cmd == nil {
		t.Fatal("Ctrl+V issued no command")
	}
	if _, ok := cmd().(pasteClipboardMsg); !ok {
		t.Error("Ctrl+V did not read the clipboard")
	}
}

func TestSuccessfulClipboardPasteLandsInTheComposer(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.Update(pasteClipboardMsg{text: "pasted via ctrl+v"})
	after := updated.(ChatModel)

	if got := after.input.Value(); got != "pasted via ctrl+v" {
		t.Errorf("composer holds %q, want the clipboard text", got)
	}
	if len(after.messages) != 0 {
		t.Errorf("a successful paste should not post a status line: %+v", after.messages)
	}
}

// A clipboard read that fails must say so — a key that appears to do nothing
// reads as broken, not as "nothing was on the clipboard".
func TestFailedClipboardPasteSaysSo(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.Update(pasteClipboardMsg{err: errors.New("clipboard unavailable")})
	after := updated.(ChatModel)

	if after.input.Value() != "" {
		t.Error("a failed read should not have put anything in the composer")
	}
	if len(after.messages) == 0 {
		t.Fatal("a failed Ctrl+V paste was silent")
	}
	last := after.messages[len(after.messages)-1]
	if !strings.Contains(last.Content, "clipboard unavailable") {
		t.Errorf("the failure's cause was lost: %q", last.Content)
	}
	if !strings.Contains(last.Content, "Ctrl+T") {
		t.Errorf("no fallback offered when Ctrl+V cannot read the clipboard: %q", last.Content)
	}
}

// Windows Terminal over ConPTY does not bracket its pastes at all — confirmed
// live, not just in bubbletea's docs: it types the clipboard text out,
// newlines included, as ordinary keystrokes with a real Enter between every
// line (microsoft/terminal#395, the same failure independently reported
// against zellij and Git Bash on Windows). Reproduced here as what that looks
// like at the KeyMsg level: runes and a real Enter, back to back, with none
// of the reaction time a human leaves between typing and sending.
func TestUnbracketedPasteDoesNotFragmentIntoSeparateSends(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	for _, r := range "line one" {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(ChatModel)
	}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)
	for _, r := range "line two" {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(ChatModel)
	}

	if len(m.messages) != 0 {
		t.Fatalf("an unbracketed paste's newline was read as Enter and sent a turn: %+v", m.messages)
	}
	if got := m.input.Value(); got != "line one\nline two" {
		t.Errorf("composer holds %q, want both lines joined by the newline Enter should have inserted", got)
	}
}

// The guard must not cost a real user their Enter forever: once actual time
// has passed (a human reacting, not a terminal flooding stdin), Enter sends.
func TestEnterSendsNormallyOnceEnoughTimeHasPassed(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m = typeInto(t, m, "a real message")

	m, _ = press(t, m, tea.KeyEnter)

	if got := m.input.Value(); got != "" {
		t.Errorf("Enter after a real pause left %q in the composer instead of sending it", got)
	}
}

// An empty clipboard is not an error, but it still needs to say something —
// otherwise it looks identical to the paste silently failing.
func TestEmptyClipboardPasteSaysSo(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.Update(pasteClipboardMsg{text: "   "})
	after := updated.(ChatModel)

	if len(after.messages) == 0 {
		t.Fatal("pasting an empty clipboard was silent")
	}
	last := after.messages[len(after.messages)-1]
	if !strings.Contains(last.Content, "empty") {
		t.Errorf("nothing told the user the clipboard was empty: %q", last.Content)
	}
}
