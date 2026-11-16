// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// idleChat is a sized, idle chat (sizedChat leaves a turn streaming).
func idleChat(t *testing.T, w, h int, msgs ...Message) ChatModel {
	t.Helper()
	m := sizedChat(t, w, h)
	m.streaming = false
	m.appMouse = true
	m.messages = append(m.messages, msgs...)
	m.updateViewport()
	return m
}

func numberedStatus(n int) []Message {
	out := make([]Message, n)
	for i := range out {
		out[i] = Message{Role: RoleStatus, Content: fmt.Sprintf("line %02d", i)}
	}
	return out
}

func mouse(t *testing.T, m ChatModel, action tea.MouseAction, x, y int) (ChatModel, tea.Cmd) {
	t.Helper()
	next, cmd := m.handleTranscriptMouse(tea.MouseMsg{
		X: x, Y: y, Action: action, Button: tea.MouseButtonLeft,
	})
	return next.(ChatModel), cmd
}

// captureClipboard swaps the OSC 52 writer for a buffer and returns a func that
// runs a copy command and decodes what reached the "terminal".
func captureClipboard(t *testing.T) func(tea.Cmd) string {
	t.Helper()
	var buf bytes.Buffer
	prev := clipboardOut
	clipboardOut = &buf
	t.Cleanup(func() { clipboardOut = prev })
	return func(cmd tea.Cmd) string {
		t.Helper()
		if cmd == nil {
			t.Fatal("no copy command was returned")
		}
		buf.Reset()
		res, ok := cmd().(clipboardResultMsg)
		if !ok {
			t.Fatalf("the command did not copy: got %T", res)
		}
		if res.err != nil {
			t.Fatalf("copy failed: %v", res.err)
		}
		return decodeOSC52(t, buf.String())
	}
}

// decodeOSC52 checks seq is exactly one system-clipboard OSC 52 and returns its
// decoded payload.
func decodeOSC52(t *testing.T, seq string) string {
	t.Helper()
	const prefix, suffix = "\x1b]52;c;", "\x07"
	if !strings.HasPrefix(seq, prefix) || !strings.HasSuffix(seq, suffix) {
		t.Fatalf("not an OSC 52 system-clipboard sequence: %q", seq)
	}
	payload := strings.TrimSuffix(strings.TrimPrefix(seq, prefix), suffix)
	if strings.ContainsAny(payload, "\x1b\x07\n") {
		t.Fatalf("payload is not plain base64: %q", payload)
	}
	raw, err := base64.StdEncoding.DecodeString(payload)
	if err != nil {
		t.Fatalf("payload is not valid base64: %v", err)
	}
	return string(raw)
}

// rowOf finds the screen row showing text, or fails.
func rowOf(t *testing.T, m ChatModel, text string) int {
	t.Helper()
	top := m.contentHeaderRows()
	for i := 0; i < m.viewport.Height; i++ {
		line, ok := m.viewportLineAt(top + i)
		if ok && strings.Contains(line, text) {
			return top + i
		}
	}
	t.Fatalf("%q is not on screen", text)
	return 0
}

func TestOSC52PayloadDecodesToTheExactText(t *testing.T) {
	read := captureClipboard(t)
	want := "tab\there\nünïcödé — and a ``` fence\n"
	if got := read(copyToClipboard(want, "x")); got != want {
		t.Errorf("clipboard got %q, want %q", got, want)
	}
}

// Content positions stay put while the viewport scrolls under them, so a
// selection started on one screen and finished on another covers every line
// in between.
func TestSelectionRangeSurvivesScrolling(t *testing.T) {
	m := idleChat(t, 80, 14, numberedStatus(40)...)
	m.viewport.GotoTop()
	m = m.afterScroll()

	y0 := rowOf(t, m, "line 02")
	m, _ = mouse(t, m, tea.MouseActionPress, 2, y0)
	if m.sel.anchor.row != 2 {
		t.Fatalf("anchor row = %d, want content row 2", m.sel.anchor.row)
	}

	// Scroll with the wheel mid-drag, then move to a row only now on screen.
	for i := 0; i < 5; i++ {
		next, _ := m.handleTranscriptMouse(tea.MouseMsg{Action: tea.MouseActionPress, Button: tea.MouseButtonWheelDown})
		m = next.(ChatModel)
	}
	if m.viewport.YOffset == 0 {
		t.Fatal("test setup: the wheel did not scroll")
	}
	target := m.viewport.YOffset + m.viewport.Height - 2
	if target <= m.viewport.Height {
		t.Fatalf("test setup: row %d was already on the first screen", target)
	}
	y1 := rowOf(t, m, fmt.Sprintf("line %02d", target))
	m, _ = mouse(t, m, tea.MouseActionMotion, 9, y1)
	if m.sel.head.row != target {
		t.Fatalf("head row = %d, want content row %d", m.sel.head.row, target)
	}

	read := captureClipboard(t)
	m, cmd := mouse(t, m, tea.MouseActionRelease, 9, y1)
	got := read(cmd)
	var want []string
	for i := 2; i <= target; i++ {
		want = append(want, fmt.Sprintf("line %02d", i))
	}
	if got != strings.Join(want, "\n") {
		t.Errorf("selection copied:\n%s\nwant lines 02..25", got)
	}
	if !m.sel.active || m.sel.dragging {
		t.Error("the selection should stay highlighted after the copy")
	}
}

// A drag that rests past the bottom edge keeps scrolling and keeps extending
// the selection, with no further pointer events — the whole point of #5085.
func TestDragPastTheEdgeAutoScrolls(t *testing.T) {
	m := idleChat(t, 80, 14, numberedStatus(60)...)
	m.viewport.GotoTop()
	m = m.afterScroll()

	top := m.contentHeaderRows()
	m, _ = mouse(t, m, tea.MouseActionPress, 2, top)
	below := top + m.viewport.Height + 1
	m, cmd := mouse(t, m, tea.MouseActionMotion, 4, below)
	if cmd == nil || !m.sel.ticking {
		t.Fatal("dragging past the bottom edge did not start auto-scroll")
	}
	// A second motion past the edge must not start a second ticker.
	if _, again := mouse(t, m, tea.MouseActionMotion, 5, below); again != nil {
		t.Error("each motion event stacked another auto-scroll ticker")
	}

	start := m.viewport.YOffset
	for i := 0; i < 5; i++ {
		next, tick := m.Update(selectScrollTickMsg{})
		m = next.(ChatModel)
		if tick == nil {
			t.Fatalf("tick %d stopped scrolling while the pointer is still past the edge", i)
		}
	}
	if m.viewport.YOffset <= start {
		t.Fatalf("auto-scroll did not move the view: YOffset %d → %d", start, m.viewport.YOffset)
	}
	bottomRow := m.viewport.YOffset + m.viewport.Height - 1
	if m.sel.head.row != bottomRow {
		t.Errorf("head row = %d, want the last visible row %d", m.sel.head.row, bottomRow)
	}

	// Back inside: the next tick stands down.
	m, _ = mouse(t, m, tea.MouseActionMotion, 4, top+1)
	next, tick := m.Update(selectScrollTickMsg{})
	m = next.(ChatModel)
	if tick != nil || m.sel.ticking {
		t.Error("auto-scroll kept running after the pointer came back inside")
	}

	// And up past the top edge scrolls back.
	m, _ = mouse(t, m, tea.MouseActionMotion, 4, 0)
	before := m.viewport.YOffset
	next, _ = m.Update(selectScrollTickMsg{})
	m = next.(ChatModel)
	if m.viewport.YOffset >= before {
		t.Errorf("dragging above the top edge did not scroll up: %d → %d", before, m.viewport.YOffset)
	}
	if m.sel.head.row != m.viewport.YOffset {
		t.Errorf("head row = %d, want the first visible row %d", m.sel.head.row, m.viewport.YOffset)
	}
}

// A drag that starts on a link selects; it must not open a browser on press.
func TestADragFromALinkSelectsInsteadOfOpeningIt(t *testing.T) {
	m := idleChat(t, 100, 30, Message{Role: RoleStatus, Content: "see https://example.com/x and more"})
	x, y, ok := findLink(m, "https://example.com/x")
	if !ok {
		t.Fatal("test setup: link not rendered")
	}
	m, cmd := mouse(t, m, tea.MouseActionPress, x, y)
	if cmd != nil {
		t.Fatal("a press on a link did something before the gesture was known")
	}
	m, _ = mouse(t, m, tea.MouseActionMotion, x+7, y)
	read := captureClipboard(t)
	_, cmd = mouse(t, m, tea.MouseActionRelease, x+7, y)
	if got := read(cmd); got != "https://" {
		t.Errorf("drag copied %q, want the dragged-over text", got)
	}
}

func TestShiftClickExtendsTheSelection(t *testing.T) {
	m := idleChat(t, 80, 30, numberedStatus(10)...)
	y2, y6 := rowOf(t, m, "line 02"), rowOf(t, m, "line 06")
	m, _ = mouse(t, m, tea.MouseActionPress, 2, y2)
	m, _ = mouse(t, m, tea.MouseActionMotion, 8, y2+1)
	m, _ = mouse(t, m, tea.MouseActionRelease, 8, y2+1)

	read := captureClipboard(t)
	next, _ := m.handleTranscriptMouse(tea.MouseMsg{
		X: 8, Y: y6, Shift: true, Action: tea.MouseActionPress, Button: tea.MouseButtonLeft,
	})
	m = next.(ChatModel)
	m, cmd := mouse(t, m, tea.MouseActionRelease, 8, y6)
	want := "line 02\nline 03\nline 04\nline 05\nline 06"
	if got := read(cmd); got != want {
		t.Errorf("shift+click copied %q, want %q", got, want)
	}
	if m.sel.anchor.row != 2 {
		t.Errorf("shift+click moved the anchor to row %d", m.sel.anchor.row)
	}
}

func TestEnterRecopiesAndEscClearsTheSelection(t *testing.T) {
	m := idleChat(t, 80, 30, numberedStatus(5)...)
	y := rowOf(t, m, "line 01")
	m, _ = mouse(t, m, tea.MouseActionPress, 2, y)
	m, _ = mouse(t, m, tea.MouseActionMotion, 8, y+1)
	m, _ = mouse(t, m, tea.MouseActionRelease, 8, y+1)

	read := captureClipboard(t)
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if got := read(cmd); got != "line 01\nline 02" {
		t.Errorf("Enter copied %q", got)
	}
	next, _ = next.(ChatModel).handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	if next.(ChatModel).sel.active {
		t.Error("Esc left the selection up")
	}
}

// Everything a person did not write — the "▶ You:" label, the answer's
// padding, an error's title band, the timing line — stays out of the copy.
func TestSelectionCopyLeavesTheChromeOut(t *testing.T) {
	m := idleChat(t, 100, 40,
		Message{Role: RoleUser, Content: "what is up"},
		Message{Role: RoleAssistant, Content: "All good.", Rendered: "x", Duration: 2 * time.Second},
		Message{Role: RoleError, Content: "the gateway refused"},
	)
	lines := strings.Split(m.vpContent, "\n")
	// From mid-way through the question's label to mid-way through the error.
	user := rowOf(t, m, "You:") - m.contentHeaderRows()
	errRow := rowOf(t, m, "the gateway refused") - m.contentHeaderRows()
	got := extractSelection(lines, m.msgSpans, m.messages,
		selPos{row: user, col: 3}, selPos{row: errRow, col: 2 + len("the gateway") - 1})

	want := "what is up\n\nAll good.\n\nthe gateway"
	if got != want {
		t.Errorf("copied:\n%q\nwant:\n%q", got, want)
	}
	for _, chrome := range []string{"You:", "▶", "2.0s", "ERROR"} {
		if strings.Contains(got, chrome) {
			t.Errorf("copy carries chrome %q: %q", chrome, got)
		}
	}
}

const codeAnswer = "Here:\n\n```python\ndef f(x):\n    if x:\n        return 1\n    return 0\n```\n\nDone."

// Part of a code block, selected row by row, comes back with its own
// indentation — not the answer panel's, not the block's margin.
func TestPartialSelectionKeepsCodeIndentation(t *testing.T) {
	m := idleChat(t, 100, 40, Message{Role: RoleAssistant, Content: codeAnswer, Rendered: "x"})
	from := rowOf(t, m, "if x:") - m.contentHeaderRows()
	to := rowOf(t, m, "return 1") - m.contentHeaderRows()
	got := extractSelection(strings.Split(m.vpContent, "\n"), m.msgSpans, m.messages,
		selPos{row: from, col: 0}, selPos{row: to, col: 99})
	if want := "    if x:\n        return 1"; got != want {
		t.Errorf("copied %q, want %q", got, want)
	}
}

// A message selected end to end comes back as its source, so a code block is
// exact and the markdown is intact.
func TestWholeMessageSelectionCopiesTheSource(t *testing.T) {
	m := idleChat(t, 100, 40,
		Message{Role: RoleUser, Content: "show me"},
		Message{Role: RoleAssistant, Content: codeAnswer, Rendered: "x", Duration: time.Second},
	)
	var ans msgSpan
	for _, sp := range m.msgSpans {
		if sp.index == 1 {
			ans = sp
		}
	}
	got := extractSelection(strings.Split(m.vpContent, "\n"), m.msgSpans, m.messages,
		selPos{row: ans.start, col: 0}, selPos{row: ans.end - 1, col: 99})
	if got != codeAnswer {
		t.Errorf("copied:\n%s\nwant the source markdown:\n%s", got, codeAnswer)
	}
}

func TestCardBordersAreStripped(t *testing.T) {
	for _, tc := range []struct {
		in   string
		want string
		keep bool
	}{
		{"╭──────╮", "", false},
		{"│ a@b.c  subject │", "a@b.c  subject", true},
		{"  plain  ", "plain", true},
	} {
		got, keep := stripBorders(tc.in)
		if got != tc.want || keep != tc.keep {
			t.Errorf("stripBorders(%q) = %q, %v; want %q, %v", tc.in, got, keep, tc.want, tc.keep)
		}
	}
	if got := stripLeadingGlyph("  ▪ Reading file: a.go"); got != "Reading file: a.go" {
		t.Errorf("work-log glyph kept: %q", got)
	}
}

func TestCopyAllSendsTheTranscriptAsMarkdown(t *testing.T) {
	m := idleChat(t, 100, 30,
		Message{Role: RoleUser, Content: "fix the test"},
		Message{Role: RoleWork, Work: []ActivityItem{{Content: "Reading file: a.go", Detail: "3 symbols"}}},
		Message{Role: RoleAssistant, Content: "Fixed.\n\n```go\nx := 1\n```"},
		Message{Role: RoleStatus, Content: "answer sent to the clipboard"},
		Message{Role: RoleError, Content: "one\ntwo"},
	)
	m.agentName = "GAIA"
	want := "## You\n\nfix the test\n\n" +
		"- Reading file: a.go — 3 symbols\n\n" +
		"## GAIA\n\nFixed.\n\n```go\nx := 1\n```\n\n" +
		"> **Error:** one\n> two\n"
	if got := transcriptMarkdown(m.messages, m.agentName); got != want {
		t.Fatalf("transcript:\n%q\nwant:\n%q", got, want)
	}

	read := captureClipboard(t)
	_, cmd := m.submit("/copy all")
	if got := read(cmd); got != want {
		t.Errorf("/copy all sent %q", got)
	}
}

func TestCopyPicksAMessageWithTheKeyboard(t *testing.T) {
	m := idleChat(t, 100, 30,
		Message{Role: RoleUser, Content: "first question"},
		Message{Role: RoleAssistant, Content: "first **answer**", Rendered: "x"},
		Message{Role: RoleUser, Content: "second question"},
		Message{Role: RoleAssistant, Content: "second answer", Rendered: "x"},
	)
	next, _ := m.submit("/copy")
	m = next.(ChatModel)
	if !m.pick.open || m.pick.index != 3 {
		t.Fatalf("/copy should start on the latest answer, got open=%v index=%d", m.pick.open, m.pick.index)
	}
	for i := 0; i < 2; i++ {
		next, _ = m.handleKey(tea.KeyMsg{Type: tea.KeyUp})
		m = next.(ChatModel)
	}
	if m.pick.index != 1 {
		t.Fatalf("two ↑ from the last answer landed on %d, want 1", m.pick.index)
	}
	// The picked message is highlighted on screen.
	if lo, hi, ok := m.selectedCols(m.msgSpans[1].start, strings.Split(m.viewport.View(), "\n")); !ok || lo >= hi {
		t.Error("the picked message is not highlighted")
	}

	read := captureClipboard(t)
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if got := read(cmd); got != "first **answer**" {
		t.Errorf("copied %q, want the picked message's source", got)
	}
	if next.(ChatModel).pick.open {
		t.Error("the picker stayed open after copying")
	}
}

// A key the picker does not own closes it and still does its usual job.
func TestTypingClosesThePicker(t *testing.T) {
	m := idleChat(t, 100, 30, Message{Role: RoleAssistant, Content: "a", Rendered: "x"})
	next, _ := m.submit("/copy")
	next, _ = next.(ChatModel).handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("h")})
	m = next.(ChatModel)
	if m.pick.open {
		t.Error("typing left the picker open")
	}
	if m.input.Value() != "h" {
		t.Errorf("the keystroke was swallowed: composer = %q", m.input.Value())
	}
}

// Copying is local, so it must not queue behind a running turn.
func TestCopyRunsMidTurn(t *testing.T) {
	m := idleChat(t, 100, 30, Message{Role: RoleAssistant, Content: "a", Rendered: "x"})
	m.streaming = true
	m.input.SetValue("/copy all")
	read := captureClipboard(t)
	next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if len(next.(ChatModel).queued) != 0 {
		t.Fatal("/copy all was queued behind the turn")
	}
	if got := read(cmd); !strings.Contains(got, "a") {
		t.Errorf("copied %q", got)
	}
}

// The highlight is drawn on exactly the selected cells.
func TestHighlightCoversTheSelectedColumns(t *testing.T) {
	m := idleChat(t, 80, 30, numberedStatus(5)...)
	y := rowOf(t, m, "line 01")
	m, _ = mouse(t, m, tea.MouseActionPress, 4, y)
	m, _ = mouse(t, m, tea.MouseActionMotion, 6, y+1)
	view := strings.Split(m.viewport.View(), "\n")
	row := y - m.contentHeaderRows() + m.viewport.YOffset
	if lo, hi, ok := m.selectedCols(row, view); !ok || lo != 4 || hi != len("  line 01") {
		t.Errorf("first row highlight = [%d,%d) ok=%v, want [4,%d)", lo, hi, ok, len("  line 01"))
	}
	if lo, hi, ok := m.selectedCols(row+1, view); !ok || lo != 0 || hi != 7 {
		t.Errorf("last row highlight = [%d,%d) ok=%v, want [0,7)", lo, hi, ok)
	}
	// The drawn row still reads the same once the styling is stripped.
	drawn := strings.Split(m.highlightSelection(m.viewport.View()), "\n")
	if ansi.Strip(drawn[y-m.contentHeaderRows()]) != ansi.Strip(view[y-m.contentHeaderRows()]) {
		t.Error("highlighting changed the row's text")
	}
}
