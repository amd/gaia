package cards

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// maxDiffCardRows bounds how many diff content lines (hunk headers plus
// +/-/context lines, header lines excluded) the card draws before folding
// the rest into a "+N more lines (truncated)" footer.
//
// Deliberately larger than maxCardRows (22): a reviewer skimming a code
// change wants more context than an inbox summary needs. Still bounded --
// an unbounded diff card is exactly the "reflow the whole payload" failure
// devlog.go's live-region comment warns against, just relocated from the
// status line to the permanent transcript. A producer-side cap exists too
// (DIFF_MAX_LINES in gaia.agents.tools.diff_utils, protecting the transport
// itself); this is the independent DISPLAY cap on top of it.
const maxDiffCardRows = 40

// diffPayload is contract §4.3's generic `diff` primitive:
// `{ title?: string, unified: string }`. Unified is a pointer so an ABSENT
// key (a schema failure -- only title is marked optional) is distinguishable
// from an empty-but-present one, which is the honest "no changes" diff and
// renders as such rather than as an error.
type diffPayload struct {
	Title   string  `json:"title"`
	Unified *string `json:"unified"`
}

// hunkHeaderPattern matches a unified-diff hunk header, e.g.
// "@@ -12,7 +12,9 @@ def handler():" -- the trailing function-context text
// difflib sometimes appends is optional and captured but not required.
var hunkHeaderPattern = regexp.MustCompile(`^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@.*$`)

// diffLine is one rendered row: a hunk header ('@'), an addition ('+'), a
// deletion ('-'), unchanged context (' '), or anything else the parser does
// not recognize (0 -- e.g. difflib's "\ No newline at end of file" marker,
// or a producer's own truncation note). oldNum/newNum are 0 when not
// applicable to that line's kind.
type diffLine struct {
	kind   byte
	text   string
	oldNum int
	newNum int
}

// renderDiff draws the `diff` card: a unified-diff text with `+`/`-` lines
// colored green/red and a per-line number gutter, Claude-Code style. It is
// the producer for EVERY text-file edit the GAIA agent performs -- the
// tool_result payload comes from gaia.agents.tools.diff_utils, shared by
// every write/edit tool in file_io_tools.py regardless of file type.
func renderDiff(data json.RawMessage, width int) string {
	var p diffPayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("diff", err.Error(), data, width)
	}
	if p.Unified == nil {
		return renderInvalid("diff", "unified is required", data, width)
	}

	title := strings.TrimSpace(p.Title)
	if title == "" {
		title = "Diff"
	}
	b := newBox(title, width)

	lines := diffContentLines(*p.Unified)
	if len(lines) == 0 {
		b.add("  (no changes)")
		return b.render()
	}

	gutter := gutterWidth(lines)
	shown := lines
	if len(shown) > maxDiffCardRows {
		shown = shown[:maxDiffCardRows]
	}
	for _, l := range shown {
		b.addStyled("  " + renderDiffLine(l, gutter))
	}
	if hidden := len(lines) - len(shown); hidden > 0 {
		b.add("  +" + itoa(hidden) + " more line" + plural(hidden) + " (truncated)")
	}
	return b.render()
}

// diffContentLines parses a unified-diff text into rows, dropping the
// "--- a/f" / "+++ b/f" file-header lines -- the card's own title already
// names the file, so repeating it inside the body is noise.
func diffContentLines(unified string) []diffLine {
	rawLines := strings.Split(unified, "\n")
	// Every line difflib emits (headers included) already carries its own
	// trailing "\n", so splitting on "\n" leaves one bare "" at the end --
	// drop only that, never an interior blank: a genuinely blank SOURCE line
	// arrives as " " (bare context marker), "+", or "-", never as "".
	if n := len(rawLines); n > 0 && rawLines[n-1] == "" {
		rawLines = rawLines[:n-1]
	}

	var out []diffLine
	oldNum, newNum := 0, 0
	seenHunk := false
	for _, raw := range rawLines {
		// File headers appear only BEFORE the first hunk. Matching the
		// prefix on every line ate real content — a deleted SQL comment
		// ("-- x") arrives as "--- x", an added "++i;" as "+++i;" — and
		// desynced the gutter for the rest of the hunk.
		if !seenHunk && (strings.HasPrefix(raw, "--- ") || strings.HasPrefix(raw, "+++ ")) {
			continue
		}
		if m := hunkHeaderPattern.FindStringSubmatch(raw); m != nil {
			seenHunk = true
			oldNum = atoiSafe(m[1])
			newNum = atoiSafe(m[2])
			out = append(out, diffLine{kind: '@', text: raw})
			continue
		}
		switch {
		case strings.HasPrefix(raw, "+"):
			out = append(out, diffLine{kind: '+', text: raw[1:], newNum: newNum})
			newNum++
		case strings.HasPrefix(raw, "-"):
			out = append(out, diffLine{kind: '-', text: raw[1:], oldNum: oldNum})
			oldNum++
		case strings.HasPrefix(raw, " "):
			out = append(out, diffLine{kind: ' ', text: raw[1:], oldNum: oldNum, newNum: newNum})
			oldNum++
			newNum++
		default:
			// difflib's "\ No newline at end of file", or a producer's own
			// truncation note -- shown verbatim, no line number, never dropped.
			out = append(out, diffLine{kind: 0, text: raw})
		}
	}
	return out
}

// gutterWidth sizes the line-number column off the largest number the diff
// actually shows, so a 9-line file gets a 3-wide gutter and a 12,000-line
// one gets a 5-wide gutter instead of every diff paying for the wider one.
func gutterWidth(lines []diffLine) int {
	max := 0
	for _, l := range lines {
		if l.oldNum > max {
			max = l.oldNum
		}
		if l.newNum > max {
			max = l.newNum
		}
	}
	w := len(itoa(max))
	if w < 3 {
		w = 3
	}
	return w
}

var (
	diffAddStyle     = lipgloss.NewStyle().Foreground(theme.Success)
	diffDelStyle     = lipgloss.NewStyle().Foreground(theme.Danger)
	diffHunkStyle    = lipgloss.NewStyle().Foreground(theme.Info)
	diffContextStyle = lipgloss.NewStyle().Foreground(theme.Text)
	diffMetaStyle    = lipgloss.NewStyle().Foreground(theme.Dim)
)

// renderDiffLine draws one row. It does NOT pre-truncate or pad to width --
// box.render() already truncates/pads every stored line via the ANSI-aware
// helpers in box.go, so doing it again here would double the work and risk
// disagreeing with it. clean() runs on the file content BEFORE it is
// wrapped in color: content lines come from the file the agent edited, an
// untrusted source, and clean() is the one place that strips anything in it
// that could move the cursor -- style codes added AFTER that point are
// trusted, because this function is the only thing that adds them.
func renderDiffLine(l diffLine, gutter int) string {
	switch l.kind {
	case '@':
		return diffHunkStyle.Render(clean(l.text))
	case 0:
		return diffMetaStyle.Render(clean(l.text))
	}

	var marker string
	var style lipgloss.Style
	var num string
	switch l.kind {
	case '+':
		marker, style, num = "+", diffAddStyle, itoa(l.newNum)
	case '-':
		marker, style, num = "-", diffDelStyle, itoa(l.oldNum)
	default: // context
		marker, style, num = " ", diffContextStyle, itoa(l.newNum)
	}
	prefix := padLeft(num, gutter) + " " + marker + " "
	return style.Render(prefix + clean(l.text))
}

func atoiSafe(s string) int {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0
	}
	return n
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}
