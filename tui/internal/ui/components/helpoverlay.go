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

const hubHelpText = `  GAIA Agent Hub

  Keyboard Shortcuts
  ──────────────────
  Enter       Launch selected agent
  /           Search agents
  Tab         Next category
  Shift+Tab   Previous category
  d           Delete/uninstall agent
  v           Vote for coming-soon agent
  r           Request a new agent
  ?           Toggle this help
  q, Ctrl+C   Quit

  Data: Votes send only the agent ID
  to amd-gaia.ai — no personal data.`

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
  /init       Initialize agent LLM
  /clear      Clear conversation`
