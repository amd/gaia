// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"time"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

// In-app text selection, for while the app holds the mouse (Ctrl+T).
//
// The terminal's own drag-select works on the cells it can see. The transcript
// is an app-drawn viewport on the alternate screen, so the terminal has nothing
// to scroll into and a selection can never reach past the window. Here the app
// tracks the drag itself, scrolls the viewport while the pointer sits past the
// top or bottom edge, and copies over OSC 52 on release.
//
// Positions are CONTENT rows (the transcript's own line numbers, independent of
// the scroll offset) and display columns, so a selection survives the viewport
// scrolling under it.

// selPos is one cell of the laid-out transcript.
type selPos struct {
	row int // content row, 0 = first line of the transcript
	col int // display column
}

func (p selPos) before(q selPos) bool {
	return p.row < q.row || (p.row == q.row && p.col < q.col)
}

// orderSel returns the two ends of a selection, earliest first.
func orderSel(a, b selPos) (selPos, selPos) {
	if b.before(a) {
		return b, a
	}
	return a, b
}

// textSelection is the drag in progress, or the range it left behind.
type textSelection struct {
	anchor, head selPos
	// active means a range exists: it is highlighted, and Enter re-copies it.
	active bool
	// dragging is true while the left button is down.
	dragging bool
	// moved is true once the drag left the cell it started on — the line
	// between a click (open a link, double-click to copy) and a selection.
	moved bool
	// pointerX/pointerY are the last screen cell the pointer reported, which
	// the auto-scroll tick re-reads: a pointer resting past the edge sends no
	// further events.
	pointerX, pointerY int
	// ticking is true while an auto-scroll tick is scheduled, so a burst of
	// motion events cannot stack several tickers.
	ticking bool
}

// selectScrollInterval is how often a drag past the edge scrolls. Fast enough
// to cover a long answer in a few seconds, slow enough to stop on the line
// you want.
const selectScrollInterval = 50 * time.Millisecond

// selectScrollMaxLines caps how many lines one tick scrolls — further past the
// edge scrolls faster, the way terminals and browsers do.
const selectScrollMaxLines = 5

type selectScrollTickMsg struct{}

func selectScrollTick() tea.Cmd {
	return tea.Tick(selectScrollInterval, func(time.Time) tea.Msg { return selectScrollTickMsg{} })
}

// contentPosAt maps screen cell (x, y) to a content position, clamped to the
// visible transcript. edge is -1 above it, +1 below it, 0 inside; dist is how
// many rows past that edge the pointer is.
func (m ChatModel) contentPosAt(x, y int) (pos selPos, edge, dist int) {
	top := m.contentHeaderRows()
	bottom := top + m.viewport.Height - 1
	lastRow := m.viewport.TotalLineCount() - 1
	if lastRow < 0 {
		lastRow = 0
	}
	if x < 0 {
		x = 0
	}
	switch {
	case y < top:
		return selPos{row: m.viewport.YOffset, col: 0}, -1, top - y
	case y > bottom:
		row := m.viewport.YOffset + m.viewport.Height - 1
		if row > lastRow {
			row = lastRow
		}
		return selPos{row: row, col: m.viewport.Width}, 1, y - bottom
	}
	row := m.viewport.YOffset + (y - top)
	if row > lastRow {
		return selPos{row: lastRow, col: m.viewport.Width}, 0, 0
	}
	return selPos{row: row, col: x}, 0, 0
}

// handleSelectionPress starts a selection, or extends the current one when
// Shift is held. A press outside the transcript is not ours.
func (m ChatModel) handleSelectionPress(msg tea.MouseMsg) ChatModel {
	pos, edge, _ := m.contentPosAt(msg.X, msg.Y)
	if edge != 0 {
		return m
	}
	if msg.Shift && m.sel.active {
		m.sel.head = pos
		m.sel.moved = true
	} else {
		m.sel = textSelection{anchor: pos, head: pos}
	}
	m.sel.dragging = true
	m.sel.pointerX, m.sel.pointerY = msg.X, msg.Y
	return m
}

// handleSelectionMotion extends a drag to the pointer, and starts the
// auto-scroll ticker when the pointer has left the transcript.
func (m ChatModel) handleSelectionMotion(msg tea.MouseMsg) (ChatModel, tea.Cmd) {
	if !m.sel.dragging {
		return m, nil
	}
	m.sel.pointerX, m.sel.pointerY = msg.X, msg.Y
	pos, edge, _ := m.contentPosAt(msg.X, msg.Y)
	m.sel.head = pos
	if pos != m.sel.anchor {
		m.sel.moved = true
		m.sel.active = true
	}
	if edge != 0 && !m.sel.ticking {
		m.sel.ticking = true
		return m, selectScrollTick()
	}
	return m, nil
}

// onSelectScrollTick scrolls one step toward the pointer while a drag rests
// past an edge, and carries the selection's head along with the view.
func (m ChatModel) onSelectScrollTick() (ChatModel, tea.Cmd) {
	if !m.sel.dragging {
		m.sel.ticking = false
		return m, nil
	}
	_, edge, dist := m.contentPosAt(m.sel.pointerX, m.sel.pointerY)
	if edge == 0 || (edge < 0 && m.viewport.AtTop()) || (edge > 0 && m.viewport.AtBottom()) {
		m.sel.ticking = false
		return m, nil
	}
	n := dist
	if n > selectScrollMaxLines {
		n = selectScrollMaxLines
	}
	if edge < 0 {
		m.viewport.LineUp(n)
	} else {
		m.viewport.LineDown(n)
	}
	m = m.afterScroll()
	m.sel.head, _, _ = m.contentPosAt(m.sel.pointerX, m.sel.pointerY)
	m.sel.moved = true
	m.sel.active = true
	return m, selectScrollTick()
}

// handleSelectionRelease ends a drag. It reports whether the gesture was a
// selection (and copies it); false means it was a plain click.
func (m ChatModel) handleSelectionRelease() (ChatModel, tea.Cmd, bool) {
	if !m.sel.dragging {
		return m, nil, false
	}
	m.sel.dragging = false
	if !m.sel.moved {
		m.sel = textSelection{}
		return m, nil, false
	}
	m.sel.active = true
	return m, m.copySelection(), true
}

// copySelection sends the selected text to the clipboard.
func (m ChatModel) copySelection() tea.Cmd {
	return copyToClipboard(m.selectionText(), "selection")
}

// selectionText is the selection as plain text — see extractSelection.
func (m ChatModel) selectionText() string {
	if !m.sel.active {
		return ""
	}
	return extractSelection(strings.Split(m.vpContent, "\n"), m.msgSpans, m.messages, m.sel.anchor, m.sel.head)
}

func (m *ChatModel) clearSelection() {
	m.sel = textSelection{}
	m.pick = messagePicker{}
}

// extractSelection turns a selected range of the rendered transcript into the
// text a person meant to copy.
//
// A message the range covers end to end is copied from its SOURCE — the
// markdown, exactly as the agent wrote it — the same rule as Ctrl+Y and a
// double-click. That is what keeps a code block exact: the rendered copy is
// reflowed to the pane and indented by the answer panel.
//
// A partly covered message is copied from the rendered rows, minus the chrome
// updateViewport recorded for it (the "▶ You:" label, the answer's left
// padding, an error's title band, card borders, work-log glyphs, the timing
// line under an answer). A full row of an answer that is a line of one of its
// code blocks is swapped for that source line, so indentation survives a
// partial copy too.
func extractSelection(lines []string, spans []msgSpan, msgs []Message, a, b selPos) string {
	if len(lines) == 0 {
		return ""
	}
	s, e := orderSel(a, b)
	if s.row < 0 {
		s = selPos{}
	}
	if e.row >= len(lines) {
		e = selPos{row: len(lines) - 1, col: 1 << 30}
	}
	if s.row > e.row {
		return ""
	}

	plain := func(row int) string { return ansi.Strip(lines[row]) }
	// lo/hi are the selected columns of row, as a half-open range.
	lo := func(row int) int {
		if row == s.row {
			return s.col
		}
		return 0
	}
	hi := func(row int) int {
		if row == e.row {
			return e.col + 1
		}
		return 1 << 30
	}

	// Each group remembers the rows it came from, so groups on adjacent rows
	// join with one newline and groups the transcript spaced apart keep their
	// blank line.
	type group struct {
		text        string
		first, last int
	}
	var groups []group
	add := func(text string, first, last int) {
		if strings.TrimSpace(text) != "" {
			groups = append(groups, group{text, first, last})
		}
	}
	var free []string
	freeStart := 0
	flushFree := func() {
		first, last := freeStart, freeStart+len(free)-1
		for len(free) > 0 && strings.TrimSpace(free[0]) == "" {
			free, first = free[1:], first+1
		}
		for len(free) > 0 && strings.TrimSpace(free[len(free)-1]) == "" {
			free, last = free[:len(free)-1], last-1
		}
		add(dedentBlock(free), first, last)
		free = nil
	}

	for row := s.row; row <= e.row; {
		span, ok := spanAt(spans, row)
		if !ok || span.index < 0 || span.index >= len(msgs) {
			if len(free) == 0 {
				freeStart = row
			}
			free = append(free, strings.TrimRight(cutCols(plain(row), lo(row), hi(row)), " "))
			row++
			continue
		}
		flushFree()
		msg := &msgs[span.index]
		last := span.end - 1
		if last > e.row {
			last = e.row
		}

		if span.bodyStart < span.bodyEnd {
			first, end := span.bodyStart, span.bodyEnd-1
			startCovered := s.row < first ||
				(s.row == first && s.col <= max(span.gutter, leadingCols(plain(first))))
			endCovered := e.row > end ||
				(e.row == end && e.col+1 >= trimmedCols(plain(end)))
			if startCovered && endCovered {
				if src, ok := messageCopyText(msg); ok {
					add(src, first, end)
					row = span.end
					continue
				}
			}
		}

		var out []string
		var code []string
		codeAt := 0
		if msg.Role == RoleAssistant {
			code = fencedCodeLines(msg.Content)
		}
		first, end := row, last
		for r := row; r <= last; r++ {
			if r < span.bodyStart || r >= span.bodyEnd {
				continue
			}
			text := plain(r)
			from := lo(r)
			if from < span.gutter {
				from = span.gutter
			}
			cut := strings.TrimRight(cutCols(text, from, hi(r)), " ")
			wholeRow := lo(r) <= span.gutter && hi(r) >= trimmedCols(text)
			switch msg.Role {
			case RoleAssistant:
				if wholeRow && strings.HasPrefix(cut, " ") {
					if j := matchCodeLine(code, codeAt, cut); j >= 0 {
						cut, codeAt = code[j], j+1
					}
				}
			case RoleWork:
				cut = stripLeadingGlyph(cut)
			case RoleCard, RoleError:
				var keep bool
				if cut, keep = stripBorders(cut); !keep {
					continue
				}
			}
			out = append(out, cut)
		}
		add(strings.Trim(strings.Join(out, "\n"), "\n"), first, end)
		row = span.end
	}
	flushFree()

	var sb strings.Builder
	for i, g := range groups {
		if i > 0 {
			sb.WriteString("\n")
			if g.first > groups[i-1].last+1 {
				sb.WriteString("\n")
			}
		}
		sb.WriteString(g.text)
	}
	return sb.String()
}

// spanAt returns the message span covering content row, if any.
func spanAt(spans []msgSpan, row int) (msgSpan, bool) {
	for _, sp := range spans {
		if row >= sp.start && row < sp.end {
			return sp, true
		}
	}
	return msgSpan{}, false
}

// cutCols is the part of a plain line between display columns [lo, hi).
func cutCols(s string, lo, hi int) string {
	w := ansi.StringWidth(s)
	if hi > w {
		hi = w
	}
	if lo < 0 {
		lo = 0
	}
	if lo >= hi {
		return ""
	}
	return ansi.Cut(s, lo, hi)
}

// leadingCols is the width of a plain line's leading spaces.
func leadingCols(s string) int {
	return ansi.StringWidth(s) - ansi.StringWidth(strings.TrimLeft(s, " "))
}

// trimmedCols is the width of a plain line without its trailing spaces.
func trimmedCols(s string) int {
	return ansi.StringWidth(strings.TrimRight(s, " "))
}

// dedentBlock joins rows that belong to no message (the live answer, the work
// log of a running turn) after removing the indent they share.
func dedentBlock(rows []string) string {
	if len(rows) == 0 {
		return ""
	}
	indent := -1
	for _, r := range rows {
		if strings.TrimSpace(r) == "" {
			continue
		}
		if n := len(r) - len(strings.TrimLeft(r, " ")); indent < 0 || n < indent {
			indent = n
		}
	}
	out := make([]string, len(rows))
	for i, r := range rows {
		if len(r) >= indent {
			out[i] = r[indent:]
		}
	}
	return strings.Join(out, "\n")
}

// fencedCodeLines is every line inside the ``` fences of a markdown document.
func fencedCodeLines(markdown string) []string {
	var out []string
	in := false
	for _, line := range strings.Split(markdown, "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "```") {
			in = !in
			continue
		}
		if in {
			out = append(out, strings.TrimRight(line, " \t\r"))
		}
	}
	return out
}

// matchCodeLine finds the source code line, at or after from, that a rendered
// row shows. Whitespace runs are compared collapsed: the renderer expands tabs
// and indents the block, neither of which changes the words.
func matchCodeLine(code []string, from int, rendered string) int {
	key := strings.Join(strings.Fields(rendered), " ")
	if key == "" {
		return -1
	}
	for j := from; j < len(code); j++ {
		if strings.Join(strings.Fields(code[j]), " ") == key {
			return j
		}
	}
	return -1
}

// stripLeadingGlyph drops a work-log row's marker (▪, ✻, └, a spinner frame)
// and the indent before it.
func stripLeadingGlyph(s string) string {
	t := strings.TrimLeft(s, " ")
	r := []rune(t)
	if len(r) >= 2 && r[1] == ' ' && !unicode.IsLetter(r[0]) && !unicode.IsDigit(r[0]) {
		return string(r[2:])
	}
	return t
}

// isBoxRune reports whether r is a box-drawing or block character — the
// borders of a card or panel.
func isBoxRune(r rune) bool {
	return (r >= 0x2500 && r <= 0x259F) || r == ' '
}

// stripBorders removes a card's frame from one row. keep is false for a row
// that is nothing but frame (a top or bottom edge).
func stripBorders(s string) (string, bool) {
	t := strings.TrimSpace(s)
	if t == "" {
		return "", true
	}
	if strings.IndexFunc(t, func(r rune) bool { return !isBoxRune(r) }) < 0 {
		return "", false
	}
	r := []rune(t)
	if isBoxRune(r[0]) {
		r = r[1:]
	}
	if len(r) > 0 && isBoxRune(r[len(r)-1]) {
		r = r[:len(r)-1]
	}
	return strings.TrimSpace(string(r)), true
}

// messageCopyText is what copying a whole message puts on the clipboard: the
// source text, not the rendered rows. ok is false when the message has no
// source to copy (a card is data drawn as a table).
func messageCopyText(msg *Message) (string, bool) {
	switch msg.Role {
	case RoleCard:
		return "", false
	case RoleWork:
		var rows []string
		for _, item := range msg.Work {
			if c := strings.TrimSpace(item.Content); c != "" {
				rows = append(rows, c)
			}
			if d := strings.TrimSpace(item.Detail); d != "" {
				rows = append(rows, "  "+d)
			}
		}
		if len(rows) == 0 {
			return "", false
		}
		return strings.Join(rows, "\n"), true
	}
	if strings.TrimSpace(msg.Content) == "" {
		return "", false
	}
	return msg.Content, true
}

// selectionStyle marks the selected cells, the way a terminal does.
var selectionStyle = lipgloss.NewStyle().Reverse(true)

// selectedCols is the highlighted column range of content row: [lo, hi).
func (m ChatModel) selectedCols(row int, lines []string) (int, int, bool) {
	if m.pick.open {
		if m.pick.index < 0 || m.pick.index >= len(m.messages) {
			return 0, 0, false
		}
		for _, sp := range m.msgSpans {
			if sp.index == m.pick.index && row >= sp.start && row < sp.end {
				return 0, m.viewport.Width, true
			}
		}
		return 0, 0, false
	}
	if !m.sel.active {
		return 0, 0, false
	}
	s, e := orderSel(m.sel.anchor, m.sel.head)
	if row < s.row || row > e.row {
		return 0, 0, false
	}
	lo, hi := 0, m.viewport.Width
	if row == s.row {
		lo = s.col
	}
	if row == e.row {
		hi = e.col + 1
	}
	if i := row - m.viewport.YOffset; i >= 0 && i < len(lines) {
		if end := trimmedCols(ansi.Strip(lines[i])); hi > end {
			hi = end
		}
	}
	return lo, hi, lo < hi
}

// highlightSelection draws the selection (or the message being picked) over
// the viewport's rendered rows.
func (m ChatModel) highlightSelection(view string) string {
	if !m.sel.active && !m.pick.open {
		return view
	}
	lines := strings.Split(view, "\n")
	for i, line := range lines {
		lo, hi, ok := m.selectedCols(m.viewport.YOffset+i, lines)
		if !ok {
			continue
		}
		w := ansi.StringWidth(line)
		if hi > w {
			hi = w
		}
		if lo >= hi {
			continue
		}
		lines[i] = ansi.Truncate(line, lo, "") + "\x1b[0m" +
			selectionStyle.Render(ansi.Strip(ansi.Cut(line, lo, hi))) +
			ansi.TruncateLeft(line, hi, "")
	}
	return strings.Join(lines, "\n")
}
