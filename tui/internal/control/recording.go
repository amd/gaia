// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package control

import (
	"fmt"
	"strings"
)

// Replaying a session as a moving picture.
//
// The state already keeps every rendered frame with the millisecond it was
// drawn (see recordFrame) — a recording in all but name, with no capture step
// to start and nothing to remember to stop. This renders that history as one
// self-contained animated SVG: the frames play back at the speed they actually
// happened, so a scroll that jumped, a flicker between two layouts, or a
// spinner that stopped moving is visible as motion rather than inferred from
// two stills.
//
// The same reasoning as ScreenSVG applies to the format: no font to ship, no
// encoder to depend on, every glyph the terminal can draw survives, and the
// result is text.

// recordingTailMS is how long the last frame is held before the loop restarts.
// Without it the final state — usually the thing being demonstrated — flashes
// past in whatever handful of milliseconds separated it from its predecessor.
const recordingTailMS = 1500

// RecordingSVG renders a sequence of captured frames as one looping animation.
// Frames are expected in the order they were drawn.
func RecordingSVG(frames []Frame, cols, rows int) string {
	if len(frames) == 0 {
		return ScreenSVG("", cols, rows)
	}
	if len(frames) == 1 {
		return ScreenSVG(frames[0].Screen, cols, rows)
	}

	// Geometry comes from the widest and tallest frame, so a mid-recording
	// resize does not clip the frames on either side of it.
	maxRows, maxCols := rows, cols
	for _, f := range frames {
		lines := strings.Split(strings.TrimRight(f.Screen, "\n"), "\n")
		if len(lines) > maxRows {
			maxRows = len(lines)
		}
		for _, line := range lines {
			if w := svgLineWidth(line); w > maxCols {
				maxCols = w
			}
		}
	}
	if maxCols <= 0 || maxRows <= 0 {
		maxCols, maxRows = 1, 1
	}

	// Each frame runs until the next one was drawn; the last one is held.
	starts := make([]int64, len(frames))
	base := frames[0].AtMS
	for i, f := range frames {
		starts[i] = f.AtMS - base
		if starts[i] < 0 {
			starts[i] = 0
		}
	}
	total := starts[len(starts)-1] + recordingTailMS

	w := float64(maxCols)*svgCellW + 2*svgPad
	h := float64(maxRows)*svgCellH + 2*svgPad

	var b strings.Builder
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" `+
		`viewBox="0 0 %.0f %.0f" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" `+
		`font-size="%.1f">`, w, h, w, h, svgFontSize)

	b.WriteString("<style>")
	fmt.Fprintf(&b, `.f{visibility:hidden;animation-duration:%.3fs;animation-iteration-count:infinite;animation-timing-function:step-end}`,
		float64(total)/1000)
	for i := range frames {
		start := pct(starts[i], total)
		end := 100.0
		if i+1 < len(frames) {
			end = pct(starts[i+1], total)
		}
		// step-end plus an explicit hidden bookend: a frame is on for exactly
		// its own slice of the timeline and off for the rest of the loop.
		fmt.Fprintf(&b, `@keyframes f%d{0%%{visibility:hidden}%.4f%%{visibility:visible}%.4f%%{visibility:hidden}100%%{visibility:hidden}}`,
			i, start, end)
		fmt.Fprintf(&b, `.f%d{animation-name:f%d}`, i, i)
	}
	b.WriteString("</style>")

	fmt.Fprintf(&b, `<rect width="%.0f" height="%.0f" fill="%s"/>`, w, h, svgDefaultBG)
	for i, f := range frames {
		fmt.Fprintf(&b, `<g class="f f%d">`, i)
		writeFrameBody(&b, f.Screen)
		b.WriteString("</g>")
	}
	b.WriteString("</svg>")
	return b.String()
}

// pct is one instant's position on the timeline, as a percentage.
func pct(at, total int64) float64 {
	if total <= 0 {
		return 0
	}
	p := float64(at) / float64(total) * 100
	if p < 0 {
		return 0
	}
	if p > 100 {
		return 100
	}
	return p
}
