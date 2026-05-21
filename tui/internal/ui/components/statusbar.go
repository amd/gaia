package components

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
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
			Background(lipgloss.Color("236")).
			Foreground(lipgloss.Color("252")).
			Padding(0, 1)

	connectedDot    = lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Render("●")
	disconnectedDot = lipgloss.NewStyle().Foreground(lipgloss.Color("196")).Render("●")
)

func RenderStatusBar(state StatusBarState, width int) string {
	dot := disconnectedDot
	status := "disconnected"
	if state.Connected {
		dot = connectedDot
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
