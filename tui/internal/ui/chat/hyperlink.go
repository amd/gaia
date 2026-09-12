// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	"github.com/charmbracelet/x/ansi"
)

// Marking printed URLs as real terminal hyperlinks (OSC 8).
//
// Clicking is already handled on our side — handleTranscriptMouse hit-tests the
// click and shells out to the platform opener — and that works in every
// terminal. This adds what only the TERMINAL can give: the underline on hover,
// its own right-click "copy link address", and a link that still works while
// SELECT MODE has the mouse, when no click reaches us at all.
//
// The two are deliberately both on. OSC 8 is unevenly supported (iTerm2,
// WezTerm, kitty, Windows Terminal, GNOME Terminal, foot and recent Alacritty
// have it; plenty of others do not), and a terminal that ignores the sequence
// prints nothing extra — it just falls back to the click path.
const (
	osc8Prefix = "\x1b]8;;"
	osc8Sep    = "\x1b\\"
	osc8Close  = "\x1b]8;;\x1b\\"
)

// linkify marks every printed http(s) URL in already-rendered content as a
// terminal hyperlink. Width is the measure the content will be displayed at.
//
// The sequences are zero-width — ansi.StringWidth ignores them and ansi.Strip
// removes them — so the transcript's own column arithmetic, the click
// hit-testing (urlAt) and the row spans are all untouched.
//
// Known edge, shared with the click path and older than this: prose wraps
// before it gets here, and a single URL longer than the measure has to break
// across two lines. Each half is then its own match, so the first is marked up
// as a link to a truncated URI. Marking only what is on the line is the honest
// reading — the alternative is guessing that two lines are one link — and a
// URL that long is already unreadable on the row it is printed on.
func linkify(content string, width int) string {
	if width <= 0 || !strings.Contains(content, "http") {
		return content
	}
	lines := strings.Split(content, "\n")
	for i, line := range lines {
		// A line wider than the pane is truncated by the viewport (it renders
		// with MaxWidth), and a cut between a link's opening sequence and its
		// close would leave the link open — in some terminals that makes the
		// whole rest of the screen clickable. Such a line keeps the click path
		// only, which needs no sequence on the wire.
		if ansi.StringWidth(line) > width {
			continue
		}
		// Already marked up by whatever produced it; marking it again would
		// nest one link inside another.
		if strings.Contains(line, osc8Prefix) {
			continue
		}
		lines[i] = linkifyLine(line)
	}
	return strings.Join(lines, "\n")
}

// linkifyLine wraps each URL on one rendered line. Only the URL itself is
// wrapped — never the sentence punctuation that follows it, which is why the
// span comes from trimURLTail rather than the raw match.
func linkifyLine(line string) string {
	locs := urlPattern.FindAllStringIndex(line, -1)
	if len(locs) == 0 {
		return line
	}
	var b strings.Builder
	last := 0
	for _, loc := range locs {
		u := trimURLTail(line[loc[0]:loc[1]])
		if u == "" {
			continue
		}
		b.WriteString(line[last:loc[0]])
		b.WriteString(osc8Prefix + u + osc8Sep + u + osc8Close)
		last = loc[0] + len(u)
	}
	b.WriteString(line[last:])
	return b.String()
}
