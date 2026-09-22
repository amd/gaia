// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"os/exec"
	"regexp"
	"runtime"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// What a click in the transcript does.
//
// An agent that answers with a list of pull requests has printed twenty links
// the reader now has to retype. One click opens the one under the pointer, and
// a double click copies the whole message it is part of — the two things a
// reader wants from a transcript they cannot select with the mouse while the
// app is tracking it (Ctrl+T, selectmode.go, hands that back).

// doubleClickWindow is how long after a click a second one on the same row
// still counts as a double click. Long enough not to demand a fast hand,
// short enough that two deliberate clicks on two different links never merge
// — and they cannot anyway, since the row has to match.
const doubleClickWindow = 500 * time.Millisecond

// urlPattern matches a bare http(s) URL as it appears in rendered output.
// Deliberately not a full URI grammar: everything here was printed for a human
// to read, so a run of non-space characters after the scheme is the URL, and
// trimURLTail deals with the sentence punctuation that run swallows.
//
// ESC is excluded because this also runs over STYLED text (see hyperlink.go).
// Glamour closes a link's colour immediately after the last URL character, so
// without this the match swallows the trailing SGR codes into the URI.
var urlPattern = regexp.MustCompile("https?://[^\\s<>\"'`\x1b]+")

// trimURLTail drops trailing characters that belong to the sentence rather
// than the link — the period ending "see https://example.com/x." and the
// closing bracket of "[1]: (https://example.com/x)". A closing paren is kept
// when the URL opened one itself, which is what Wikipedia-style links need.
func trimURLTail(u string) string {
	for len(u) > 0 {
		last := u[len(u)-1]
		switch last {
		case '.', ',', ';', ':', '!', '?', '"', '\'':
		case ')':
			// Balanced: the URL opened this paren itself, the way a Wikipedia
			// article title does. Unbalanced means the sentence opened it.
			if strings.Count(u, "(") >= strings.Count(u, ")") {
				return u
			}
		case ']', '}', '>':
		default:
			return u
		}
		u = u[:len(u)-1]
	}
	return u
}

// urlAt returns the URL printed under display column col of a plain (already
// ANSI-stripped) line, or "" when the click landed on ordinary text.
//
// Columns, not byte offsets: a line can carry box-drawing characters and
// emoji ahead of the link, and a byte index would put the hit test several
// cells off.
func urlAt(plain string, col int) string {
	for _, loc := range urlPattern.FindAllStringIndex(plain, -1) {
		raw := trimURLTail(plain[loc[0]:loc[1]])
		if raw == "" {
			continue
		}
		start := ansi.StringWidth(plain[:loc[0]])
		if col >= start && col < start+ansi.StringWidth(raw) {
			return raw
		}
	}
	return ""
}

// viewportLineAt returns the plain text of the transcript row at absolute
// screen row y, and whether y is inside the transcript at all.
//
// Read off the viewport's own rendered View rather than the content buffer, so
// the scroll offset, the height and any clipping the viewport applies are the
// viewport's business and cannot drift out of sync here.
func (m ChatModel) viewportLineAt(y int) (string, bool) {
	top := m.contentHeaderRows()
	if y < top || y >= top+m.viewport.Height {
		return "", false
	}
	lines := strings.Split(m.viewport.View(), "\n")
	i := y - top
	if i < 0 || i >= len(lines) {
		return "", false
	}
	return ansi.Strip(lines[i]), true
}

// messageAt returns the index into m.messages of the message drawn at absolute
// screen row y, or -1. Spans are recorded by updateViewport as it lays the
// transcript out (see msgSpan), for the same reason questionViewLine is:
// recomputing the layout independently here would drift the first time a
// message above changed height.
func (m ChatModel) messageAt(y int) int {
	top := m.contentHeaderRows()
	if y < top || y >= top+m.viewport.Height {
		return -1
	}
	row := m.viewport.YOffset + (y - top)
	for _, span := range m.msgSpans {
		if row >= span.start && row < span.end {
			return span.index
		}
	}
	return -1
}

// handleTranscriptMouse routes a mouse event that no overlay claimed.
//
// The wheel scrolls. A left click opens the link under it. A second left click
// on the same row within doubleClickWindow copies that message — the source
// markdown, not the rendered ANSI, same rule as Ctrl+Y.
func (m ChatModel) handleTranscriptMouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
	if isWheelEvent(msg) {
		var cmd tea.Cmd
		m.viewport, cmd = m.viewport.Update(msg)
		return m.afterScroll(), cmd
	}
	if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft {
		return m, nil
	}

	if line, ok := m.viewportLineAt(msg.Y); ok {
		if u := urlAt(line, msg.X); u != "" {
			m.lastClickRow, m.lastClickAt = -1, time.Time{}
			return m, openURL(u)
		}
	}

	double := msg.Y == m.lastClickRow && time.Since(m.lastClickAt) <= doubleClickWindow
	m.lastClickRow, m.lastClickAt = msg.Y, time.Now()
	if !double {
		return m, nil
	}
	// A double click that landed on a link never reaches here — the link won
	// above — so this is the "copy what I am pointing at" case.
	m.lastClickRow, m.lastClickAt = -1, time.Time{}
	idx := m.messageAt(msg.Y)
	if idx < 0 || idx >= len(m.messages) {
		return m, nil
	}
	return m, copyToClipboard(m.messages[idx].Content, "message")
}

// urlOpenResultMsg reports what happened to a clicked link. Only a FAILURE is
// ever shown: a browser that came up says so by coming up, and a status line
// under every click would bury the transcript the user clicked in.
type urlOpenResultMsg struct {
	url string
	err error
}

// openURL hands a clicked link to the platform's own opener.
//
// Only http(s) ever reaches here (urlPattern), which is what keeps this from
// being a way for agent output to launch an arbitrary handler — a printed
// `file://` or a custom scheme is text, not a link.
func openURL(u string) tea.Cmd {
	return func() tea.Msg {
		var cmd *exec.Cmd
		switch runtime.GOOS {
		case "darwin":
			cmd = exec.Command("open", u)
		case "windows":
			// Not `cmd /c start`, which treats the URL's & as a command
			// separator and needs quoting rules that differ by shell.
			cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", u)
		default:
			cmd = exec.Command("xdg-open", u)
		}
		if err := cmd.Start(); err != nil {
			return urlOpenResultMsg{url: u, err: err}
		}
		// Reaped in the background: xdg-open can outlive the click by as long
		// as the browser takes to start, and an unwaited child is a zombie for
		// the rest of the session.
		go func() { _ = cmd.Wait() }()
		return urlOpenResultMsg{url: u}
	}
}

// openURLHint is the status line for a link that would not open — it names the
// URL so the reader can still get at it.
func openURLHint(u string, err error) string {
	return fmt.Sprintf("could not open %s: %v", u, err)
}
