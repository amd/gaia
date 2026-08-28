package preflight

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
	"github.com/charmbracelet/x/ansi"
)

// Colour is ADDITIVE here. Every state is already carried by its text marker
// ([ok] / [!] / [?] / [ ] / [..]) and by the words in the line, so the screen
// reads identically on a monochrome terminal, over a pipe, or to a user who
// cannot distinguish red from green.
var (
	titleStyle   = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	summaryStyle = lipgloss.NewStyle().Foreground(theme.Dim)
	dividerStyle = lipgloss.NewStyle().Foreground(theme.Divider)
	labelStyle   = lipgloss.NewStyle().Foreground(theme.Text)
	dimStyle     = lipgloss.NewStyle().Foreground(theme.Dim)
	okStyle      = lipgloss.NewStyle().Foreground(theme.Success)
	failStyle    = lipgloss.NewStyle().Foreground(theme.Danger)
	unknownStyle = lipgloss.NewStyle().Foreground(theme.Warning)
	keyStyle     = lipgloss.NewStyle().Bold(true).Foreground(theme.Info)
	noteStyle    = lipgloss.NewStyle().Foreground(theme.Warning)
	cmdStyle     = lipgloss.NewStyle().Foreground(theme.Accent)
)

const (
	indentW = 2
	cursorW = 2
	markerW = 6
	minW    = 40
	minH    = 10
)

func stateStyle(s State) lipgloss.Style {
	switch s {
	case StateOK:
		return okStyle
	case StateFailed:
		return failStyle
	case StateUnknown:
		return unknownStyle
	default:
		return dimStyle
	}
}

func (m Model) dims() (w, h, labelW, lineW int) {
	w, h = m.width, m.height
	if w < minW {
		w = minW
	}
	if h < minH {
		h = minH
	}
	labelW = w - 60
	if labelW < 12 {
		labelW = 12
	}
	if labelW > 20 {
		labelW = 20
	}
	lineW = w - indentW - cursorW - markerW - labelW - 1
	if lineW < 12 {
		lineW = 12
	}
	return w, h, labelW, lineW
}

func (m Model) render() string {
	w, h, labelW, lineW := m.dims()

	head := m.header(w)
	div := strings.Repeat("─", w-2*indentW)
	foot := m.footer(w)

	// header + divider + divider + footer are fixed chrome; everything else has
	// to fit in what is left. A readiness screen that itself does not fit at
	// 80x24 is self-defeating, so the body is trimmed, never the footer.
	budget := h - 4
	if budget < 1 {
		budget = 1
	}

	var body []string
	if m.details {
		body = m.detailLines(w, budget)
	} else {
		body = m.rowLines(labelW, lineW, w, budget)
	}
	if len(body) > budget {
		body = body[:budget]
	}

	out := []string{head, pad(dividerStyle.Render(div))}
	out = append(out, body...)
	out = append(out, pad(dividerStyle.Render(div)), foot)
	return clipToTerminal(out, m.width, m.height)
}

// clipToTerminal is the last thing every frame passes through.
//
// dims() floors the layout at minW x minH so the column arithmetic cannot go
// negative, which means a terminal SMALLER than the floor gets a frame laid out
// for a screen it does not have. Emitting that verbatim is worse than an ugly
// screen: an over-wide line soft-wraps and an over-tall frame scrolls, and
// Bubble Tea's repaint is cursor-relative — so from then on every frame, this
// one and the chat that follows it, lands in the wrong place.
//
// A 30-column terminal is below anything this screen can lay out usefully. It
// still has to stay a screen rather than corrupt the session.
func clipToTerminal(lines []string, cols, rows int) string {
	if cols > 0 {
		for i, line := range lines {
			if ansi.StringWidth(line) > cols {
				lines[i] = ansi.Truncate(line, cols, "…")
			}
		}
	}
	// The last line is the footer and carries the way out, so height is trimmed
	// from the BODY: keep the head, keep the tail, drop the middle.
	if rows > 0 && len(lines) > rows {
		if rows < 3 {
			lines = lines[:rows]
		} else {
			lines = append(append([]string{}, lines[:rows-2]...), lines[len(lines)-2:]...)
		}
	}
	return strings.Join(lines, "\n")
}

func (m Model) header(w int) string {
	left := fmt.Sprintf("Getting %s ready", m.cfg.AgentName)
	right := m.rep.Summary()
	if m.phase == phaseChecking {
		right = "checking…"
	}
	gap := w - 2*indentW - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 1 {
		return pad(titleStyle.Render(truncate(left, w-2*indentW)))
	}
	return pad(titleStyle.Render(left) + strings.Repeat(" ", gap) + summaryStyle.Render(right))
}

func (m Model) footer(w int) string {
	keys := [][2]string{}
	if fix := m.focusedFix(); fix != FixNone && !m.Busy() {
		keys = append(keys, [2]string{"f", fix.Label()})
	}
	keys = append(keys, [2]string{"r", "re-check"})
	keys = append(keys, [2]string{"d", "details"})
	// `enter` is offered whenever pressing it would actually do something:
	// when a report is neither ready nor refusing, and when the screen is
	// holding after a fix so the user can read what it did.
	if m.HeldForReview() ||
		(!m.Busy() && !m.rep.Ready() &&
			(!m.rep.Blocked() || m.rep.OfferableDespiteFailure())) {
		keys = append(keys, [2]string{"enter", "continue"})
	}
	keys = append(keys, [2]string{"esc", "back"})

	// Fitted against the REAL terminal, not the floored layout width, and
	// narrowed by dropping whole hints from the FRONT.
	//
	// Truncating the joined string instead would cut from the right, and the
	// rightmost hint is `esc` — the way out. A screen that has run out of room
	// to advertise anything else still has to advertise that.
	avail := w
	if m.width > 0 && m.width < avail {
		avail = m.width
	}
	avail -= 2 * indentW

	for first := 0; first < len(keys); first++ {
		line := renderKeys(keys[first:])
		if first == len(keys)-1 || lipgloss.Width(line) <= avail {
			return pad(truncateStyled(line, avail))
		}
	}
	return pad("")
}

func renderKeys(keys [][2]string) string {
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, keyStyle.Render(k[0])+" "+dimStyle.Render(k[1]))
	}
	return strings.Join(parts, dimStyle.Render(" · "))
}

func (m Model) focusedFix() FixKind {
	if m.focus < 0 || m.focus >= len(m.rep.Rows) {
		return FixNone
	}
	return m.rep.Rows[m.focus].Fix
}

// rowLines renders the checklist plus the focused row's remedy block. Only the
// focused row expands: five expanded remedies would not fit 24 rows, and a wall
// of advice is how a user stops reading any of it.
//
// When the terminal is too short, the remedy block shrinks first and the
// checklist keeps every row — a gate that hides the precondition it is
// reporting on is worse than one that hides the advice about it.
func (m Model) rowLines(labelW, lineW, w, budget int) []string {
	checklist, remedy, focusAt := m.checklistLines(labelW, lineW, w)
	trailer := m.trailerLines(w)

	available := budget - len(checklist) - len(trailer)
	if available < 0 {
		// Not even the checklist fits: drop the trailer, then rows.
		trailer = nil
		if budget < len(checklist) {
			return checklist[:budget]
		}
		available = budget - len(checklist)
	}
	if len(remedy) > available {
		if available > 0 {
			remedy = append(remedy[:available-1:available-1],
				dimStyle.Render(strings.Repeat(" ", indentW+cursorW+markerW)+"…press d for the raw answer"))
		} else {
			remedy = nil
		}
	}

	out := make([]string, 0, len(checklist)+len(remedy)+len(trailer))
	out = append(out, checklist[:focusAt+1]...)
	out = append(out, remedy...)
	out = append(out, checklist[focusAt+1:]...)
	return append(out, trailer...)
}

// checklistLines returns one line per row, the focused row's remedy block, and
// the index the remedy block belongs after.
func (m Model) checklistLines(labelW, lineW, w int) (checklist, remedy []string, focusAt int) {
	remedyIndent := indentW + cursorW + markerW
	remedyW := w - remedyIndent - indentW
	focusAt = len(m.rep.Rows) - 1

	for i, row := range m.rep.Rows {
		focused := i == m.focus
		cursor := "  "
		if focused && !m.rep.Ready() {
			cursor = "> "
		}
		st := stateStyle(row.State)
		marker := st.Render(fmt.Sprintf("%-*s", markerW-1, row.State.Marker())) + " "
		label := labelStyle.Render(fmt.Sprintf("%-*s", labelW, truncate(row.Label, labelW)))

		line := row.Line
		if row.State == StateChecking {
			line = m.spin.View() + " " + line
		}
		if row.State == StatePending && row.Detail != "" {
			line = row.Line + "  " + row.Detail
		}
		body := st.Render(truncate(line, lineW))
		if row.State == StateOK || row.State == StatePending {
			body = dimStyle.Render(truncate(line, lineW))
		}
		checklist = append(checklist, pad(cursor+marker+label+body))

		if !focused || row.State == StateOK || row.State == StatePending {
			continue
		}
		focusAt = i
		remedy = indentAll(m.remedyLines(row, remedyW), remedyIndent)
	}
	return checklist, remedy, focusAt
}

// trailerLines is what sits under the checklist: the download's progress line,
// the outcome of the last fix, or the hand-off note on an all-ready screen.
func (m Model) trailerLines(w int) []string {
	switch {
	case m.phase == phaseProvisioning:
		return []string{
			"",
			pad(noteStyle.Render(truncate(m.spin.View()+" "+m.provisionLine, w-2*indentW))),
			pad(dimStyle.Render("Leaving this screen cancels the download.")),
		}
	case m.HeldForReview() && m.rep.Ready():
		// A fix just ran and everything passed. Say so, and say the screen is
		// waiting — a green checklist that sits there with no explanation
		// reads as frozen.
		return []string{"", pad(noteStyle.Render(
			"Setup finished and everything checks out. Press enter to start " +
				m.cfg.AgentName + "."))}
	case m.note != "":
		return append([]string{""}, indentAll(wrap(m.note, w-2*indentW), indentW)...)
	case m.phase == phaseDone:
		// Only when a hand-off is actually scheduled — under ManualProceed
		// nothing is starting, and saying so would be a lie.
		return []string{"", pad(dimStyle.Render("Starting " + m.cfg.AgentName + "…"))}
	}
	return nil
}

func (m Model) remedyLines(row Row, w int) []string {
	out := []string{}
	if row.Detail != "" {
		for _, l := range wrap(row.Detail, w) {
			out = append(out, dimStyle.Render(l))
		}
	}
	if row.Remedy.Action != "" {
		for _, l := range wrap(row.Remedy.Action, w) {
			out = append(out, labelStyle.Render(l))
		}
	}
	if row.Remedy.Command != "" {
		for i, l := range wrap(row.Remedy.Command, w-7) {
			prefix := dimStyle.Render("run:   ")
			if i > 0 {
				prefix = "       "
			}
			out = append(out, prefix+cmdStyle.Render(l))
		}
	}
	if row.Remedy.Where != "" {
		for i, l := range wrap(row.Remedy.Where, w-7) {
			prefix := dimStyle.Render("look:  ")
			if i > 0 {
				prefix = "       "
			}
			out = append(out, prefix+dimStyle.Render(l))
		}
	}
	if row.Fix != FixNone {
		out = append(out, keyStyle.Render("f")+" "+dimStyle.Render(row.Fix.Label()))
	}
	return out
}

// detailLines is `d`: the raw answer behind the focused row, so a user filing a
// bug can copy exactly what the probe saw.
func (m Model) detailLines(w, budget int) []string {
	if m.focus < 0 || m.focus >= len(m.rep.Rows) {
		return []string{pad(dimStyle.Render("no row selected"))}
	}
	row := m.rep.Rows[m.focus]
	out := []string{
		pad(labelStyle.Render(fmt.Sprintf("%s — %s", row.Label, row.State.Word()))),
		"",
	}
	raw := row.Raw
	if strings.TrimSpace(raw) == "" {
		raw = "(this check recorded no raw answer)"
	}
	for _, l := range wrap(raw, w-2*indentW) {
		out = append(out, pad(dimStyle.Render(l)))
		if len(out) >= budget {
			break
		}
	}
	return out
}

// --- text helpers ----------------------------------------------------------

func pad(s string) string { return strings.Repeat(" ", indentW) + s }

func indentAll(lines []string, n int) []string {
	prefix := strings.Repeat(" ", n)
	out := make([]string, 0, len(lines))
	for _, l := range lines {
		out = append(out, prefix+l)
	}
	return out
}

// truncate cuts a plain (ANSI-free) string to w display columns.
func truncate(s string, w int) string {
	if w <= 0 {
		return ""
	}
	if lipgloss.Width(s) <= w {
		return s
	}
	runes := []rune(s)
	for len(runes) > 0 && lipgloss.Width(string(runes))+1 > w {
		runes = runes[:len(runes)-1]
	}
	return string(runes) + "…"
}

// truncateStyled cuts a string that may already carry ANSI styling. It only
// drops whole segments, so it never leaves a half-written escape sequence.
func truncateStyled(s string, w int) string {
	if lipgloss.Width(s) <= w {
		return s
	}
	parts := strings.Split(s, " ")
	out := ""
	for _, p := range parts {
		candidate := out
		if candidate != "" {
			candidate += " "
		}
		candidate += p
		if lipgloss.Width(candidate) > w {
			break
		}
		out = candidate
	}
	return out
}

// wrap breaks text into lines of at most w columns, on word boundaries where it
// can and mid-token when a single token (a URL, a long command) is longer.
func wrap(s string, w int) []string {
	if w < 8 {
		w = 8
	}
	var out []string
	for _, paragraph := range strings.Split(s, "\n") {
		line := ""
		for _, word := range strings.Fields(paragraph) {
			for lipgloss.Width(word) > w {
				if line != "" {
					out = append(out, line)
					line = ""
				}
				runes := []rune(word)
				cut := len(runes)
				for cut > 0 && lipgloss.Width(string(runes[:cut])) > w {
					cut--
				}
				out = append(out, string(runes[:cut]))
				word = string(runes[cut:])
			}
			switch {
			case line == "":
				line = word
			case lipgloss.Width(line)+1+lipgloss.Width(word) <= w:
				line += " " + word
			default:
				out = append(out, line)
				line = word
			}
		}
		if line != "" {
			out = append(out, line)
		}
	}
	if len(out) == 0 {
		return []string{""}
	}
	return out
}
