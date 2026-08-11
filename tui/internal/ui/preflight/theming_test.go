package preflight

import (
	"fmt"
	"regexp"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// The readiness screen is where a user meets GAIA before anything else works,
// so it is the screen these tests hold to the palette. Both assertions are
// about the ESCAPE CODES, not the words — every other test in this package
// strips styling on purpose, which is exactly why a colour regression could
// never fail one of them.

// renderBothModes returns the readiness screen as it reaches a truecolor
// terminal, once assuming a light background and once a dark one.
func renderBothModes(t *testing.T) (light, dark string) {
	t.Helper()

	// Tests do not run on a TTY, so lipgloss would otherwise strip every colour
	// and both modes would render identically — passing for the wrong reason.
	prevProfile := lipgloss.ColorProfile()
	prevDark := lipgloss.HasDarkBackground()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() {
		lipgloss.SetColorProfile(prevProfile)
		lipgloss.SetHasDarkBackground(prevDark)
	})

	render := func(dark bool) string {
		lipgloss.SetHasDarkBackground(dark)
		f := newFake()
		m, _ := renderAt(t, f, 100, 30)
		return m.View()
	}
	return render(false), render(true)
}

func TestReadinessScreenAdaptsToBackground(t *testing.T) {
	light, dark := renderBothModes(t)

	if stripANSI(light) != stripANSI(dark) {
		t.Fatalf("the two modes rendered different TEXT; the comparison below would be meaningless")
	}
	if light == dark {
		t.Error("the readiness screen renders identically on a light and a dark " +
			"background: its colours are not going through theme tokens")
	}
}

// TestReadinessScreenUsesOnlyPaletteColours is the guard against a hardcoded
// colour surviving anywhere on the screen. Anything the renderer emits has to be
// a value theme.All() declares for the mode being rendered.
func TestReadinessScreenUsesOnlyPaletteColours(t *testing.T) {
	light, dark := renderBothModes(t)

	for _, mode := range []struct {
		name   string
		screen string
		dark   bool
	}{{"light", light, false}, {"dark", dark, true}} {
		var allowed []string
		for _, c := range theme.All() {
			hex := c.Light
			if mode.dark {
				hex = c.Dark
			}
			allowed = append(allowed, strings.ToUpper(hex))
		}
		for _, got := range truecolorsIn(mode.screen) {
			if !nearAny(got, allowed) {
				t.Errorf("%s mode emits %s, which is not in the %s palette",
					mode.name, got, mode.name)
			}
		}
	}
}

// truecolorRE matches the SGR form lipgloss emits for a 24-bit colour:
// 38;2;R;G;B for a foreground and 48;2;R;G;B for a background.
var truecolorRE = regexp.MustCompile(`[34]8;2;(\d{1,3});(\d{1,3});(\d{1,3})`)

// ansi256RE matches the SGR form lipgloss emits when the profile can only do
// indexed colour: 38;5;N for a foreground and 48;5;N for a background. A
// lipgloss.Color("N") — the exact hardcoded-index bug this whole change
// removed — emits this form even on a truecolor profile: termenv's profile
// conversion only ever degrades a colour, never upgrades an indexed one to
// 24-bit. Without matching this form too, the guard is blind to that
// regression.
var ansi256RE = regexp.MustCompile(`[34]8;5;(\d{1,3})`)

func truecolorsIn(s string) []string {
	var out []string
	seen := map[string]bool{}
	add := func(hex string) {
		if !seen[hex] {
			seen[hex] = true
			out = append(out, hex)
		}
	}
	for _, m := range truecolorRE.FindAllStringSubmatch(s, -1) {
		var r, g, b int
		fmt.Sscanf(m[1], "%d", &r)
		fmt.Sscanf(m[2], "%d", &g)
		fmt.Sscanf(m[3], "%d", &b)
		add(fmt.Sprintf("#%02X%02X%02X", r, g, b))
	}
	for _, m := range ansi256RE.FindAllStringSubmatch(s, -1) {
		var n int
		fmt.Sscanf(m[1], "%d", &n)
		add(hexFromANSI256(n))
	}
	return out
}

// hexFromANSI256 converts an indexed colour to RGB via termenv — the same
// library lipgloss itself uses — so this test and the renderer agree on the
// conversion by construction rather than by a hand-rolled xterm cube.
func hexFromANSI256(n int) string {
	c := termenv.ConvertToRGB(termenv.ANSI256Color(n))
	return strings.ToUpper(c.Hex())
}

// nearAny allows a one-step channel drift: lipgloss converts a hex string to
// floating-point RGB and back before emitting it, so #116329 can leave as
// #116328. Anything further off is a colour that did not come from the palette.
func nearAny(got string, allowed []string) bool {
	for _, want := range allowed {
		if maxChannelDelta(got, want) <= 2 {
			return true
		}
	}
	return false
}

func maxChannelDelta(a, b string) int {
	worst := 0
	for i := 1; i < 7; i += 2 {
		var x, y int
		fmt.Sscanf(a[i:i+2], "%x", &x)
		fmt.Sscanf(b[i:i+2], "%x", &y)
		if d := x - y; d > worst {
			worst = d
		} else if -d > worst {
			worst = -d
		}
	}
	return worst
}

var ansiRE = regexp.MustCompile(`\x1b\[[0-9;]*m`)

func stripANSI(s string) string { return ansiRE.ReplaceAllString(s, "") }
