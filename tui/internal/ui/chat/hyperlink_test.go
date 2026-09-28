// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

func TestLinkifyMarksAURLAsATerminalHyperlink(t *testing.T) {
	const line = "  see https://example.com/pull/1 for details"
	got := linkify(line, 80)

	want := osc8Prefix + "https://example.com/pull/1" + osc8Sep +
		"https://example.com/pull/1" + osc8Close
	if !strings.Contains(got, want) {
		t.Errorf("no hyperlink sequence around the URL:\n%q", got)
	}
}

// The sequences must cost no columns and leave no visible text: the whole
// transcript's layout, the row spans and the click hit-testing are all
// measured off this content.
func TestLinkifyChangesNeitherTheTextNorItsWidth(t *testing.T) {
	const line = "  see https://example.com/pull/1 for details"
	got := linkify(line, 80)

	if stripped := ansi.Strip(got); stripped != line {
		t.Errorf("visible text changed:\n got %q\nwant %q", stripped, line)
	}
	if w := ansi.StringWidth(got); w != ansi.StringWidth(line) {
		t.Errorf("width changed: %d, want %d", w, ansi.StringWidth(line))
	}
}

// The link stops where the URL stops. A trailing period belongs to the
// sentence, and a terminal handed it would open a 404.
func TestLinkifyLeavesSentencePunctuationOutOfTheURI(t *testing.T) {
	got := linkify("see https://example.com/x.", 80)
	if !strings.Contains(got, osc8Prefix+"https://example.com/x"+osc8Sep) {
		t.Errorf("the URI is not the bare URL:\n%q", got)
	}
	if strings.Contains(got, osc8Prefix+"https://example.com/x."+osc8Sep) {
		t.Errorf("the trailing period was swallowed into the URI:\n%q", got)
	}
}

// A line the viewport will truncate must not be marked up: a cut between the
// opening sequence and its close leaves the link open, and some terminals then
// treat the rest of the screen as part of it.
func TestLinkifySkipsALineTheViewportWillTruncate(t *testing.T) {
	line := strings.Repeat("x", 40) + " https://example.com/pull/1"
	if got := linkify(line, 20); got != line {
		t.Errorf("an over-wide line was marked up anyway:\n%q", got)
	}
}

func TestLinkifyLeavesContentThatIsAlreadyMarkedUp(t *testing.T) {
	line := osc8Prefix + "https://example.com/x" + osc8Sep + "label" + osc8Close
	if got := linkify(line, 80); got != line {
		t.Errorf("nested one hyperlink inside another:\n%q", got)
	}
}

func TestLinkifyLeavesProseAlone(t *testing.T) {
	const line = "no links in this line at all"
	if got := linkify(line, 80); got != line {
		t.Errorf("rewrote a line with no URL: %q", got)
	}
}

// Styled output closes a link's colour immediately after the last URL
// character. The URI must be the URL, not the URL plus the SGR codes that
// happen to follow it.
func TestLinkifyDoesNotSwallowTrailingStyleCodes(t *testing.T) {
	line := "see \x1b[38;5;33mhttps://example.com/x\x1b[0m done"
	got := linkify(line, 80)

	if !strings.Contains(got, osc8Prefix+"https://example.com/x"+osc8Sep) {
		t.Errorf("the URI is not the bare URL:\n%q", got)
	}
	if strings.Contains(got, "\x1b[0m"+osc8Sep) {
		t.Errorf("style codes were swallowed into the URI:\n%q", got)
	}
}

// The two paths have to coexist: marking the transcript up must not move the
// link out from under the click hit-testing, which reads the same rows.
func TestAMarkedUpLinkIsStillClickable(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m.messages = append(m.messages, Message{
		Role:    RoleAssistant,
		Content: "https://example.com/pull/7",
	})
	m.updateViewport()

	if !strings.Contains(m.viewport.View(), osc8Prefix) {
		t.Fatal("the rendered transcript carries no hyperlink sequence")
	}

	x, y, ok := findLink(m, "https://example.com/pull/7")
	if !ok {
		t.Fatal("the link is no longer findable on a rendered row")
	}
	if _, cmd := clickAt(t, m, x, y); cmd == nil {
		t.Error("marking the link up broke clicking it")
	}
}

// And the sequences must not shift what a click lands on: a row's spans are
// counted off the same content.
func TestMarkupDoesNotShiftTheRowAClickLandsOn(t *testing.T) {
	m := sizedChat(t, 100, 30)
	m.messages = append(m.messages,
		Message{Role: RoleAssistant, Content: "first https://example.com/a"},
		Message{Role: RoleAssistant, Content: "second"},
	)
	m.updateViewport()

	x, y, ok := findLink(m, "https://example.com/a")
	if !ok {
		t.Fatal("test setup: the link never reached the rendered transcript")
	}
	if got := m.messageAt(y); got != 0 {
		t.Errorf("row %d maps to message %d, want the first message", y, got)
	}
	// One column before the link is prose, so it must not open anything.
	if _, cmd := clickAt(t, m, x-1, y); cmd != nil {
		t.Error("a click just before the link opened it")
	}
}
