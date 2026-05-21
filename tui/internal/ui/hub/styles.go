package hub

import "github.com/charmbracelet/lipgloss"

// AMD-inspired color palette: greens and teals from the GAIA robot mascot
var (
	// Primary accent — muted green (matches GAIA robot)
	accentColor = lipgloss.Color("114")
	// Bright accent — for selected/highlighted items
	brightAccent = lipgloss.Color("150")

	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(brightAccent).
			Padding(0, 1)

	dashboardStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("252")).
			Padding(0, 1)

	installedLabel = lipgloss.NewStyle().Foreground(lipgloss.Color("252"))
	activeLabel    = lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Bold(true)
	idleLabel      = lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Bold(true)

	tabActive = lipgloss.NewStyle().
			Bold(true).
			Foreground(brightAccent).
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
				Foreground(brightAccent).
				Bold(true)

	normalItemStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("252"))

	descriptionStyle = lipgloss.NewStyle().
				Foreground(lipgloss.Color("243"))

	selectedDescStyle = lipgloss.NewStyle().
				Foreground(lipgloss.Color("252"))

	// Status dots — green for active, amber for idle, dim for installed
	activeDot    = lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Render("●")
	idleDot      = lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Render("●")
	installedDot = lipgloss.NewStyle().Foreground(lipgloss.Color("243")).Render("●")
	availableDot = lipgloss.NewStyle().Foreground(lipgloss.Color("243")).Render("○")
	comingSoonDot = lipgloss.NewStyle().Foreground(lipgloss.Color("243")).Render("◌")

	voteStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("214"))
)
