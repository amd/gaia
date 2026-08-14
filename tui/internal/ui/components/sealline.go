package components

import "strings"

// sealLineEnds closes any background colour still open at the end of a line's
// visible text, so the wrap padding after it is not painted.
//
// glamour wraps inside an inline code span without closing the span first: the
// line ends "…code text" with the background still set, and the spaces it pads
// the line out with inherit it. On screen that is a stray grey block hanging off
// the end of a sentence, one per wrapped span.
//
// Fenced blocks already emit a reset after every run, so they are untouched —
// their trailing padding is foreground-only and stays that way.
func sealLineEnds(rendered string) string {
	lines := strings.Split(rendered, "\n")
	for i, line := range lines {
		lines[i] = sealLine(line)
	}
	return strings.Join(lines, "\n")
}

func sealLine(line string) string {
	// Where the line's trailing run of spaces begins. Escapes are skipped, so a
	// reset sitting between the last word and the padding does not count as
	// content — it is exactly what makes the padding safe.
	tail := len(line)
	for i := 0; i < len(line); {
		if _, next, ok := sgrAt(line, i); ok {
			i = next
			continue
		}
		if line[i] != ' ' && line[i] != '\t' {
			// Multi-byte runes have no byte below 0x80, so stepping a byte at a
			// time can only land on further non-space bytes here.
			tail = i + 1
		}
		i++
	}
	if tail >= len(line) {
		return line // nothing trailing to protect
	}

	bg := false
	for i := 0; i < len(line); {
		if params, next, ok := sgrAt(line, i); ok {
			bg = applySGR(bg, params)
			i = next
			continue
		}
		if i >= tail && bg {
			return line[:i] + "\x1b[0m" + line[i:]
		}
		i++
	}
	return line
}

// sgrAt reports whether an SGR escape starts at i, returning its parameter text
// and the offset just past it.
func sgrAt(s string, i int) (string, int, bool) {
	if i+1 >= len(s) || s[i] != 0x1b || s[i+1] != '[' {
		return "", 0, false
	}
	for j := i + 2; j < len(s); j++ {
		c := s[j]
		if c == 'm' {
			return s[i+2 : j], j + 1, true
		}
		if c != ';' && (c < '0' || c > '9') {
			return "", 0, false
		}
	}
	return "", 0, false
}

// applySGR folds one escape's parameters into "is a background set". Only the
// background matters here; foreground and attributes are left to the terminal.
func applySGR(bg bool, params string) bool {
	if params == "" {
		return false // a bare ESC[m is ESC[0m
	}
	fields := strings.Split(params, ";")
	for i := 0; i < len(fields); i++ {
		switch fields[i] {
		case "0", "":
			bg = false
		case "49":
			bg = false
		case "48":
			bg = true
			// 48;5;N and 48;2;R;G;B — skip the colour operands so a component
			// that happens to read "0" or "49" is not mistaken for a reset.
			if i+1 < len(fields) {
				switch fields[i+1] {
				case "5":
					i += 2
				case "2":
					i += 4
				}
			}
		default:
			if isBackgroundCode(fields[i]) {
				bg = true
			}
		}
	}
	return bg
}

// isBackgroundCode reports whether a parameter is one of the direct background
// colours: 40-47 (standard) or 100-107 (bright).
func isBackgroundCode(field string) bool {
	if len(field) == 2 && field[0] == '4' && field[1] >= '0' && field[1] <= '7' {
		return true
	}
	return len(field) == 3 && field[0] == '1' && field[1] == '0' &&
		field[2] >= '0' && field[2] <= '7'
}
