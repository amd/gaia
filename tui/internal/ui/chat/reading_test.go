// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// "GAIA │ GAIA" over "Welcome to GAIA / Connected to: GAIA" is the product name
// four times before the user has typed anything.
func TestTheProductNameIsNotRepeatedBackAtTheUser(t *testing.T) {
	m := NewChatModel(&nullClient{}, "GAIA", "", false)
	m.width, m.height = 100, 30
	m.resize()

	header := ansi.Strip(m.renderHeader())
	if strings.Count(header, "GAIA") != 1 {
		t.Errorf("header names the product more than once: %q", header)
	}
	welcome := ansi.Strip(m.renderWelcome())
	if strings.Contains(welcome, "Connected to: GAIA") {
		t.Errorf("welcome repeats the product name as an agent name:\n%s", welcome)
	}

	// A DIFFERENT agent still gets named — that line carries information.
	other := NewChatModel(&nullClient{}, "Email", "", false)
	other.width, other.height = 100, 30
	other.resize()
	if h := ansi.Strip(other.renderHeader()); !strings.Contains(h, "Email") {
		t.Errorf("a non-flagship agent lost its name from the header: %q", h)
	}
	if w := ansi.Strip(other.renderWelcome()); !strings.Contains(w, "Email") {
		t.Errorf("a non-flagship agent lost its name from the welcome:\n%s", w)
	}
}

// The alt screen has no terminal scrollback of its own, so if the TUI does not
// scroll, every earlier turn is simply gone.
func TestTranscriptScrollsBackThroughHistory(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	for i := 0; i < 40; i++ {
		m.messages = append(m.messages, Message{Role: RoleUser, Content: "question " + strings.Repeat("x", i%7)})
	}
	m.updateViewport()

	if !m.viewport.AtBottom() {
		t.Fatal("a fresh transcript should start pinned to the newest content")
	}

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyUp})
	m = updated.(ChatModel)
	if m.viewport.AtBottom() {
		t.Error("↑ did not scroll the transcript")
	}
	if m.followTail {
		t.Error("scrolling away must stop the view being dragged back to the bottom")
	}

	// New content arriving while the reader is up here must not yank them down.
	m.messages = append(m.messages, Message{Role: RoleAssistant, Content: "a new answer"})
	m.updateViewport()
	if m.viewport.AtBottom() {
		t.Error("new content stole the reader's scroll position")
	}

	updated, _ = m.handleKey(tea.KeyMsg{Type: tea.KeyEnd})
	m = updated.(ChatModel)
	if !m.viewport.AtBottom() || !m.followTail {
		t.Error("End must return to the newest content and re-arm following")
	}
}

// The wheel is the first thing a person reaches for; in an alt-screen app it
// only works if the program asked the terminal for mouse events AND forwards
// them to the transcript.
func TestMouseWheelScrollsTheTranscript(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	for i := 0; i < 40; i++ {
		m.messages = append(m.messages, Message{Role: RoleUser, Content: "question"})
	}
	m.updateViewport()

	updated, _ := m.Update(tea.MouseMsg{
		Button: tea.MouseButtonWheelUp,
		Action: tea.MouseActionPress,
	})
	after := updated.(ChatModel)
	if after.viewport.AtBottom() {
		t.Error("the wheel did not scroll the transcript")
	}
	if after.followTail {
		t.Error("wheeling up must stop the view following new content")
	}
}

// Asking something new means you want to see the answer, wherever you had
// scrolled to.
func TestSendingAQuestionReturnsToTheNewestContent(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m.followTail = false

	updated, _ := m.sendQuery("what changed?")
	if !updated.(ChatModel).followTail {
		t.Error("a new question must re-arm following the newest content")
	}
}

// Tokens land in the transcript as they arrive — a 90s turn that shows nothing
// until the final event is indistinguishable from a hang.
func TestStreamedTokensAppearBeforeTheFinalEvent(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalTokenEvent{Type: "token", Delta: "The github-triage skill "},
		event.CanonicalTokenEvent{Type: "token", Delta: "clusters open issues."},
	)

	if out := ansi.Strip(m.viewport.View()); !strings.Contains(out, "clusters open issues.") {
		t.Errorf("streamed tokens are not on screen before `final`:\n%s", out)
	}
}

// Scrolling is only useful if the user knows it exists.
func TestTheStatusBarAdvertisesScrolling(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	if out := ansi.Strip(m.View()); !strings.Contains(out, "scroll") {
		t.Errorf("nothing on screen tells the user they can scroll:\n%s", out)
	}
}

// The user's own question must not be the brightest thing on screen — styling
// only the "You:" label left the question itself at the terminal's default
// foreground, louder than the answer it was asking for.
func TestTheUsersQuestionIsDimmedInFull(t *testing.T) {
	m := newTestChat(t)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "what changed in the release?"})

	raw := m.renderMessage(&m.messages[len(m.messages)-1], nil)
	// The styled run has to cover the text, not just the prefix: no unstyled
	// tail after the last reset.
	if i := strings.LastIndex(raw, "\x1b[0m"); i >= 0 {
		if tail := strings.TrimSpace(raw[i+len("\x1b[0m"):]); tail != "" {
			t.Errorf("the question has unstyled text after the last reset: %q", tail)
		}
	}
	if !strings.Contains(ansi.Strip(raw), "what changed in the release?") {
		t.Errorf("the question text was lost: %q", ansi.Strip(raw))
	}
}

// Turns run together into one block are hard to scan; each gets air around it.
func TestTurnsAreSeparatedByBlankLines(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m.messages = []Message{
		{Role: RoleUser, Content: "first"},
		{Role: RoleAssistant, Content: "answer one"},
		{Role: RoleUser, Content: "second"},
		{Role: RoleAssistant, Content: "answer two"},
	}
	m.updateViewport()

	// The viewport pads every row to the full width, so a "blank" line is a
	// whitespace-only one — not an empty string.
	var blanks int
	lines := strings.Split(ansi.Strip(m.viewport.View()), "\n")
	for i, line := range lines {
		if strings.TrimSpace(line) != "" {
			continue
		}
		// Only count gaps BETWEEN content, not the empty tail of the pane.
		for _, later := range lines[i+1:] {
			if strings.TrimSpace(later) != "" {
				blanks++
				break
			}
		}
	}
	if blanks < 3 {
		t.Errorf("only %d blank lines separate 4 messages; the transcript is one block:\n%s",
			blanks, ansi.Strip(m.viewport.View()))
	}
}

// A 200-column terminal laying prose out as 200-character lines loses the eye on
// the carriage return. Cards and tables are not prose and are not capped.
func TestProseIsCappedToAReadableMeasure(t *testing.T) {
	m := NewChatModel(&nullClient{}, "GAIA", "", false)
	m.width, m.height = 220, 50
	m.resize()

	if w := m.answerWidth(); w > answerMeasure {
		t.Errorf("answers lay out at %d columns; capped measure is %d", w, answerMeasure)
	}
	// A narrow terminal still uses what it has rather than padding to the cap.
	m.width = 60
	m.resize()
	if w := m.answerWidth(); w > 60 {
		t.Errorf("answer width %d exceeds the terminal's own %d columns", w, 60)
	}
}

// An action can occupy two rows once its outcome lands, so capping ACTIONS
// alone let the live region grow to 13 rows and shove the answer being read off
// the top of the pane.
func TestTheLiveRegionHasABoundedHeight(t *testing.T) {
	m := newTestChat(t)
	for _, tool := range []string{
		"list_inbox", "get_message", "classify", "apply_prefs",
		"pre_scan_inbox", "index_document", "read_file", "summarize",
	} {
		m = feed(t, m,
			event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool},
			event.CanonicalToolResultEvent{Type: "tool_result", Tool: tool, Preview: "done in 12ms"},
		)
	}

	out := ansi.Strip(m.renderLiveRegion())
	t.Logf("\n%s", out)
	if n := len(strings.Split(out, "\n")); n > workLogMaxRows {
		t.Errorf("live region is %d rows; capped at %d", n, workLogMaxRows)
	}
	// Trimming drops whole actions, so no outcome line is left hanging under
	// nothing at the top.
	if first := strings.TrimSpace(strings.Split(out, "\n")[0]); strings.HasPrefix(first, glyphDetail) {
		t.Errorf("an outcome line was orphaned at the top: %q", first)
	}
}

// Home/End are cursor keys while there is something to move the cursor in.
func TestHomeAndEndYieldToTheComposer(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	for i := 0; i < 40; i++ {
		m.messages = append(m.messages, Message{Role: RoleUser, Content: "question"})
	}
	m.updateViewport()

	m.input.SetValue("half a sentence")
	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyHome})
	if !updated.(ChatModel).viewport.AtBottom() {
		t.Error("Home scrolled the transcript while the user was mid-sentence")
	}

	m.input.SetValue("")
	updated, _ = m.handleKey(tea.KeyMsg{Type: tea.KeyHome})
	if updated.(ChatModel).viewport.AtBottom() {
		t.Error("Home did not jump the transcript with an empty composer")
	}
}
