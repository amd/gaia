package components

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

type StatusBarState struct {
	AgentName string
	Connected bool
	Steps     int
	Streaming bool
	Hint      string
}

var (
	statusBarStyle = lipgloss.NewStyle().
			Background(theme.SurfaceBG).
			Foreground(theme.OnSurface).
			Padding(0, 1)

	connectedDotStyle    = lipgloss.NewStyle().Foreground(theme.Success)
	disconnectedDotStyle = lipgloss.NewStyle().Foreground(theme.Danger)
)

// The dots are rendered per call, not stored pre-rendered: an AdaptiveColor
// resolves when it is rendered, and a package-level Render() would freeze the
// light/dark choice before theme.Init has made it.
func connectedDot() string    { return connectedDotStyle.Render("●") }
func disconnectedDot() string { return disconnectedDotStyle.Render("●") }

func RenderStatusBar(state StatusBarState, width int) string {
	dot := disconnectedDot()
	status := "disconnected"
	if state.Connected {
		dot = connectedDot()
		status = "connected"
	}
	if state.Streaming {
		status = "streaming"
	}

	// Build left and right content
	leftText := fmt.Sprintf("%s %s", state.AgentName, status)
	rightText := ""
	if state.Hint != "" {
		rightText = state.Hint
	} else if state.Steps > 0 {
		rightText = fmt.Sprintf("steps: %d", state.Steps)
	}

	// Calculate padding (accounting for dot + spaces + padding(0,1) = 2 chars)
	// left: " ● agentname status" — dot is 1 visible char
	// right: "hint "
	leftVisibleLen := 3 + len(leftText) // " ● " + text
	rightVisibleLen := len(rightText)
	if rightVisibleLen > 0 {
		rightVisibleLen++ // trailing space
	}

	innerWidth := width - 2 // padding(0,1) adds 1 on each side
	gap := innerWidth - leftVisibleLen - rightVisibleLen
	if gap < 1 {
		gap = 1
	}

	content := " " + dot + " " + leftText + strings.Repeat(" ", gap) + rightText
	return statusBarStyle.Width(width).Render(content)
}
