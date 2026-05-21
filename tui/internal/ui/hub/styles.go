package hub

import "github.com/charmbracelet/lipgloss"

var (
	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("212")).
			Padding(0, 1)

	dashboardStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("252")).
			Padding(0, 1)

	installedLabel = lipgloss.NewStyle().Foreground(lipgloss.Color("252"))
	activeLabel    = lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Bold(true)
	idleLabel      = lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Bold(true)

	tabActive = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("212")).
			Underline(true).
			Padding(0, 2)

	tabInactive = lipgloss.NewStyle().
			Foreground(lipgloss.Color("243")).
			Padding(0, 2)

	dividerStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("238"))

	statusBarStyle = lipgloss.NewStyle().
			Background(lipgloss.Color("236")).
			Foreground(lipgloss.Color("252")).
			Padding(0, 1)

	// Agent list item styles
	selectedItemStyle = lipgloss.NewStyle().
				Foreground(lipgloss.Color("212")).
				Bold(true)

	normalItemStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("252"))

	descriptionStyle = lipgloss.NewStyle().
				Foreground(lipgloss.Color("243"))

	selectedDescStyle = lipgloss.NewStyle().
				Foreground(lipgloss.Color("252"))

	// Status dots
	activeDot   = lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Render("●")
	idleDot     = lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Render("●")
	installedDot = lipgloss.NewStyle().Foreground(lipgloss.Color("243")).Render("●")
	availableDot = lipgloss.NewStyle().Foreground(lipgloss.Color("243")).Render("○")
	comingSoonDot = lipgloss.NewStyle().Foreground(lipgloss.Color("243")).Render("◌")

	voteStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("214"))
)
