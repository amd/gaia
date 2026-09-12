// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package control

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/charmbracelet/x/ansi"
)

// Turning a captured frame into a picture.
//
// `format=plain` answers "what does it say"; this answers "what does it LOOK
// like" — colour, alignment, which cell a link sits in — which is the half of
// a TUI bug report that text cannot carry. It is the closest thing to a
// screenshot an agent driving this API can produce, and unlike a screen
// grab it needs no display, no window, and no permission to record one.
//
// SVG rather than a raster format on purpose: no font file to ship, no
// rasteriser to depend on, every box-drawing glyph and emoji the terminal can
// show renders because the viewer draws it with its own monospace font, and
// the output is text, so it diffs.

// Cell geometry. The advance width is the one number that has to be right: get
// it wrong and every box-drawing rule shears. Each run is drawn with an
// explicit textLength, so the glyphs are stretched to the grid rather than
// trusted to land on it.
//
// The document also pins preserveAspectRatio. Without it a renderer asked for a
// square thumbnail is free to SLICE the frame to fill — macOS qlmanage does
// exactly that — and the result looks like a TUI that cannot draw its own
// header and status bar, rather than a converter that cropped them off.
const (
	svgCellW    = 8.4
	svgCellH    = 17.0
	svgFontSize = 14.0
	svgPad      = 10.0
)

// The 16 ANSI colours, in the palette most terminals ship.
var svgBasePalette = [16]string{
	"#000000", "#cd3131", "#0dbc79", "#e5e510",
	"#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
	"#666666", "#f14c4c", "#23d18b", "#f5f543",
	"#3b8eea", "#d670d6", "#29b8db", "#e5e5e5",
}

const (
	svgDefaultFG = "#e5e5e5"
	svgDefaultBG = "#1e1e1e"
)

// sgrState is the drawing state a run of cells inherits.
type sgrState struct {
	fg, bg  string
	bold    bool
	faint   bool
	italic  bool
	under   bool
	reverse bool
}

func (s sgrState) colors() (fg, bg string) {
	fg, bg = s.fg, s.bg
	if fg == "" {
		fg = svgDefaultFG
	}
	if bg == "" {
		bg = svgDefaultBG
	}
	if s.reverse {
		fg, bg = bg, fg
	}
	return fg, bg
}

// run is a stretch of cells sharing one drawing state.
type run struct {
	col   int
	width int
	text  string
	state sgrState
}

// ScreenSVG renders one captured ANSI frame as a standalone SVG document.
func ScreenSVG(frame string, cols, rows int) string {
	lines := strings.Split(strings.TrimRight(frame, "\n"), "\n")
	if rows <= 0 || rows < len(lines) {
		rows = len(lines)
	}
	if cols <= 0 {
		for _, line := range lines {
			if w := ansi.StringWidth(line); w > cols {
				cols = w
			}
		}
	}
	if cols <= 0 || rows <= 0 {
		cols, rows = 1, 1
	}

	w := float64(cols)*svgCellW + 2*svgPad
	h := float64(rows)*svgCellH + 2*svgPad

	var b strings.Builder
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" `+
		`viewBox="0 0 %.0f %.0f" preserveAspectRatio="xMidYMid meet" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" `+
		`font-size="%.1f">`, w, h, w, h, svgFontSize)
	fmt.Fprintf(&b, `<rect width="%.0f" height="%.0f" fill="%s"/>`, w, h, svgDefaultBG)
	writeFrameBody(&b, frame)
	b.WriteString("</svg>")
	return b.String()
}

// splitRuns walks one line, tracking SGR state, and returns the runs to draw.
// OSC sequences (hyperlinks among them) carry no cells and are skipped whole —
// a link's URI must never end up drawn as text.
func splitRuns(line string) []run {
	var runs []run
	var cur sgrState
	var text strings.Builder
	col, start := 0, 0

	flush := func() {
		if text.Len() == 0 {
			return
		}
		s := text.String()
		runs = append(runs, run{col: start, width: ansi.StringWidth(s), text: s, state: cur})
		text.Reset()
	}

	for i := 0; i < len(line); {
		if line[i] != 0x1b {
			r, size := decodeRune(line[i:])
			if text.Len() == 0 {
				start = col
			}
			text.WriteString(r)
			col += ansi.StringWidth(r)
			i += size
			continue
		}
		seq, kind, size := scanEscape(line[i:])
		i += size
		switch kind {
		case escSGR:
			flush()
			cur = applySGR(cur, seq)
		default:
			// OSC and everything else: zero cells, no drawing state.
		}
	}
	flush()
	return runs
}

const (
	escSGR = iota
	escOther
)

// scanEscape consumes one escape sequence and says what it was.
func scanEscape(s string) (body string, kind, size int) {
	if len(s) < 2 {
		return "", escOther, len(s)
	}
	switch s[1] {
	case '[': // CSI
		i := 2
		for i < len(s) && (s[i] >= 0x30 && s[i] <= 0x3f) {
			i++
		}
		for i < len(s) && (s[i] >= 0x20 && s[i] <= 0x2f) {
			i++
		}
		if i < len(s) {
			final := s[i]
			body = s[2:i]
			i++
			if final == 'm' {
				return body, escSGR, i
			}
			return body, escOther, i
		}
		return "", escOther, len(s)
	case ']': // OSC, terminated by BEL or ST
		i := 2
		for i < len(s) {
			if s[i] == 0x07 {
				return "", escOther, i + 1
			}
			if s[i] == 0x1b && i+1 < len(s) && s[i+1] == '\\' {
				return "", escOther, i + 2
			}
			i++
		}
		return "", escOther, len(s)
	default:
		return "", escOther, 2
	}
}

// decodeRune returns the next UTF-8 rune as a string plus its byte length.
func decodeRune(s string) (string, int) {
	for i := 1; i <= len(s); i++ {
		if i == len(s) || (s[i]&0xc0) != 0x80 {
			return s[:i], i
		}
	}
	return s[:1], 1
}

// applySGR folds one SGR parameter list into the drawing state.
func applySGR(st sgrState, body string) sgrState {
	if body == "" {
		return sgrState{}
	}
	parts := strings.Split(body, ";")
	for i := 0; i < len(parts); i++ {
		n, err := strconv.Atoi(parts[i])
		if err != nil {
			continue
		}
		switch {
		case n == 0:
			st = sgrState{}
		case n == 1:
			st.bold = true
		case n == 2:
			st.faint = true
		case n == 3:
			st.italic = true
		case n == 4:
			st.under = true
		case n == 7:
			st.reverse = true
		case n == 22:
			st.bold, st.faint = false, false
		case n == 23:
			st.italic = false
		case n == 24:
			st.under = false
		case n == 27:
			st.reverse = false
		case n == 39:
			st.fg = ""
		case n == 49:
			st.bg = ""
		case n >= 30 && n <= 37:
			st.fg = svgBasePalette[n-30]
		case n >= 90 && n <= 97:
			st.fg = svgBasePalette[n-90+8]
		case n >= 40 && n <= 47:
			st.bg = svgBasePalette[n-40]
		case n >= 100 && n <= 107:
			st.bg = svgBasePalette[n-100+8]
		case n == 38 || n == 48:
			color, consumed := readExtendedColor(parts[i+1:])
			i += consumed
			if color != "" {
				if n == 38 {
					st.fg = color
				} else {
					st.bg = color
				}
			}
		}
	}
	return st
}

// readExtendedColor handles 5;N (256-colour) and 2;R;G;B (truecolor), and
// reports how many parameters it consumed beyond the 38/48 itself.
func readExtendedColor(rest []string) (string, int) {
	if len(rest) == 0 {
		return "", 0
	}
	switch rest[0] {
	case "5":
		if len(rest) < 2 {
			return "", len(rest)
		}
		n, err := strconv.Atoi(rest[1])
		if err != nil {
			return "", 2
		}
		return xterm256(n), 2
	case "2":
		if len(rest) < 4 {
			return "", len(rest)
		}
		r, _ := strconv.Atoi(rest[1])
		g, _ := strconv.Atoi(rest[2])
		bl, _ := strconv.Atoi(rest[3])
		return fmt.Sprintf("#%02x%02x%02x", clamp8(r), clamp8(g), clamp8(bl)), 4
	}
	return "", 1
}

func clamp8(v int) int {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return v
}

// xterm256 resolves one 256-palette index to a hex colour.
func xterm256(n int) string {
	switch {
	case n < 0 || n > 255:
		return ""
	case n < 16:
		return svgBasePalette[n]
	case n < 232:
		n -= 16
		steps := []int{0, 95, 135, 175, 215, 255}
		return fmt.Sprintf("#%02x%02x%02x", steps[n/36], steps[(n/6)%6], steps[n%6])
	default:
		v := 8 + (n-232)*10
		return fmt.Sprintf("#%02x%02x%02x", v, v, v)
	}
}

// escapeXML makes one run's text safe to drop into the document.
func escapeXML(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")
	return r.Replace(s)
}

// svgLineWidth is one rendered line's width in cells.
func svgLineWidth(line string) int { return ansi.StringWidth(line) }

// writeFrameBody draws one frame's cells into an open SVG document. Split out
// so a still (ScreenSVG) and one frame of a replay (RecordingSVG) can never
// drift in how they render the same bytes.
func writeFrameBody(b *strings.Builder, frame string) {
	for row, line := range strings.Split(strings.TrimRight(frame, "\n"), "\n") {
		y := svgPad + float64(row)*svgCellH
		runs := splitRuns(line)
		// Backgrounds in their own pass, so a run never paints over the
		// glyphs of the one before it.
		for _, r := range runs {
			_, bg := r.state.colors()
			if bg == svgDefaultBG {
				continue
			}
			fmt.Fprintf(b, `<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s"/>`,
				svgPad+float64(r.col)*svgCellW, y, float64(r.width)*svgCellW, svgCellH, bg)
		}
		for _, r := range runs {
			if strings.TrimSpace(r.text) == "" {
				continue
			}
			fg, _ := r.state.colors()
			fmt.Fprintf(b, `<text x="%.2f" y="%.2f" textLength="%.2f" lengthAdjust="spacingAndGlyphs" fill="%s"`,
				svgPad+float64(r.col)*svgCellW, y+svgFontSize*0.8, float64(r.width)*svgCellW, fg)
			if r.state.bold {
				b.WriteString(` font-weight="bold"`)
			}
			if r.state.italic {
				b.WriteString(` font-style="italic"`)
			}
			if r.state.under {
				b.WriteString(` text-decoration="underline"`)
			}
			if r.state.faint {
				b.WriteString(` opacity="0.65"`)
			}
			b.WriteString(` xml:space="preserve">`)
			b.WriteString(escapeXML(r.text))
			b.WriteString("</text>")
		}
	}
}
