// Package brand is GAIA's identity on screen: the mascot, the wordmark, and
// the tagline every full-screen entry point opens with.
//
// It depends on lipgloss and the theme palette and nothing else, so any screen
// can render it without pulling in a view package.
package brand

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// CompactHeightRows is the terminal height below which Banner drops the mascot
// for a one-line wordmark. The art alone is 20 rows; keeping it on an 80x24
// terminal leaves nothing for the rows underneath it and pushes the footer off
// screen.
const CompactHeightRows = 32

// ArtWidth is the widest row of the mascot, in columns. A terminal narrower
// than this plus a margin gets the compact form too — art that wraps is worse
// than no art.
const ArtWidth = 42

// robotArt is the GAIA mascot. Each glyph is a shading rung, densest first;
// artStyles maps every one of them.
var robotArt = []string{
	"               +=-------                 ",
	"           =====++======-----            ",
	"        ++======+*=========-----         ",
	"      +++++++++++===========------       ",
	"    ++++*#*++++++++============-=--      ",
	"  +***+++==#+++++++++==============--    ",
	" +#####*++=*%+++++##+++==============    ",
	" *##%#%##++*%*++*%%%%%%%%%#########+=-   ",
	" +#%#%%*#+*#%++#%%%#######%########%%%+  ",
	"  *+**#####%++*%%%##*--+##%%%%%##++##%++ ",
	"   +#####%%*+*#%%%##+--+*##%%%##*--*##** ",
	"    +##%##++*#%%%%###++###%%%%%#*==##*#   ",
	"    +**##+*++#%%%%%%####%%%%%%%%####*    ",
	"     +*%%%**#+*%##%%%%%%%%%%%%%%%%%#+    ",
	"       *##*###**+*####%%%%%%%%%%###=     ",
	"         ==+*#######+*##########+=       ",
	"               +#############*=          ",
	"             +=***%%%%#**                 ",
	"           %%%##*##**##***==              ",
	"              #*+++++++**+=              ",
}

// artStyles maps every glyph the art may contain to its shading. There is no
// default case: a glyph with no entry here renders unstyled, and
// TestEveryArtGlyphHasAStyle is what keeps the art and this map in step.
func artStyles() map[rune]lipgloss.Style {
	return map[rune]lipgloss.Style{
		'%': lipgloss.NewStyle().Foreground(theme.ArtBright), // body highlights
		'#': lipgloss.NewStyle().Foreground(theme.ArtBody),   // solid green
		'*': lipgloss.NewStyle().Foreground(theme.ArtMid),    // mid-tone
		'+': lipgloss.NewStyle().Foreground(theme.ArtDetail), // detail
		'=': lipgloss.NewStyle().Foreground(theme.ArtShadow), // shading
		'-': lipgloss.NewStyle().Foreground(theme.ArtEye),    // eyes
	}
}

// Robot renders the mascot, one styled row per line, with a trailing newline.
// Trailing padding is trimmed — it is invisible on screen and only shows up as
// noise in a captured frame.
func Robot() string {
	styles := artStyles()
	var out strings.Builder
	for _, line := range robotArt {
		for _, ch := range strings.TrimRight(line, " ") {
			if style, ok := styles[ch]; ok {
				out.WriteString(style.Render(string(ch)))
				continue
			}
			out.WriteRune(ch)
		}
		out.WriteByte('\n')
	}
	return out.String()
}

// Wordmark is the product name, spaced and accented.
func Wordmark() string {
	return lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright).Render("  G A I A")
}

// Tagline is the one-line description under the wordmark.
func Tagline() string {
	return lipgloss.NewStyle().Foreground(theme.Dim).Italic(true).Render("  " + TaglineText)
}

// TaglineText is the tagline in plain words, so a test can assert on it
// without stripping ANSI.
const TaglineText = "Your local AI agent — by AMD"

// Banner is the header for a full-screen view: the mascot over the wordmark
// where there is room, and the wordmark alone where there is not.
//
// An UNKNOWN size (zero width or height) gets the compact form. Bubble Tea
// renders once before the first WindowSizeMsg and assumes 80x24 until then, so
// a 23-row banner drawn at that moment overflows, scrolls the terminal, and
// leaves the cursor-relative repaint misaligned for the rest of the session —
// every later frame, chat included, then draws in the wrong place. The banner
// growing on the first resize costs far less than that.
func Banner(width, height int) string {
	if compact(width, height) {
		return truncate(Wordmark()+Tagline(), width)
	}
	return Robot() + "\n" + Wordmark() + "  " + Tagline() + "\n"
}

// compact reports whether the mascot fits. An unknown dimension counts as not
// fitting — see Banner.
func compact(width, height int) bool {
	return height < CompactHeightRows || width < ArtWidth+2
}

func truncate(s string, width int) string {
	if width <= 0 {
		return s
	}
	return ansi.Truncate(s, width, "…")
}
