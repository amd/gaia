// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "strings"

// scrubDisplayControls drops the control characters that survive clean().
//
// clean() strips ESC-introduced ANSI and the C0/DEL range, which is the right
// scrub for the narration prose it was written for. Commands, code and model
// descriptions are wider: whatever a web page, a file or an email put in front
// of the model can come back in them, and they reach the frame.
// Two classes matter and neither is C0:
//
//   - C1 (U+0080–U+009F). U+009B is CSI. A terminal honouring 8-bit controls
//     reads it as the start of a real escape sequence.
//   - Bidi overrides (U+202A–U+202E, U+2066–U+2069). Zero width, and they
//     reorder everything after them — so a payload can display as text it does
//     not contain, which is a lie told in the one view meant for verifying what
//     actually happened.
//
// Dropped rather than escaped: these lines are a pointer to the full record in
// the trace, so losing an exotic character costs nothing.
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
