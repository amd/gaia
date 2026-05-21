package components

import (
	"fmt"

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

	connectedDot   = lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Render("●")
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

	left := fmt.Sprintf(" %s %s %s", dot, state.AgentName, status)

	right := ""
	if state.Hint != "" {
		right = state.Hint + " "
	} else if state.Steps > 0 {
		right = fmt.Sprintf("steps: %d ", state.Steps)
	}

	gap := width - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 0 {
		gap = 0
	}

	padding := ""
	for i := 0; i < gap; i++ {
		padding += " "
	}

	return statusBarStyle.Width(width).Render(left + padding + right)
}
