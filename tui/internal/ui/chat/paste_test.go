// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"os"
	"path/filepath"
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
//
// This reads the machine's ACTUAL clipboard (see pasteFromClipboardOrImage),
// so either message type is a pass: an image happening to be on the
// clipboard when this runs (a screenshot left over from testing Ctrl+V
// itself, say) is Ctrl+V correctly doing its job, not a failure — the
// image-vs-text tests below cover each path's own content deterministically.
func TestCtrlVReadsTheClipboard(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	_, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyCtrlV})
	if cmd == nil {
		t.Fatal("Ctrl+V issued no command")
	}
	switch cmd().(type) {
	case pasteClipboardMsg, pasteImageMsg:
	default:
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

// Windows Terminal over ConPTY does not bracket its pastes: it types the
// clipboard text out, newlines included, as ordinary keystrokes with a real
// Enter between the lines (microsoft/terminal#395, and the same failure reported
// against zellij and Git Bash on Windows). So on those terminals a multi-line
// paste into the composer DOES send the first line, and this test pins that as
// the accepted behaviour rather than pretending otherwise.
//
// The alternative was inferring a paste from keystroke timing — "this Enter came
// too fast to be a person". That was tried and reverted: it also swallows the
// Enter of a fast typist and of every programmatic driver, including this TUI's
// own control API, which took the capability ladder from 7/7 to 0/7 with no
// error anywhere. An Enter key that sometimes does nothing is a worse defect
// than a paste that needs a different key.
//
// Ctrl+V is that key: it reads the clipboard directly, so multi-line paste works
// on these terminals without guessing.
func TestAnUnbracketedPasteSendsItsFirstLine(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	for _, r := range "line one" {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(ChatModel)
	}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)

	if len(m.messages) == 0 {
		t.Fatal("Enter did not send — the timing heuristic is back, and with it a " +
			"key that silently does nothing for fast typists and for the control API")
	}
	if m.input.Value() != "" {
		t.Errorf("composer was not cleared by the send: %q", m.input.Value())
	}
}

// The property that must never regress again: Enter sends, however fast it
// arrives after the last keystroke. This is what the control API relies on.
func TestEnterAlwaysSendsHoweverFastItArrives(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	// No pause anywhere — exactly how the control API and a paste-typing
	// terminal both deliver keys.
	for _, r := range "hello" {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(ChatModel)
	}
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)

	var sent bool
	for _, msg := range m.messages {
		if msg.Role == RoleUser && msg.Content == "hello" {
			sent = true
		}
	}
	if !sent {
		t.Fatalf("an immediate Enter did not send: %+v", m.messages)
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

// --- pasting an IMAGE (a screenshot) -------------------------------------------

// A successful image paste inserts the file's PATH into the composer — the
// agent is handed something it can read as a file, not raw image bytes — and
// says so, with the path, so the user can tell it grabbed the right thing.
func TestSuccessfulImagePasteInsertsThePath(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	path := filepath.Join(t.TempDir(), "gaia-paste-test.png")
	if err := os.WriteFile(path, []byte{1, 2, 3, 4}, 0o600); err != nil {
		t.Fatalf("test setup: %v", err)
	}

	updated, _ := m.Update(pasteImageMsg{path: path})
	after := updated.(ChatModel)

	if after.input.Value() != path {
		t.Errorf("composer = %q, want the pasted image's path %q", after.input.Value(), path)
	}
	if len(after.messages) == 0 {
		t.Fatal("a successful image paste was silent")
	}
	last := after.messages[len(after.messages)-1]
	if !strings.Contains(last.Content, path) {
		t.Errorf("status line does not name the file: %q", last.Content)
	}
}

// A path with a space (the common case: %TEMP% usually contains the Windows
// profile directory name) must be quoted the same way Windows Terminal
// already quotes a dropped file's path — see quotePathForComposer and
// docs/guides/terminal-hub.mdx's drag-and-drop section.
func TestImagePasteQuotesAPathWithASpace(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	sub := filepath.Join(t.TempDir(), "with space")
	if err := os.MkdirAll(sub, 0o700); err != nil {
		t.Fatalf("test setup: %v", err)
	}
	path := filepath.Join(sub, "gaia-paste-test.png")
	if err := os.WriteFile(path, []byte{1, 2, 3, 4}, 0o600); err != nil {
		t.Fatalf("test setup: %v", err)
	}

	updated, _ := m.Update(pasteImageMsg{path: path})
	after := updated.(ChatModel)

	want := `"` + path + `"`
	if after.input.Value() != want {
		t.Errorf("composer = %q, want the quoted path %q", after.input.Value(), want)
	}
}

// A failed image read/decode must say so — silence reads as Ctrl+V doing
// nothing, same as the existing text-paste failure contract.
func TestFailedImagePasteSaysSo(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false

	updated, _ := m.Update(pasteImageMsg{err: errors.New("clipboard image format unsupported")})
	after := updated.(ChatModel)

	if after.input.Value() != "" {
		t.Error("a failed image read should not have put anything in the composer")
	}
	if len(after.messages) == 0 {
		t.Fatal("a failed image paste was silent")
	}
	last := after.messages[len(after.messages)-1]
	if !strings.Contains(last.Content, "clipboard image format unsupported") {
		t.Errorf("the failure's cause was lost: %q", last.Content)
	}
}

// --- pure helpers: writeClipboardImageToTemp, formatByteSize, quotePathForComposer --

func TestWriteClipboardImageToTempWritesARealFile(t *testing.T) {
	png := []byte{0x89, 'P', 'N', 'G', 1, 2, 3, 4}
	path, err := writeClipboardImageToTemp(png)
	if err != nil {
		t.Fatalf("writeClipboardImageToTemp: %v", err)
	}
	defer os.Remove(path)

	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("could not read back %s: %v", path, err)
	}
	if string(got) != string(png) {
		t.Errorf("file contents = %v, want %v", got, png)
	}
	if !strings.HasSuffix(path, ".png") {
		t.Errorf("temp path %q does not look like a .png file", path)
	}
}

func TestQuotePathForComposer(t *testing.T) {
	cases := []struct{ in, want string }{
		{`C:\Users\kalin\AppData\Local\Temp\gaia-paste-1.png`, `C:\Users\kalin\AppData\Local\Temp\gaia-paste-1.png`},
		{`C:\Users\John Doe\AppData\Local\Temp\gaia-paste-1.png`, `"C:\Users\John Doe\AppData\Local\Temp\gaia-paste-1.png"`},
	}
	for _, c := range cases {
		if got := quotePathForComposer(c.in); got != c.want {
			t.Errorf("quotePathForComposer(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestFormatByteSize(t *testing.T) {
	cases := []struct {
		n    int
		want string
	}{
		{500, "500 B"},
		{2048, "2.0 KB"},
		{5 * 1024 * 1024, "5.0 MB"},
	}
	for _, c := range cases {
		if got := formatByteSize(c.n); got != c.want {
			t.Errorf("formatByteSize(%d) = %q, want %q", c.n, got, c.want)
		}
	}
}
