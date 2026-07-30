package theme

import (
	"testing"

	"github.com/charmbracelet/lipgloss"
)

// GAIA_TUI_THEME is the escape hatch for a terminal that never answers the
// background-colour query — over SSH, inside tmux, in a CI log. If it did not
// win, those users would have no way to fix an unreadable screen.
func TestEnvOverrideWins(t *testing.T) {
	restore := lipgloss.HasDarkBackground()
	t.Cleanup(func() { lipgloss.SetHasDarkBackground(restore) })

	for _, tc := range []struct {
		env  string
		want bool
	}{
		{"light", false},
		{"dark", true},
		{"LIGHT", false},
		{"  Dark  ", true},
	} {
		// Start from the opposite of what we expect, so a no-op Init fails.
		lipgloss.SetHasDarkBackground(!tc.want)
		t.Setenv(EnvTheme, tc.env)
		Init()
		if got := IsDark(); got != tc.want {
			t.Errorf("GAIA_TUI_THEME=%q: IsDark()=%v, want %v", tc.env, got, tc.want)
		}
	}
}

// An unset or unrecognised value must fall through to detection rather than
// silently pinning a mode. IsDark() is defined in terms of
// lipgloss.HasDarkBackground(), so comparing the two can never fail — this
// pins a known mode first and asserts Init left it untouched instead.
func TestUnknownValueFallsBackToDetection(t *testing.T) {
	restore := lipgloss.HasDarkBackground()
	t.Cleanup(func() { lipgloss.SetHasDarkBackground(restore) })

	lipgloss.SetHasDarkBackground(false)
	t.Setenv(EnvTheme, "chartreuse")
	Init()
	if IsDark() != false {
		t.Error("an unrecognised GAIA_TUI_THEME did not fall through to detection: " +
			"it pinned a mode instead of leaving the existing one alone")
	}
}

func TestEveryTokenIsSixDigitHex(t *testing.T) {
	for name, c := range All() {
		for _, hex := range []string{c.Light, c.Dark} {
			if len(hex) != 7 || hex[0] != '#' {
				t.Errorf("theme.%s has %q, want #RRGGBB", name, hex)
				continue
			}
			// parseHex panics on a bad digit, which is the assertion.
			parseHex(hex)
		}
	}
}
