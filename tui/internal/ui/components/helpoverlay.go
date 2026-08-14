package components

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

type HelpContext int

const (
	HelpContextHub HelpContext = iota
	HelpContextChat
)

var helpBoxStyle = lipgloss.NewStyle().
	Border(lipgloss.RoundedBorder()).
	BorderForeground(theme.Accent).
	Padding(1, 2)

const (
	// helpBoxMaxWidth keeps the panel readable on a wide terminal: a help list
	// stretched to 200 columns is one long scan line per binding.
	helpBoxMaxWidth = 60
	// helpChromeRows is what the box costs beyond its content — one border row
	// and one padding row at each end.
	helpChromeRows = 4
	// helpTightChromeRows is the same with the vertical padding dropped, which
	// is what a short window gets instead of a clipped panel.
	helpTightChromeRows = 2
	// helpTightHeight is the window height below which the padding goes.
	helpTightHeight = 14
)

// RenderHelpOverlay renders a help panel centered over a background view.
//
// The panel is bounded by BOTH dimensions. It used to be bounded only by width:
// on a short terminal the box came out taller than the screen and lipgloss.Place
// clipped it from both ends at once, so the reader lost the title AND the last
// bindings with nothing on screen saying so. Now it drops its vertical padding
// first, then scrolls — see fitHelpLines.
//
// scroll is how many body lines are hidden above the visible window; 0 shows
// the top. The caller (root model) owns and clamps this across key presses —
// HelpMaxScroll reports the ceiling it should clamp to.
func RenderHelpOverlay(ctx HelpContext, background string, width, height, scroll int) string {
	content := helpTextFor(ctx)

	boxWidth, inner, rows, ok := helpBoxSize(width, height)
	if !ok {
		return background
	}

	style := helpBoxStyle
	if height < helpTightHeight {
		style = style.Padding(0, 2)
	}

	lines := fitHelpLines(content, inner, rows, scroll)
	box := style.Width(boxWidth).Render(strings.Join(lines, "\n"))
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
}

// HelpMaxScroll reports the furthest a panel at this size can scroll before
// it reaches its last line — 0 when the whole thing already fits, or when
// there is no room to draw a panel at all. Root uses this to clamp the
// scroll offset it drives on ↑/↓/PgUp/PgDn/Home/End while help is open; it
// has to use EXACTLY the row math fitHelpLines uses below, or a Home jump on
// one and a real line count on the other disagree.
func HelpMaxScroll(ctx HelpContext, width, height int) int {
	_, _, rows, ok := helpBoxSize(width, height)
	if !ok {
		return 0
	}
	lines := strings.Split(helpTextFor(ctx), "\n")
	if len(lines) <= rows {
		return 0
	}
	return maxScrollFor(len(lines), helpContentRows(rows))
}

func helpTextFor(ctx HelpContext) string {
	switch ctx {
	case HelpContextHub:
		return hubHelpText
	default:
		return chatHelpText
	}
}

// helpBoxSize returns the box's own width, the content columns inside its
// padding, and the content row budget for a panel at width x height — the
// same three numbers RenderHelpOverlay and HelpMaxScroll both need, computed
// once so they cannot drift apart.
func helpBoxSize(width, height int) (boxWidth, inner, rows int, ok bool) {
	boxWidth = width - 4
	if boxWidth > helpBoxMaxWidth {
		boxWidth = helpBoxMaxWidth
	}
	// lipgloss draws the border outside Width, so the box occupies boxWidth+2
	// columns and the horizontal padding (2 each side) leaves this much for the
	// text. Any less and there is no panel to draw, so leave the view alone.
	inner = boxWidth - 4
	if inner < 1 || height < 3 {
		return 0, 0, 0, false
	}

	chrome := helpChromeRows
	if height < helpTightHeight {
		chrome = helpTightChromeRows
	}
	rows = height - chrome
	if rows < 1 {
		rows = 1
	}
	return boxWidth, inner, rows, true
}

// helpContentRows is how many of the row budget go to real text once one row
// is set aside for a scroll indicator — all of it, if there is only one row
// to begin with and no room to spare.
func helpContentRows(rows int) int {
	if rows <= 1 {
		return rows
	}
	return rows - 1
}

func maxScrollFor(totalLines, contentRows int) int {
	m := totalLines - contentRows
	if m < 0 {
		return 0
	}
	return m
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// fitHelpLines forces the panel body to a known number of rows and columns.
// Row count has to be exact: a line that lipgloss soft-wraps adds a row nobody
// counted, and the panel silently grows past the clamp it was just given.
//
// When the content is longer than the row budget, one row goes to a scroll
// indicator instead of a content line — replacing the old hard truncation,
// which just cut the list and said "too short for the rest" with no way to
// see what got cut. scroll picks the window; the indicator always says
// whether there is more above, below, or both, so cutting the list is never
// silent.
func fitHelpLines(content string, inner, rows, scroll int) []string {
	lines := strings.Split(content, "\n")
	for i, line := range lines {
		if ansi.StringWidth(line) > inner {
			lines[i] = ansi.Truncate(line, inner, "…")
		}
	}
	if rows < 1 {
		rows = 1
	}
	if len(lines) <= rows {
		return lines
	}

	contentRows := helpContentRows(rows)
	maxScroll := maxScrollFor(len(lines), contentRows)
	scroll = clampInt(scroll, 0, maxScroll)

	visible := append([]string{}, lines[scroll:scroll+contentRows]...)
	if contentRows < rows {
		visible = append(visible, ansi.Truncate(helpScrollIndicator(scroll, maxScroll), inner, "…"))
	}
	return visible
}

// helpScrollIndicator names which direction(s) still have hidden content.
// Only called once maxScroll > 0 has already been established by the caller.
func helpScrollIndicator(scroll, maxScroll int) string {
	switch {
	case scroll <= 0:
		return "  ── ↓ more below · PgDn ──"
	case scroll >= maxScroll:
		return "  ── ↑ more above · PgUp ──"
	default:
		return "  ── ↑ more above · ↓ more below ──"
	}
}

// hubHelpText and chatHelpText are no longer bounded to a fixed line count —
// RenderHelpOverlay scrolls whatever does not fit (see fitHelpLines) — but
// every line still has to fit helpBoxMaxWidth-4 columns, or it soft-wraps and
// throws off the row count the box was told to draw.
const hubHelpText = `  GAIA Agent Hub
  ──────────────────
  Enter       Run the selected agent
  i           Install it (or update it)
  d           Uninstall it
  r           Refresh the agent list
  /           Search agents
  Tab / S-Tab Next / previous category
  v           Vote for a coming-soon agent
  ?           Toggle this help
  q, Ctrl+C   Quit

  Installing a non-verified agent runs
  third-party code — GAIA asks first and
  shows you exactly what you're trusting.

  Votes send only the agent ID to
  amd-gaia.ai; no personal data. Request
  an agent at github.com/amd/gaia/issues.`

const chatHelpText = `  GAIA Chat
  ──────────────────
  Enter       Send (queues if the agent is busy)
  Alt+Enter   New line in the composer (Ctrl+J too)
  Esc         Cancel the turn — or back to the hub
  Esc twice   Give up waiting on the cancel
  Ctrl+C      Quit

  Scroll        ↑ / ↓ line · PgUp/PgDn page
  Home / End    Top / bottom, if the composer is
                empty — otherwise cursor keys
  Mouse wheel   Only in wheel mode — see Ctrl+T

  Copy and paste
  ──────────────────
  Drag to select and use your terminal's own
  copy and paste — right-click, Ctrl+Shift+C,
  Cmd+C, whatever your terminal uses.
  Ctrl+T      Mouse wheel scrolling. Selection is
              off while it is on; Esc ends it.
  Ctrl+V      Paste · Ctrl+Y copy answer · Ctrl+B code

  Commands    /help /hub /clear /bypass
              /setup /memory /model
  /           On an empty line, browse commands —
              ↑/↓ to pick, Enter to run, Esc to close`
