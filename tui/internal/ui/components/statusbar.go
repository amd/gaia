package components

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

type StatusBarState struct {
	AgentName string
	Connected bool
	Steps     int
	Streaming bool
	// AwaitingDecision is set while the agent is parked on a confirmation the
	// user has not answered. It outranks Streaming, which is true at the same
	// time and is the wrong word for it: the turn is not producing anything,
	// it is waiting on a person. Reading "streaming" for minutes while a
	// prompt sits unanswered is how a question reads as a hang.
	AwaitingDecision bool
	Hint             string
}

var (
	// No background fill. A band of near-black across the bottom of a black
	// terminal is a seam, not a surface — it reads as grubby rather than as
	// structure. The divider above it already separates the status line from
	// the transcript.
	statusBarStyle = lipgloss.NewStyle().
			Foreground(theme.Dim).
			Padding(0, 1)

	connectedDotStyle    = lipgloss.NewStyle().Foreground(theme.Success)
	disconnectedDotStyle = lipgloss.NewStyle().Foreground(theme.Danger)
)

// minHintWidth is the narrowest a truncated hint may get before it is dropped
// outright: below this the ellipsis is most of what is left, which tells the
// reader nothing and still costs the columns.
const minHintWidth = 8

// The dots are rendered per call, not stored pre-rendered: an AdaptiveColor
// resolves when it is rendered, and a package-level Render() would freeze the
// light/dark choice before theme.Init has made it.
func connectedDot() string    { return connectedDotStyle.Render("●") }
func disconnectedDot() string { return disconnectedDotStyle.Render("●") }

// RenderStatusBar draws the one-row bar at the foot of a screen.
//
// It always returns exactly one line of exactly `width` display columns. Both
// halves are measured with ansi.StringWidth rather than len: the hint is full
// of arrows and middots (`↑↓ scroll · Esc cancel`), and a byte count reads that
// 22-column string as 27, which pushed the hint five columns left of the right
// edge. StringWidth is also the measure that survives a caller passing a styled
// string — it skips the escape sequences a byte count would charge for — and it
// counts a wide CJK rune as the two columns the terminal actually draws.
func RenderStatusBar(state StatusBarState, width int) string {
	if width <= 0 {
		return ""
	}

	dot := disconnectedDot()
	status := "disconnected"
	if state.Connected {
		dot = connectedDot()
		status = "connected"
	}
	if state.Streaming {
		status = "streaming"
	}
	if state.AwaitingDecision {
		status = "waiting for your answer"
	}

	left := " " + dot + " " + fmt.Sprintf("%s %s", state.AgentName, status)
	right := ""
	if state.Hint != "" {
		right = state.Hint
	} else if state.Steps > 0 {
		right = fmt.Sprintf("steps: %d", state.Steps)
	}
	if right != "" {
		// One column of air before the bar's own padding, mirroring the leading
		// space on the left.
		right += " "
	}

	// Padding(0, 1) spends one column on each side, so the content this
	// function builds has `width-2` to live in.
	inner := width - 2
	if inner < 1 {
		// Narrower than its own padding: lipgloss cannot render the style below
		// that floor, so the bar becomes a bare band of the width it was asked
		// for rather than silently coming out wider than the screen.
		return statusBarStyle.UnsetPadding().Width(width).Render("")
	}

	leftW := ansi.StringWidth(left)
	rightW := ansi.StringWidth(right)

	// Overflowing used to clamp the gap to 1 and let the content run past the
	// bar, at which point lipgloss wrapped it onto a second row and sheared the
	// layout below. Truncate instead — and truncate the hint first, because
	// which agent is talking matters more than how to scroll it.
	if budget := inner - leftW - 1; rightW > budget {
		if budget < minHintWidth {
			right, rightW = "", 0
		} else {
			right = ansi.Truncate(right, budget, "…")
			rightW = ansi.StringWidth(right)
		}
	}
	if leftW+rightW > inner {
		left = ansi.Truncate(left, inner-rightW, "…")
		leftW = ansi.StringWidth(left)
	}

	gap := inner - leftW - rightW
	if gap < 0 {
		gap = 0
	}
	content := left + strings.Repeat(" ", gap) + right
	return statusBarStyle.Width(width).Render(content)
}
