// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"math"
	"strings"
)

// eighths are the partial cells of a bar, so it grows by 1/8 of a cell and a
// slow download still visibly moves.
var eighths = []string{"", "▏", "▎", "▍", "▌", "▋", "▊", "▉"}

// bar draws a progress bar of width cells for a fraction in [0, 1]. It returns
// the filled part and the empty track separately so the caller can colour
// each; together they are always exactly width cells wide.
func bar(frac float64, width int, ascii bool) (filled, track string) {
	if width < 1 {
		return "", ""
	}
	if math.IsNaN(frac) || frac < 0 {
		frac = 0
	}
	if frac > 1 {
		frac = 1
	}
	if ascii {
		n := int(math.Floor(frac * float64(width)))
		return strings.Repeat("#", n), strings.Repeat("-", width-n)
	}
	total := int(math.Floor(frac * float64(width) * 8))
	full, part := total/8, total%8
	filled = strings.Repeat("█", full) + eighths[part]
	used := full
	if part > 0 {
		used++
	}
	return filled, strings.Repeat("░", width-used)
}

// indeterminate is the verifying bar: a block that slides along the track.
// frame advances it; under reduced motion it is a static striped bar.
func indeterminate(frame, width int, ascii, reduced bool) string {
	if width < 1 {
		return ""
	}
	stripe, dot := "▚", "░"
	if ascii {
		stripe, dot = "/", "-"
	}
	if reduced {
		return strings.Repeat(stripe, width)
	}
	block := width / 4
	if block < 1 {
		block = 1
	}
	span := width + block
	at := frame%span - block
	var b strings.Builder
	for i := 0; i < width; i++ {
		if i >= at && i < at+block {
			if ascii {
				b.WriteString("#")
			} else {
				b.WriteString("█")
			}
		} else {
			b.WriteString(dot)
		}
	}
	return b.String()
}
