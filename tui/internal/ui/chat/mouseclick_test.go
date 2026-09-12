// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

func TestURLAtFindsTheLinkUnderTheColumn(t *testing.T) {
	const line = "  [1]: #3697 https://github.com/amd/gaia/pull/3697"
	start := len("  [1]: #3697 ")

	for _, tc := range []struct {
		name string
		col  int
		want string
	}{
		{"on the scheme", start, "https://github.com/amd/gaia/pull/3697"},
		{"mid-URL", start + 20, "https://github.com/amd/gaia/pull/3697"},
		{"last character", len(line) - 1, "https://github.com/amd/gaia/pull/3697"},
		{"one past the end", len(line), ""},
		{"on the issue number", 8, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := urlAt(line, tc.col); got != tc.want {
				t.Errorf("urlAt(col %d) = %q, want %q", tc.col, got, tc.want)
			}
		})
	}
}

// The hit test is in DISPLAY columns, not bytes: a table rule or an emoji
// ahead of the link is one column and several bytes, and a byte index would
// put every link on the row several cells to the left of where it is drawn.
func TestURLAtCountsColumnsNotBytes(t *testing.T) {
	const line = "  │ ✅ see https://example.com/x"
	col := 0
	for _, r := range []rune(line) {
		if r == 'h' {
			break
		}
		col++
		if r == '✅' {
			col++ // double-width
		}
	}
	if got := urlAt(line, col); got != "https://example.com/x" {
		t.Errorf("urlAt(col %d) = %q, want the link", col, got)
	}
}

func TestURLAtTrimsSentencePunctuation(t *testing.T) {
	for _, tc := range []struct{ line, want string }{
		{"see https://example.com/x.", "https://example.com/x"},
		{"see (https://example.com/x)", "https://example.com/x"},
		{"see https://example.com/a_(b)", "https://example.com/a_(b)"},
		{"see https://example.com/x, then", "https://example.com/x"},
	} {
		if got := urlAt(tc.line, 5); got != tc.want {
			t.Errorf("urlAt(%q) = %q, want %q", tc.line, got, tc.want)
		}
	}
}

// A URL only a scheme away from an arbitrary handler must not be one: agent
// output is not a place to launch whatever the OS has registered.
func TestOnlyHTTPLinksAreClickable(t *testing.T) {
	for _, line := range []string{
		"open file:///etc/passwd",
		"run ssh://box/x",
		"see javascript:alert(1)",
	} {
		if got := urlAt(line, 6); got != "" {
			t.Errorf("urlAt(%q) = %q, want nothing clickable", line, got)
		}
	}
}

// clickAt drives a left press at (x, y) the way Bubble Tea delivers one.
func clickAt(t *testing.T, m ChatModel, x, y int) (ChatModel, tea.Cmd) {
	t.Helper()
	next, cmd := m.handleTranscriptMouse(tea.MouseMsg{
		X: x, Y: y, Action: tea.MouseActionPress, Button: tea.MouseButtonLeft,
	})
	return next.(ChatModel), cmd
}

func TestClickingALinkOpensIt(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m.messages = append(m.messages, Message{
		Role:    RoleAssistant,
		Content: "https://example.com/pull/1",
	})
	m.updateViewport()

	// The answer panel indents, so find the link's real row and column off
	// the rendered viewport rather than guessing at the layout.
	x, y, ok := findLink(m, "https://example.com/pull/1")
	if !ok {
		t.Fatal("test setup: the link never reached the rendered transcript")
	}

	_, cmd := clickAt(t, m, x, y)
	if cmd == nil {
		t.Fatal("clicking a link did nothing")
	}
}

func TestClickingPlainTextDoesNotOpenAnything(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m.messages = append(m.messages, Message{Role: RoleAssistant, Content: "no links here"})
	m.updateViewport()

	if _, cmd := clickAt(t, m, 4, m.contentHeaderRows()); cmd != nil {
		t.Error("a click on ordinary prose started something")
	}
}

// A click outside the transcript — the composer, the status bar — is not a
// transcript click, whatever the column arithmetic below it would say.
func TestClickingBelowTheTranscriptIsIgnored(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m.messages = append(m.messages, Message{
		Role:    RoleAssistant,
		Content: "https://example.com/x",
	})
	m.updateViewport()

	below := m.contentHeaderRows() + m.viewport.Height + 1
	if _, cmd := clickAt(t, m, 4, below); cmd != nil {
		t.Error("a click on the composer row was treated as a transcript click")
	}
}

func TestDoubleClickingAMessageCopiesIt(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m.messages = append(m.messages, Message{Role: RoleAssistant, Content: "the answer"})
	m.updateViewport()
	row := m.contentHeaderRows()

	m, cmd := clickAt(t, m, 4, row)
	if cmd != nil {
		t.Fatal("a single click on prose should do nothing on its own")
	}
	if _, cmd = clickAt(t, m, 4, row); cmd == nil {
		t.Fatal("a double click did not copy the message")
	}
}

func TestTwoSlowClicksAreNotADoubleClick(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m.messages = append(m.messages, Message{Role: RoleAssistant, Content: "the answer"})
	m.updateViewport()
	row := m.contentHeaderRows()

	m, _ = clickAt(t, m, 4, row)
	m.lastClickAt = time.Now().Add(-2 * doubleClickWindow)
	if _, cmd := clickAt(t, m, 4, row); cmd != nil {
		t.Error("two clicks a couple of seconds apart were treated as a double click")
	}
}

// The wheel still scrolls, and scrolling away still stops the view chasing
// the tail — the behaviour that made capturing the mouse worth it.
func TestTheWheelScrollsTheTranscript(t *testing.T) {
	m := sizedChat(t, 100, 12)
	for i := 0; i < 60; i++ {
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: "line"})
	}
	m.updateViewport()
	if !m.viewport.AtBottom() {
		t.Fatal("test setup: a fresh transcript should be pinned to the newest content")
	}

	next, _ := m.handleTranscriptMouse(tea.MouseMsg{
		Action: tea.MouseActionPress, Button: tea.MouseButtonWheelUp,
	})
	m = next.(ChatModel)
	if m.viewport.AtBottom() {
		t.Error("the wheel did not scroll the transcript")
	}
	if m.followTail {
		t.Error("scrolling up left the view chasing the tail")
	}
}

// findLink reports the screen position of the first rendered occurrence of u.
func findLink(m ChatModel, u string) (x, y int, ok bool) {
	top := m.contentHeaderRows()
	for i := 0; i < m.viewport.Height; i++ {
		line, inView := m.viewportLineAt(top + i)
		if !inView {
			break
		}
		for col := 0; col < len([]rune(line)); col++ {
			if urlAt(line, col) == u {
				return col, top + i, true
			}
		}
	}
	return 0, 0, false
}
