// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"encoding/json"
	"strconv"
	"strings"
)

// The work log answers two different questions depending on who is reading it.
//
// A user asks "what is the agent doing right now", and the narrated phrase plus
// its one-line outcome answers that completely — anything more is noise that
// buries it. Someone IMPROVING the agent asks a different question: what did
// the model actually pass to that tool, and what came back. That is the pair
// this file renders, and it is shown only under --dev.
//
// Both are one line each and truncated hard. A developer log that reflows the
// whole payload into the live region destroys the frame it is supposed to
// explain; the full text belongs in ~/.gaia/logs/gaia-agent.log, which --dev
// also switches to DEBUG. These lines are the pointer, not the record.

// scrubDisplayControls drops the control characters that survive clean().
//
// clean() strips ESC-introduced ANSI and the C0/DEL range, which is the right
// scrub for the narration prose it was written for. This path is wider: it
// renders RAW tool output, so whatever a tool returns — including whatever a
// web page, a file, or an email put in front of that tool — reaches the frame.
// Two classes matter and neither is C0:
//
//   - C1 (U+0080–U+009F). U+009B is CSI. A terminal honouring 8-bit controls
//     reads it as the start of a real escape sequence.
//   - Bidi overrides (U+202A–U+202E, U+2066–U+2069). Zero width, and they
//     reorder everything after them — so a payload can display as text it does
//     not contain, which is a lie told in the one view meant for verifying what
//     actually happened.
//
// Dropped rather than escaped: this line is a truncated pointer to the full
// record in the log file, so losing an exotic character costs nothing, while
// widening the line to escape it would push out the payload.
func scrubDisplayControls(s string) string {
	return strings.Map(func(r rune) rune {
		switch {
		case r >= 0x80 && r <= 0x9f:
			return -1
		case r >= 0x202a && r <= 0x202e:
			return -1
		case r >= 0x2066 && r <= 0x2069:
			return -1
		}
		return r
	}, s)
}

// stepNumberOf reads the current step out of a harness status line
// ("Step 3/50" -> 3). The second return distinguishes "not a step line" from a
// step line that genuinely said zero.
//
// A string parse because that is the only form the number arrives in: the
// canonical transport has no step event, and its `final` usage block reports
// the total only once the turn is over.
func stepNumberOf(status string) (int, bool) {
	rest, ok := strings.CutPrefix(strings.TrimSpace(status), "Step ")
	if !ok {
		return 0, false
	}
	// "3/50" -> "3"; a bare "Step 3" is accepted too.
	if slash := strings.IndexByte(rest, '/'); slash >= 0 {
		rest = rest[:slash]
	}
	n, err := strconv.Atoi(strings.TrimSpace(rest))
	if err != nil || n < 0 {
		return 0, false
	}
	return n, true
}

// devPayloadWidth caps an args/output line at capture time.
//
// Wider than detailWidth (66) because a payload is data rather than prose and
// the interesting part is often a path or an id near the end, but still short
// of the narrowest terminal worth supporting so the line cannot wrap and push
// the live region around.
const devPayloadWidth = 100

// devPayload renders a tool's arguments or its result for the developer log:
// one line, no newlines, no control bytes, truncated to width.
//
// Non-JSON is shown as-is rather than dropped. This is a raw-payload view, so
// bytes that do not parse are exactly what the reader needs to see — swallowing
// them would hide the malformed-output bug they are looking for.
func devPayload(raw json.RawMessage, width int) string {
	if len(bytes.TrimSpace(raw)) == 0 {
		return ""
	}

	// Compact first: a pretty-printed payload is mostly indentation, and
	// indentation is the least informative thing that could occupy the width.
	var buf bytes.Buffer
	text := string(raw)
	if err := json.Compact(&buf, raw); err == nil {
		text = buf.String()
	}

	if text = clean(scrubDisplayControls(text)); text == "" {
		return ""
	}
	// An absent payload arrives as any of these depending on the tool. None of
	// them says anything a blank line would not, and `out {}` on every result
	// is the kind of noise that makes a developer stop reading the log.
	switch strings.TrimSpace(text) {
	case "null", "{}", "[]", `""`:
		return ""
	}
	if width < 8 {
		return ""
	}
	return truncateRunes(text, width)
}
