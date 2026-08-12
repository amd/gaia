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

	if text = clean(text); text == "" {
		return ""
	}
	// "null" is what an absent payload marshals to; it says nothing a blank
	// line would not.
	if strings.TrimSpace(text) == "null" {
		return ""
	}
	if width < 8 {
		return ""
	}
	return truncateRunes(text, width)
}
