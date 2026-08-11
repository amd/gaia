package hub

import (
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// AMD-inspired color palette: greens and teals from the GAIA robot mascot
var (
	// Primary accent — the GAIA green (matches the robot)
	accentColor = theme.Accent
	// Bright accent — for selected/highlighted items
	brightAccent = theme.AccentBright

	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(brightAccent).
			Padding(0, 1)

	dashboardStyle = lipgloss.NewStyle().
			Foreground(theme.Text).
			Padding(0, 1)

	installedLabel = lipgloss.NewStyle().Foreground(theme.Text)
	activeLabel    = lipgloss.NewStyle().Foreground(theme.Success).Bold(true)
	idleLabel      = lipgloss.NewStyle().Foreground(theme.Warning).Bold(true)

	tabActive = lipgloss.NewStyle().
			Bold(true).
			Foreground(brightAccent).
			Underline(true).
			Padding(0, 2)

	tabInactive = lipgloss.NewStyle().
			Foreground(theme.Dim).
			Padding(0, 2)

	dividerStyle = lipgloss.NewStyle().
			Foreground(theme.Divider)

	statusBarStyle = lipgloss.NewStyle().
			Background(theme.SurfaceBG).
			Foreground(theme.OnSurface).
			Padding(0, 1)

	// Agent list item styles
	selectedItemStyle = lipgloss.NewStyle().
				Foreground(brightAccent).
				Bold(true)

	normalItemStyle = lipgloss.NewStyle().
			Foreground(theme.Text)

	descriptionStyle = lipgloss.NewStyle().
				Foreground(theme.Dim)

	selectedDescStyle = lipgloss.NewStyle().
				Foreground(theme.Text)

	// Status dots — green for active, amber for idle, dim for installed
	activeDotStyle     = lipgloss.NewStyle().Foreground(theme.Success)
	idleDotStyle       = lipgloss.NewStyle().Foreground(theme.Warning)
	installedDotStyle  = lipgloss.NewStyle().Foreground(theme.Dim)
	availableDotStyle  = lipgloss.NewStyle().Foreground(theme.Dim)
	comingSoonDotStyle = lipgloss.NewStyle().Foreground(theme.Dim)

	voteStyle = lipgloss.NewStyle().
			Foreground(theme.Warning)
)

// Dots are rendered per call. An AdaptiveColor resolves at Render time, so a
// package-level Render() would freeze the light/dark choice before theme.Init
// has made it.
func activeDot() string     { return activeDotStyle.Render("●") }
func idleDot() string       { return idleDotStyle.Render("●") }
func installedDot() string  { return installedDotStyle.Render("●") }
func availableDot() string  { return availableDotStyle.Render("○") }
func comingSoonDot() string { return comingSoonDotStyle.Render("◌") }
