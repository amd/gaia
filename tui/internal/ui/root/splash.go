package root

import (
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/brand"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// renderSplash is the first frame: GAIA's mascot and the name of what is
// starting. It holds for one render while Init's command opens the readiness
// gate behind it, so the launch never opens on an empty terminal.
//
// Inline rather than its own package: it is one function over brand.Banner, and
// a package for that would be more import than screen.
func (m FlagshipModel) renderSplash() string {
	dim := lipgloss.NewStyle().Foreground(theme.Dim)

	lines := []string{
		"",
		brand.Banner(m.width, m.height),
		"",
		"  " + dim.Render("starting "+m.agent.Name+"…"),
	}
	return strings.Join(lines, "\n")
}
