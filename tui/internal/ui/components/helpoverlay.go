package components

import "github.com/charmbracelet/lipgloss"

type HelpContext int

const (
	HelpContextHub HelpContext = iota
	HelpContextChat
)

var helpBoxStyle = lipgloss.NewStyle().
	Border(lipgloss.RoundedBorder()).
	BorderForeground(lipgloss.Color("114")).
	Padding(1, 2)

// RenderHelpOverlay renders a help panel centered over a background view.
func RenderHelpOverlay(ctx HelpContext, background string, width, height int) string {
	var content string
	switch ctx {
	case HelpContextHub:
		content = hubHelpText
	case HelpContextChat:
		content = chatHelpText
	}

	boxWidth := width - 4
	if boxWidth > 60 {
		boxWidth = 60
	}

	box := helpBoxStyle.Width(boxWidth).Render(content)
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
}

// hubHelpText is kept to 20 lines: with the box's padding and border that is
// exactly 24 rows, so the overlay still fits the minimum terminal.
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

  Keyboard Shortcuts
  ──────────────────
  Enter       Send message
  Esc         Cancel streaming / Return to hub
  Ctrl+C      Quit
  PgUp/PgDn   Scroll conversation

  Commands
  ──────────────────
  /help       Show this help
  /hub        Return to Agent Hub
  /clear      Clear conversation`
