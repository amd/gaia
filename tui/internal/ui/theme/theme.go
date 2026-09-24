// Package theme is the single place the TUI decides what a colour means.
//
// Every colour here is a lipgloss.AdaptiveColor, so the same token resolves to
// a different value depending on whether the terminal's background is light or
// dark. Screens ask for a ROLE (theme.Text, theme.Danger) and never for a
// number, which is what stops a palette tuned on one background from becoming
// unreadable on the other.
//
// The brand hues — accent, mascot, neutrals — come from the GAIA design
// language (docs/spec/gaia-design-language.mdx): graphite and ivory neutrals
// with a copper accent, the same literals the website and the Agent UI use, so
// the three surfaces read as one product. Status hues do NOT follow the brand:
// Success stays green, Warning amber, Danger red, Info blue, because a terminal
// that paints a failed check in copper is a worse terminal.
//
// Status hues keep the One Half Light / One Half Dark shape — a theme that
// ships with Windows Terminal, GNOME Terminal and macOS Terminal — darkened on
// the light side because that theme, like nearly every terminal theme, sits
// below WCAG AA for body text on a white background. contrast_test.go holds
// every token to a measured floor against ten real terminal backgrounds.
//
// The guarantee covers truecolor and 256-colour terminals — the latter matters
// because macOS Terminal is 256-colour only, so lipgloss down-converts, and the
// tests re-check every floor on the converted value, including the floor a
// token owes another token when one is painted on top of the other (see
// SurfaceBG below). Below that, at 16 colours,
// there is nothing to guarantee: every colour becomes one of the sixteen the
// USER's theme defines, so what lands on screen is their palette, not this one.
// A terminal reaches that tier when it is not a TTY at all (a pipe, a CI log, or
// an SSH session on Windows, where termenv reports Ascii and CLICOLOR_FORCE
// lifts it only as far as 16 colours).
package theme

import (
	"os"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// EnvTheme forces a mode for terminals that never answer the background-colour
// query — most often over SSH, inside tmux, or in a CI log. Values: light, dark,
// auto (the default: ask the terminal, assume dark if it stays silent).
const EnvTheme = "GAIA_TUI_THEME"

// Init resolves the light/dark decision once, BEFORE Bubble Tea owns stdin.
//
// Detection writes an OSC query to the terminal and reads the reply; done
// lazily, Bubble Tea consumes that reply and types it into the focused input.
// Same constraint as components.PrimeRenderer, and it is called alongside it.
func Init() {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(EnvTheme))) {
	case "light":
		lipgloss.SetHasDarkBackground(false)
	case "dark":
		lipgloss.SetHasDarkBackground(true)
	default:
		// Ask the terminal now and cache the answer. A terminal that stays
		// silent resolves to dark, which is what the TUI has always assumed.
		lipgloss.SetHasDarkBackground(lipgloss.HasDarkBackground())
	}
}

// IsDark reports the mode currently in effect. Only for diagnostics — screens
// should use the tokens, which already carry both values.
func IsDark() bool { return lipgloss.HasDarkBackground() }

// Text-carrying roles. Everything here holds at least 4.5:1 against every
// background in contrast_test.go, so it is safe for body copy.
var (
	// Text is primary copy: labels, answers, values. Graphite and ivory, the
	// design language's neutrals — both carry a faint violet-to-warm cast that
	// the greys below share, so nothing in the palette reads as a flat grey.
	Text = lipgloss.AdaptiveColor{Light: "#242129", Dark: "#F0EDE7"}
	// Dim is secondary copy: hints, descriptions, key names, detail lines.
	Dim = lipgloss.AdaptiveColor{Light: "#645D6A", Dark: "#AAA5B0"}
	// Accent is the GAIA copper: borders, brand marks, commands to run. Light
	// is NOT a darkened #EBA474: among 256-colour entries dark enough for body
	// text on white, the only saturated warm one is #875F00 — already Warning
	// and Selected. #9A4930 is the most saturated light copper that rounds to
	// #875F5F, a warm near-neutral, instead of onto that shared cell.
	Accent = lipgloss.AdaptiveColor{Light: "#9A4930", Dark: "#EBA474"}
	// AccentBright is the emphasised copper: titles, the selected row. Both
	// values collapse onto Accent's degraded cell on a 256-colour terminal;
	// that is fine, the two never sit adjacent and never distinguish one state
	// from another.
	AccentBright = lipgloss.AdaptiveColor{Light: "#8A4530", Dark: "#F7B189"}
	// Success means a check passed, a connection is live. Light's B channel is
	// kept low (0x2D) so a 256-colour terminal rounds it to the cube's one true
	// green, #005F00 — a higher B rounds to the teal at #005F5F, which reads as
	// a different colour, not just a duller green.
	Success = lipgloss.AdaptiveColor{Light: "#0B6E2D", Dark: "#3FD98A"}
	// Warning means unknown, idle, or "read this before continuing".
	Warning = lipgloss.AdaptiveColor{Light: "#8A5300", Dark: "#FFAF3F"}
	// Danger means failed, disconnected, destructive. Dark is lighter than a
	// plain "bright red" would need to be for body text alone: the same value
	// is also used as the disconnected-status dot painted on SurfaceBG (see
	// RenderStatusBar), which owes SurfaceBG.Dark 3:1, not just the terminal
	// 4.5:1. #FF6B6B held the terminal floor but degraded to only 2.14:1 on
	// SurfaceBG.Dark's degraded grey; #FF9C9C clears both (contrast_test.go).
	Danger = lipgloss.AdaptiveColor{Light: "#B32020", Dark: "#FF9C9C"}
	// Info is the blue used for keybindings, the user's own turn, tool names.
	Info = lipgloss.AdaptiveColor{Light: "#0A5FA8", Dark: "#5FBFFF"}
	// Highlight is the magenta cursor and spinner.
	Highlight = lipgloss.AdaptiveColor{Light: "#A3277F", Dark: "#FF87D7"}
)

// Faint is tertiary text — a version tag next to the thing it versions
// (delegate.go's versionStyle, its only consumer). Held to 3:1, not 4.5:1: it
// is deliberately recessive and never carries information that is not also in
// the row it sits on.
// Both carry Text's violet cast rather than a blue-grey, so the recessive tier
// reads as the same family of neutral.
var Faint = lipgloss.AdaptiveColor{Light: "#8A8492", Dark: "#8F8A95"}

// Divider draws rules and the empty half of a progress bar. Non-text, so it is
// only held to a visible-but-quiet floor. Light sits exactly on the ANSI-256
// cube grey #AFAFAF: the original #BFC4CB has a slight cool tint that a
// 256-colour terminal rounds unevenly per channel, landing on pale cyan
// (#AFD7D7) instead of a grey — an exact cube entry can't drift like that.
var Divider = lipgloss.AdaptiveColor{Light: "#AFAFAF", Dark: "#5A5A5A"}

// Filled surfaces: a foreground painted on an explicit background. The pair has
// to contrast with ITSELF — the terminal's own background is covered up — so
// these do not vary by mode. Solid, saturated, white text: the one combination
// that reads the same on every terminal.
var (
	// Burnished copper, the same literal the website and Agent UI fill with.
	AccentFillBG = lipgloss.AdaptiveColor{Light: "#9A4930", Dark: "#9A4930"}
	WarnFillBG   = lipgloss.AdaptiveColor{Light: "#8A5300", Dark: "#8A5300"}
	DangerFillBG = lipgloss.AdaptiveColor{Light: "#B32020", Dark: "#B32020"}
	InfoFillBG   = lipgloss.AdaptiveColor{Light: "#0A5FA8", Dark: "#0A5FA8"}
	OnFill       = lipgloss.AdaptiveColor{Light: "#FFFFFF", Dark: "#FFFFFF"}
)

// Selected is the gold marking the row you are ON in a list — a palette, a menu,
// a set of answers. Deliberately NOT the brand accent: the accent already means
// "a command you can run" everywhere else in this UI, so using it for "the one
// under your cursor" made the two indistinguishable. Gold and copper are near
// neighbours, which is why contrast_test.go's hue arcs keep them provably
// apart on a 256-colour terminal as well as a truecolor one.
var Selected = lipgloss.AdaptiveColor{Light: "#8A5300", Dark: "#FFC65C"}

// SurfaceBG is the quiet band — the status bar, an unselected button. Unlike a
// badge it does follow the mode, and it has to stay visible against BOTH ends of
// its mode's background range: a #303030 bar disappears on a One Half Dark
// terminal, which is how the old status bar behaved.
//
// It is also the backdrop the connected/disconnected status dot (Success/
// Danger) is painted on — components/statusbar.go builds the dot with its own
// Foreground and only then wraps it in SurfaceBG's Background, so the dot has
// to clear a THIRD floor beyond the two below: 3:1 against SurfaceBG itself
// (contrast_test.go's fills, "success/danger dot on surface"), not just 4.5:1
// as body text on the terminal. That third floor is genuinely tight —
// SurfaceBG has to hold, at once: ≥1.5:1 against BOTH ends of its mode's
// terminal-background range (Solarized Light and Nord are the tightest; if
// SurfaceBG can't clear this the bar itself vanishes), ≥3:1 for the dots
// painted on it, and ≥4.5:1 for OnSurface. Dark could not hold all three at
// its old #4A4A4A (1.41:1 on Nord, below the 1.5 floor) without the dot moving
// too — see Danger's comment above.
//
// That is also why SurfaceBG is NOT the design language's raised surface
// (#EAE5DC / #222128). Those are cards on a KNOWN canvas; this band sits on
// whatever background the user chose, and both fail the first floor outright —
// #EAE5DC is 1.22:1 on a white terminal, #222128 is 1.31:1 on a black one, so
// the bar would simply disappear. What carries over instead is the cast: warm
// stone on light, violet graphite on dark. Both still degrade to an exact
// ANSI-256 cube grey (#AFAFAF, #5F5F5F), so the tint is a truecolor bonus and
// the floors are measured on a neutral either way.
var (
	SurfaceBG = lipgloss.AdaptiveColor{Light: "#C2B8A8", Dark: "#615D69"}
	// OnSurface mirrors Text. The ivory clears the dark pairing where a plain
	// off-white would not: #E8E8E8 degrades to #D7D7D7 and holds only 4.44:1 on
	// SurfaceBG.Dark's degraded grey, while #F0EDE7 degrades to #FFFFD7 and
	// holds 6.25:1.
	OnSurface = lipgloss.AdaptiveColor{Light: "#242129", Dark: "#F0EDE7"}
)

// Mascot shading, brightest to darkest as drawn on a dark terminal. On a light
// terminal the ladder inverts so the same rung keeps the same emphasis. Art is
// decorative — held only to "visible", not to a text floor. The mascot is a
// brand mark, so it follows the accent into copper; only the shadow and the eye
// sit outside that family, and neither ever read as green.
//
// The rungs are picked for where they LAND, not just where they start: on a
// 256-colour terminal the warm cube entries usable here are #FFAF87 and
// #D7875F (both hue 20), and below those every warm entry is either #875F5F —
// a near-neutral, which is fine — or a saturated red/amber, which is not. That
// is why the darker rungs are tuned to fade toward neutral rather than deepen.
var (
	ArtBright = lipgloss.AdaptiveColor{Light: "#6B4632", Dark: "#F8BC99"}
	ArtBody   = lipgloss.AdaptiveColor{Light: "#9A4930", Dark: "#EBA474"} // mirrors Accent
	ArtMid    = lipgloss.AdaptiveColor{Light: "#C4815C", Dark: "#C9814F"}
	ArtDetail = lipgloss.AdaptiveColor{Light: "#B5A79E", Dark: "#96674C"}
	ArtShadow = lipgloss.AdaptiveColor{Light: "#AFAFAF", Dark: "#555555"} // degradation-safe grey, see Divider
	ArtEye    = lipgloss.AdaptiveColor{Light: "#007D8A", Dark: "#4DE8E8"}
)

// All is the whole palette, by token name. Tests use it two ways: to hold every
// colour to a measured contrast floor, and to assert that a rendered screen
// emits nothing outside it — which is how a stray hardcoded colour is caught.
func All() map[string]lipgloss.AdaptiveColor {
	return map[string]lipgloss.AdaptiveColor{
		"Text": Text, "Dim": Dim, "Accent": Accent, "AccentBright": AccentBright,
		"Success": Success, "Warning": Warning, "Danger": Danger, "Info": Info,
		"Highlight": Highlight, "Faint": Faint, "Divider": Divider,
		"AccentFillBG": AccentFillBG, "WarnFillBG": WarnFillBG,
		"DangerFillBG": DangerFillBG, "InfoFillBG": InfoFillBG, "OnFill": OnFill,
		"SurfaceBG": SurfaceBG, "OnSurface": OnSurface,
		"Selected":  Selected,
		"ArtBright": ArtBright, "ArtBody": ArtBody, "ArtMid": ArtMid,
		"ArtDetail": ArtDetail, "ArtShadow": ArtShadow, "ArtEye": ArtEye,
	}
}
