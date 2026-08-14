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
	// helpTruncated replaces the last visible line when the window cannot hold
	// the whole panel, so a reader can tell the list was cut rather than ended.
	helpTruncated = "  … (window too short for the rest)"
)

// RenderHelpOverlay renders a help panel centered over a background view.
//
// The panel is bounded by BOTH dimensions. It used to be bounded only by width:
// on a short terminal the box came out taller than the screen and lipgloss.Place
// clipped it from both ends at once, so the reader lost the title AND the last
// bindings with nothing on screen saying so. Now it drops its vertical padding
// first, then cuts lines from the bottom and says it did.
func RenderHelpOverlay(ctx HelpContext, background string, width, height int) string {
	var content string
	switch ctx {
	case HelpContextHub:
		content = hubHelpText
	case HelpContextChat:
		content = chatHelpText
	}

	boxWidth := width - 4
	if boxWidth > helpBoxMaxWidth {
		boxWidth = helpBoxMaxWidth
	}
	// lipgloss draws the border outside Width, so the box occupies boxWidth+2
	// columns and the horizontal padding (2 each side) leaves this much for the
	// text. Any less and there is no panel to draw, so leave the view alone.
	inner := boxWidth - 4
	if inner < 1 || height < 3 {
		return background
	}

	style := helpBoxStyle
	chrome := helpChromeRows
	if height < helpTightHeight {
		style = style.Padding(0, 2)
		chrome = helpTightChromeRows
	}

	lines := fitHelpLines(content, inner, height-chrome)
	box := style.Width(boxWidth).Render(strings.Join(lines, "\n"))
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
}

// fitHelpLines forces the panel body to a known number of rows and columns.
// Row count has to be exact: a line that lipgloss soft-wraps adds a row nobody
// counted, and the panel silently grows past the clamp it was just given.
func fitHelpLines(content string, inner, rows int) []string {
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
	lines = lines[:rows]
	lines[rows-1] = ansi.Truncate(helpTruncated, inner, "…")
	return lines
}

// hubHelpText is kept to 20 lines: with the box's padding and border that is
// exactly 24 rows, so the overlay still fits the minimum terminal. chatHelpText
// owes the same budget.
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
  Esc         Cancel the turn — or back to the hub
  Esc twice   Give up waiting on the cancel
  Ctrl+C      Quit

  Scroll        ↑ / ↓ one line · PgUp/PgDn page
  Home / End    Top / bottom, if the composer is empty
  Mouse wheel   Anywhere in the transcript

  Copy text out
  ──────────────────
  Ctrl+T      Select with the mouse — your own
              terminal's copy and paste. Wheel
              stops scrolling; Esc turns it off.
  Ctrl+Y      Copy the whole answer
  Ctrl+B      Copy the last code block

  Commands    /help · /hub · /clear · /setup · /memory`
