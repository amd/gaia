package cards

import (
	"strings"

	"github.com/charmbracelet/x/ansi"
)

// Minimum outer width a card can be drawn at. Below this the borders eat the
// content, so callers get an unboxed plain-text rendering instead.
const minCardWidth = 24

// box accumulates interior lines and draws a rounded-free ASCII-safe frame with
// the title embedded in the top border:
//
//	┌─ Inbox · 25 scanned ──────────┐
//	│  URGENT                  2    │
//	└───────────────────────────────┘
//
// Interior lines are stored already styled; every width calculation goes
// through ansi.StringWidth so escape sequences never inflate the padding.
type box struct {
	title string
	width int // total outer width, borders included
	lines []string
}

func newBox(title string, width int) *box {
	if width < minCardWidth {
		width = minCardWidth
	}
	return &box{title: title, width: width}
}

// inner is the usable content width between "│ " and " │".
func (b *box) inner() int {
	w := b.width - 4
	if w < 1 {
		w = 1
	}
	return w
}

func (b *box) add(line string) { b.lines = append(b.lines, line) }

func (b *box) blank() { b.lines = append(b.lines, "") }

// addWrapped appends s wrapped to the interior width, each continuation line
// carrying the same indent as the first.
func (b *box) addWrapped(indent, s string) {
	for _, line := range wrap(s, b.inner()-visualLen(indent)) {
		b.add(indent + line)
	}
}

func (b *box) render() string {
	var sb strings.Builder
	sb.WriteString(b.top())
	for _, line := range b.lines {
		sb.WriteString("\n│ ")
		sb.WriteString(padTo(truncTo(line, b.inner()), b.inner()))
		sb.WriteString(" │")
	}
	sb.WriteString("\n└")
	sb.WriteString(strings.Repeat("─", b.width-2))
	sb.WriteString("┘")
	return sb.String()
}

func (b *box) top() string {
	title := strings.TrimSpace(b.title)
	// "┌─ " + title + " " + fill + "┐"
	fill := b.width - 5 - visualLen(title)
	if title == "" || fill < 1 {
		if title != "" && b.width-6 > 0 {
			title = truncTo(title, b.width-6)
			fill = b.width - 5 - visualLen(title)
		}
		if title == "" || fill < 1 {
			return "┌" + strings.Repeat("─", b.width-2) + "┐"
		}
	}
	return "┌─ " + title + " " + strings.Repeat("─", fill) + "┐"
}

// row renders a numbered card row: index, sender column, subject column.
// The sender column is sized off the available width so an 80-column terminal
// still shows a usable amount of both.
func (b *box) row(index int, sender, subject string) {
	avail := b.inner() - 5 // " NN  "
	if avail < 4 {
		b.addWrapped("  ", sender+" — "+subject)
		return
	}
	senderW := avail * 2 / 5
	if senderW > 24 {
		senderW = 24
	}
	if senderW < 8 {
		senderW = 8
	}
	if senderW > avail-4 {
		senderW = avail - 4
	}
	subjectW := avail - senderW - 1
	num := padLeft(itoa(index), 2)
	b.add(" " + num + "  " +
		padTo(truncTo(sender, senderW), senderW) + " " +
		truncTo(subject, subjectW))
}

// sectionHeader draws "  URGENT" on the left with a count flush right. The
// label is a word, never a colour — R2 (no colour-only signals).
func (b *box) sectionHeader(label, count string) {
	left := "  " + label
	gap := b.inner() - visualLen(left) - visualLen(count) - 2
	if gap < 1 {
		b.add(truncTo(left+" "+count, b.inner()))
		return
	}
	b.add(left + strings.Repeat(" ", gap) + count + "  ")
}

// ---------------------------------------------------------------------------
// width-safe text helpers
// ---------------------------------------------------------------------------

func visualLen(s string) int { return ansi.StringWidth(s) }

func truncTo(s string, w int) string {
	if w <= 0 {
		return ""
	}
	if visualLen(s) <= w {
		return s
	}
	if w == 1 {
		return "…"
	}
	return ansi.Truncate(s, w, "…")
}

func padTo(s string, w int) string {
	n := w - visualLen(s)
	if n <= 0 {
		return s
	}
	return s + strings.Repeat(" ", n)
}

func padLeft(s string, w int) string {
	n := w - visualLen(s)
	if n <= 0 {
		return s
	}
	return strings.Repeat(" ", n) + s
}

// wrap breaks s on spaces to fit w columns, hard-splitting any single word that
// is wider than the line. Always returns at least one (possibly empty) line.
func wrap(s string, w int) []string {
	if w < 1 {
		w = 1
	}
	s = strings.ReplaceAll(s, "\t", "    ")
	fields := strings.Fields(s)
	if len(fields) == 0 {
		return []string{""}
	}
	var out []string
	cur := ""
	for _, f := range fields {
		for visualLen(f) > w {
			head := ansi.Truncate(f, w, "")
			if head == "" {
				break
			}
			if cur != "" {
				out = append(out, cur)
				cur = ""
			}
			out = append(out, head)
			f = strings.TrimPrefix(f, head)
		}
		if f == "" {
			continue
		}
		switch {
		case cur == "":
			cur = f
		case visualLen(cur)+1+visualLen(f) <= w:
			cur += " " + f
		default:
			out = append(out, cur)
			cur = f
		}
	}
	if cur != "" {
		out = append(out, cur)
	}
	if len(out) == 0 {
		return []string{""}
	}
	return out
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
