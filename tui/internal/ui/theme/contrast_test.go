package theme

import (
	"fmt"
	"go/ast"
	"go/parser"
	gotoken "go/token"
	"math"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
)

// The backgrounds below are the real defaults of the three terminals GAIA is
// tested on, plus Solarized (light and dark), which every one of them ships,
// plus Nord — not a GAIA-tested terminal's own default, but the lightest dark
// background in common use anywhere near this palette (Nord #2E3440 luminance
// 0.0341 vs One Half Dark #282C34's 0.0250; GNOME Tango Dark #2E3436 and
// Solarized Dark High-Contrast #073642 are lighter than One Half Dark too, at
// 0.0330 and 0.0308, but both are darker than Nord, so Nord alone subsumes
// them — a token that clears its floor against Nord clears it against those
// two as well). One Half Dark was previously documented as "the lightest
// common dark background", which was never true of this set and left Danger
// and SurfaceBG failing their floors against Nord/Tango/Solarized-HC
// undetected. Solarized Light is the darkest common light background, so a
// token that clears its floor against both ends clears it on anything in
// between.
type background struct {
	name string
	hex  string
	dark bool
}

var backgrounds = []background{
	{"macOS Terminal · Basic", "#FFFFFF", false},
	{"GNOME Terminal · light", "#FFFFFF", false},
	{"Windows Terminal · One Half Light", "#FAFAFA", false},
	{"Solarized Light", "#FDF6E3", false},

	{"macOS Terminal · Pro", "#000000", true},
	{"GNOME Terminal · Ubuntu", "#300A24", true},
	{"Windows Terminal · Campbell", "#0C0C0C", true},
	{"One Half Dark", "#282C34", true},
	{"Solarized Dark", "#002B36", true},
	{"Nord", "#2E3440", true},
}

type role int

const (
	// roleText carries information as words. WCAG 2.1 AA for body text.
	roleText role = iota
	// roleFaint is deliberately recessive text that always duplicates
	// something stated elsewhere on the row. WCAG AA for large text.
	roleFaint
	// roleChrome is a rule, a bar track, or mascot shading. Not information —
	// the floor only has to keep it from vanishing into the background.
	roleChrome
)

func (r role) floor() float64 {
	switch r {
	case roleText:
		return 4.5
	case roleFaint:
		return 3.0
	default:
		return 1.5
	}
}

func (r role) String() string {
	return [...]string{"text", "faint", "chrome"}[r]
}

// entry is one palette colour and the job it does.
type entry struct {
	name  string
	color lipgloss.AdaptiveColor
	role  role
}

// tokens must name every AdaptiveColor exported by the package —
// TestEveryTokenHasAFloor enforces that, so a new colour cannot be added
// without deciding what contrast it owes the reader.
var tokens = []entry{
	{"Text", Text, roleText},
	{"Dim", Dim, roleText},
	{"Accent", Accent, roleText},
	{"AccentBright", AccentBright, roleText},
	{"Success", Success, roleText},
	{"Warning", Warning, roleText},
	{"Danger", Danger, roleText},
	{"Info", Info, roleText},
	{"Highlight", Highlight, roleText},
	{"Selected", Selected, roleText},

	{"Faint", Faint, roleFaint},

	{"Divider", Divider, roleChrome},
	{"ArtBright", ArtBright, roleChrome},
	{"ArtBody", ArtBody, roleChrome},
	{"ArtMid", ArtMid, roleChrome},
	{"ArtDetail", ArtDetail, roleChrome},
	{"ArtShadow", ArtShadow, roleChrome},
	{"ArtEye", ArtEye, roleChrome},

	// Fills cover the terminal background, so against it they only need to be
	// visible as a block; legibility is the pair's job (TestFillPairs).
	{"AccentFillBG", AccentFillBG, roleChrome},
	{"WarnFillBG", WarnFillBG, roleChrome},
	{"DangerFillBG", DangerFillBG, roleChrome},
	{"InfoFillBG", InfoFillBG, roleChrome},
	{"SurfaceBG", SurfaceBG, roleChrome},
}

// fill pairs paint their own background, so they are judged against each
// other rather than against a terminal background. Most are body text on a
// filled button (4.5:1); the status dots are a short word's worth of colour on
// SurfaceBG, so they get the 3:1 roleFaint-equivalent floor instead — see
// SurfaceBG's comment in theme.go for why that pairing is tight enough to
// document explicitly.
var fills = []struct {
	name   string
	fg, bg lipgloss.AdaptiveColor
	floor  float64
}{
	{"accent button", OnFill, AccentFillBG, 4.5},
	{"warning button", OnFill, WarnFillBG, 4.5},
	{"danger button", OnFill, DangerFillBG, 4.5},
	{"info button", OnFill, InfoFillBG, 4.5},
	{"quiet surface", OnSurface, SurfaceBG, 4.5},

	// components/statusbar.go paints these two directly on SurfaceBG (the
	// connected/disconnected dot); confirmed by grep as the only tokens
	// actually rendered there. Warning is never rendered on SurfaceBG in this
	// codebase — hub/styles.go's idle/warning styles carry no Background — and
	// is deliberately left out: forcing a floor for a pairing that does not
	// exist would require either lowering SurfaceBG's own terminal-background
	// floor or picking an amber outside the ANSI-256 cube's darkest available
	// in-family corner (#875F00, still short of the floor this pairing would
	// need) — both of which this file's rules forbid.
	{"success dot on surface", Success, SurfaceBG, 3.0},
	{"danger dot on surface", Danger, SurfaceBG, 3.0},
}

// fillForegrounds sit on a painted background, so TestFillPairs covers them
// instead: judged against the bare terminal they would fail by design (white
// text on a white terminal).
var fillForegrounds = map[string]bool{"OnFill": true, "OnSurface": true}

// hueFamily is the semantic colour family a token belongs to. Luminance-only
// floors cannot see a value that clears its contrast target but rounds into a
// DIFFERENT family once a 256-colour terminal degrades it — e.g. Accent's
// green landing on the teal between green and Info's blue. hueFamily and
// TestDegradationPreservesHue below exist to catch exactly that.
type hueFamily int

const (
	hueNeutral hueFamily = iota // greys: hue is meaningless, only saturation matters
	hueRed
	hueAmber
	hueGreen
	hueCyan
	hueBlue
	hueMagenta
)

func (f hueFamily) String() string {
	return [...]string{"neutral", "red", "amber", "green", "cyan", "blue", "magenta"}[f]
}

// hueArcs are [min,max] in degrees, sized from the actual hue of every value
// in the palette (both truecolor and ANSI-256-degraded) plus margin on both
// sides so a legitimate shade never sits at the edge. hueRed wraps past 360.
var hueArcs = map[hueFamily][2]float64{
	hueRed:     {348, 372},
	hueAmber:   {20, 55},
	hueGreen:   {65, 168},
	hueCyan:    {173, 197},
	hueBlue:    {197, 232},
	hueMagenta: {290, 335},
}

// neutralSatMax is the saturation below which a colour reads as achromatic:
// hue is undefined there, so both true neutrals and a family member that has
// legitimately faded toward grey (ArtDetail.Light, once degraded) are fine.
const neutralSatMax = 0.20

// family declares the intended hue family for every token All() returns, next
// to the palette table above so the intent is readable in one place.
// TestEveryTokenHasAHueFamily enforces that nothing is missing, the same
// discipline TestEveryTokenHasAFloor already applies to contrast.
var family = map[string]hueFamily{
	"Text": hueNeutral, "Dim": hueNeutral, "Faint": hueNeutral,
	"Accent": hueGreen, "AccentBright": hueGreen, "Success": hueGreen,
	"Warning": hueAmber, "Danger": hueRed, "Info": hueBlue, "Highlight": hueMagenta,
	"Selected":  hueAmber,
	"Divider":   hueNeutral,
	"ArtBright": hueGreen, "ArtBody": hueGreen, "ArtMid": hueGreen, "ArtDetail": hueGreen,
	"ArtShadow": hueNeutral, "ArtEye": hueCyan,
	"AccentFillBG": hueGreen, "WarnFillBG": hueAmber, "DangerFillBG": hueRed, "InfoFillBG": hueBlue,
	"SurfaceBG": hueNeutral, "OnSurface": hueNeutral, "OnFill": hueNeutral,
}

// hueSat returns hex's hue in [0,360) and saturation in [0,1] (standard HSL).
func hueSat(hex string) (hue, sat float64) {
	r, g, b := parseHex(hex)
	max := math.Max(r, math.Max(g, b))
	min := math.Min(r, math.Min(g, b))
	l := (max + min) / 2
	if max == min {
		return 0, 0 // achromatic: hue is undefined, sat is correctly 0
	}
	d := max - min
	if l > 0.5 {
		sat = d / (2 - max - min)
	} else {
		sat = d / (max + min)
	}
	switch max {
	case r:
		hue = math.Mod((g-b)/d, 6)
	case g:
		hue = (b-r)/d + 2
	default:
		hue = (r-g)/d + 4
	}
	hue *= 60
	if hue < 0 {
		hue += 360
	}
	return hue, sat
}

// inFamily reports whether hex's hue lands in f's arc. A neutral family
// requires low saturation; a colour family accepts either its arc OR a
// saturation so low the hue carries no real information (faded-to-grey is
// not the "wrong colour" defect this test targets — a wrong SATURATED hue,
// like teal for green, is).
func inFamily(hex string, f hueFamily) (ok bool, hue, sat float64) {
	hue, sat = hueSat(hex)
	if f == hueNeutral {
		return sat <= neutralSatMax, hue, sat
	}
	if sat <= neutralSatMax {
		return true, hue, sat
	}
	arc := hueArcs[f]
	h := hue
	if arc[1] > 360 && h < arc[1]-360 {
		h += 360 // let the wraparound (red) arc compare on one axis
	}
	return h >= arc[0] && h <= arc[1], hue, sat
}

func TestEveryTokenHasAHueFamily(t *testing.T) {
	for name := range All() {
		if _, ok := family[name]; !ok {
			t.Errorf("theme.%s has no declared hue family in this file", name)
		}
	}
}

// TestDegradationPreservesHue is the regression test for the class of bug
// where a value clears its contrast floor but a 256-colour terminal rounds it
// into a different semantic colour's territory. The other tests in this file
// are luminance-only and structurally cannot see that — hue is the only thing
// that can, which is exactly why the teal-for-green regression shipped past
// them.
func TestDegradationPreservesHue(t *testing.T) {
	for name, f := range family {
		c, ok := All()[name]
		if !ok {
			continue // covered by TestEveryTokenHasAHueFamily
		}
		for _, side := range []struct {
			mode string
			hex  string
		}{{"light", c.Light}, {"dark", c.Dark}} {
			deg := degradeTo256(side.hex)
			if ok, hue, sat := inFamily(deg, f); !ok {
				t.Errorf("%s.%s: %s degrades to %s, hue %.1f (sat %.2f) is outside the %s family",
					name, side.mode, side.hex, deg, hue, sat, f)
			}
		}
	}
}

func TestTokenContrastOnEveryTerminalBackground(t *testing.T) {
	for _, bg := range backgrounds {
		for _, tk := range tokens {
			hex := tk.color.Light
			if bg.dark {
				hex = tk.color.Dark
			}
			got := contrast(hex, bg.hex)
			if got < tk.role.floor() {
				t.Errorf("%s (%s, %s) on %s (%s): contrast %.2f:1, floor %.1f:1",
					tk.name, hex, tk.role, bg.name, bg.hex, got, tk.role.floor())
			}
		}
	}
}

func TestFillPairs(t *testing.T) {
	for _, f := range fills {
		for _, mode := range []struct {
			name string
			dark bool
		}{{"light", false}, {"dark", true}} {
			fg, bg := f.fg.Light, f.bg.Light
			if mode.dark {
				fg, bg = f.fg.Dark, f.bg.Dark
			}
			got := contrast(fg, bg)
			if got < f.floor {
				t.Errorf("%s in %s mode: %s on %s is %.2f:1, floor %.1f:1",
					f.name, mode.name, fg, bg, got, f.floor)
			}
		}
	}
}

// A token whose two values are identical is either an oversight or a deliberate
// fill; anything else means one mode was never considered.
func TestForegroundTokensAdapt(t *testing.T) {
	for _, tk := range tokens {
		if tk.color.Light != tk.color.Dark {
			continue
		}
		switch tk.name {
		case "AccentFillBG", "WarnFillBG", "DangerFillBG", "InfoFillBG":
			continue // painted surfaces — same in both modes on purpose
		}
		t.Errorf("%s is %s in both modes: it was tuned for one background only",
			tk.name, tk.color.Light)
	}
}

// TestAllIsComplete parses theme.go and fails when it declares an AdaptiveColor
// that All() does not return. All() is what the render tests check screens
// against, so a colour missing from it is a colour nothing can police.
func TestAllIsComplete(t *testing.T) {
	all := All()
	fset := gotoken.NewFileSet()
	f, err := parser.ParseFile(fset, "theme.go", nil, 0)
	if err != nil {
		t.Fatalf("cannot parse theme.go: %v", err)
	}
	ast.Inspect(f, func(n ast.Node) bool {
		vs, ok := n.(*ast.ValueSpec)
		if !ok {
			return true
		}
		for i, name := range vs.Names {
			if i >= len(vs.Values) || !isAdaptiveColor(vs.Values[i]) {
				continue
			}
			if _, ok := all[name.Name]; !ok {
				t.Errorf("theme.%s is declared but All() does not return it", name.Name)
			}
		}
		return true
	})
}

// TestEveryTokenHasAFloor makes sure no palette entry escapes measurement.
func TestEveryTokenHasAFloor(t *testing.T) {
	covered := map[string]bool{}
	for _, tk := range tokens {
		covered[tk.name] = true
	}
	for name := range fillForegrounds {
		covered[name] = true
	}
	for name := range All() {
		if !covered[name] {
			t.Errorf("theme.%s has no contrast floor in this file", name)
		}
	}
}

func isAdaptiveColor(e ast.Expr) bool {
	cl, ok := e.(*ast.CompositeLit)
	if !ok {
		return false
	}
	sel, ok := cl.Type.(*ast.SelectorExpr)
	return ok && sel.Sel.Name == "AdaptiveColor"
}

// --- WCAG 2.1 relative luminance and contrast ------------------------------

func contrast(aHex, bHex string) float64 {
	a, b := luminance(aHex), luminance(bHex)
	if a < b {
		a, b = b, a
	}
	return (a + 0.05) / (b + 0.05)
}

func luminance(hex string) float64 {
	r, g, b := parseHex(hex)
	return 0.2126*channel(r) + 0.7152*channel(g) + 0.0722*channel(b)
}

func channel(v float64) float64 {
	if v <= 0.03928 {
		return v / 12.92
	}
	return math.Pow((v+0.055)/1.055, 2.4)
}

func parseHex(hex string) (r, g, b float64) {
	if len(hex) != 7 || hex[0] != '#' {
		panic("theme: colour must be #RRGGBB, got " + hex)
	}
	v := func(s string) float64 {
		n := 0
		for _, c := range s {
			n <<= 4
			switch {
			case c >= '0' && c <= '9':
				n |= int(c - '0')
			case c >= 'a' && c <= 'f':
				n |= int(c-'a') + 10
			case c >= 'A' && c <= 'F':
				n |= int(c-'A') + 10
			default:
				panic("theme: bad hex digit in " + hex)
			}
		}
		return float64(n) / 255
	}
	return v(hex[1:3]), v(hex[3:5]), v(hex[5:7])
}

// macOS Terminal.app has no truecolor: it advertises xterm-256color, so lipgloss
// down-converts every hex above to the nearest of 256 fixed indices before it
// reaches the screen. That conversion can move a colour far enough to lose the
// contrast the table was tuned for, and it happens on one of the three terminals
// GAIA is tested on — so the floors are re-checked against what actually lands.
func TestPaletteSurvivesANSI256Degradation(t *testing.T) {
	for _, bg := range backgrounds {
		for _, tk := range tokens {
			hex := tk.color.Light
			if bg.dark {
				hex = tk.color.Dark
			}
			got := contrast(degradeTo256(hex), bg.hex)
			if got < tk.role.floor() {
				t.Errorf("%s on %s: %s degrades to %s and drops to %.2f:1, floor %.1f:1",
					tk.name, bg.name, hex, degradeTo256(hex), got, tk.role.floor())
			}
		}
	}
}

func TestFillPairsSurviveANSI256Degradation(t *testing.T) {
	for _, f := range fills {
		for _, dark := range []bool{false, true} {
			fg, bg := f.fg.Light, f.bg.Light
			if dark {
				fg, bg = f.fg.Dark, f.bg.Dark
			}
			got := contrast(degradeTo256(fg), degradeTo256(bg))
			if got < f.floor {
				t.Errorf("%s: %s on %s degrades to %s on %s and drops to %.2f:1, floor %.1f:1",
					f.name, fg, bg, degradeTo256(fg), degradeTo256(bg), got, f.floor)
			}
		}
	}
}

// degradeTo256 is the exact conversion lipgloss performs on a 256-colour
// terminal — same library, same lookup — so this measures what lands, not an
// approximation of it.
func degradeTo256(hex string) string {
	c := termenv.ANSI256.Convert(termenv.RGBColor(hex))
	r, g, b, _ := termenv.ConvertToRGB(c).RGBA()
	return fmt.Sprintf("#%02X%02X%02X", r>>8, g>>8, b>>8)
}
