// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package textutil

import (
	"strings"

	"github.com/charmbracelet/x/ansi"
)

// ellipsis ends a string Truncate had to cut.
const ellipsis = "…"

// Truncate cuts s to w display columns, ending in an ellipsis when it cuts.
// Width is the widest line, as lipgloss.Width measures it: the first line that
// overflows is cut and the lines after it are dropped. Wide runes count two
// columns, and ANSI styling is never split.
func Truncate(s string, w int) string {
	if w <= 0 {
		return ""
	}
	lines := strings.Split(s, "\n")
	widths := make([]int, len(lines))
	fits := true
	for i, l := range lines {
		widths[i] = ansi.StringWidth(l)
		fits = fits && widths[i] <= w
	}
	if fits {
		return s
	}
	for i, l := range lines {
		if widths[i] < w {
			continue
		}
		kept := ""
		if i > 0 {
			kept = strings.Join(lines[:i], "\n") + "\n"
		}
		if widths[i] > w {
			return kept + ansi.Truncate(l, w, ellipsis)
		}
		// A line exactly w wide still gives up a column to the ellipsis.
		return kept + ansi.Truncate(l, w-1, "") + ellipsis
	}
	return s
}
