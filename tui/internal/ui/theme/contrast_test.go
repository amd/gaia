package theme

import (
	"fmt"
	"go/ast"
	"go/parser"
	gotoken "go/token"
	"math"
	"reflect"
	"sort"
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
// DIFFERENT family once a 256-colour terminal degrades it — a Success green
// landing on the teal between green and Info's blue, a copper landing on
// Warning's amber. hueFamily and TestEveryValueStaysInItsHueFamily below exist
// to catch exactly that.
type hueFamily int

const (
	hueNeutral hueFamily = iota // greys: hue is meaningless, only chroma matters
	hueRed
	hueCopper
	hueAmber
	hueGreen
	hueCyan
	hueBlue
	hueMagenta
)

func (f hueFamily) String() string {
	return [...]string{"neutral", "red", "copper", "amber", "green", "cyan", "blue", "magenta"}[f]
}

// hueArcs are [min,max] in degrees, sized from the actual hue of every value
// in the palette (both truecolor and ANSI-256-degraded) plus margin on both
// sides so a legitimate shade never sits at the edge. hueRed wraps past 360.
//
// Copper is the crowded one: it is the brand accent AND it sits between two
// colours that already carry meaning. Measured, the three families are
//
//	red     every value lands on exactly 0.0 (#AF0000, #FFAFAF)
//	copper  14.0 … 24.6 (#8A4530, #9A4930; degraded #FFAF87, #D7875F at 20.0)
//	amber   30.0 … 45.0 (degraded #FFAF5F, #875F00, #FFD75F)
//
// so the arcs below leave copper 2° clear of red's ceiling and 3.4° clear of
// amber's floor, and no two arcs overlap. Red's arc was ±12 around a family
// whose every member measures 0.0; trimming it to ±10 is what makes room under
// copper, and still leaves red five times the margin copper gets.
var hueArcs = map[hueFamily][2]float64{
	hueRed:     {350, 370},
	hueCopper:  {12, 26},
	hueAmber:   {28, 55},
	hueGreen:   {65, 168},
	hueCyan:    {173, 197},
	hueBlue:    {197, 232},
	hueMagenta: {290, 335},
}

// neutralChromaMax is the chroma (max channel − min channel) below which a
// colour reads as achromatic: hue is undefined there, so both true neutrals and
// a family member that has legitimately faded toward grey (Accent.Light's
// #875F5F, once degraded) are fine.
//
// Chroma, not HSL saturation: saturation is lightness-dependent, so the SAME
// 40/255 of colour scores 0.17 on #875F5F and 1.00 on #FFFFD7 (Text.Dark's
// degraded value) purely because one is mid-grey and the other is near-white.
// Chroma scores both at 0.157, which is what "how much colour is actually
// left" means. The ceiling is set just above that, and every value in this
// palette that genuinely carries a hue degrades to chroma ≥ 0.314 — twice the
// ceiling — so nothing saturated can slip through.
const neutralChromaMax = 0.16

// neutralHueSlack is how far outside its family's arc a FADED value may sit —
// low chroma buys tolerance, not immunity.
//
// Chroma alone cannot do this job. #875F5F (Accent.Light degraded — a warm
// grey, 12.0° from copper's arc) and #5F875F (a sage green, 94.0° from it) both
// measure chroma 0.157, so a ceiling that admits the first admits the second,
// and a green motif installs cleanly into the palette whose one hard rule is
// that the motif is not green. Hue distance separates them; chroma does not.
//
// 30° is picked from the measured palette: the widest distance any legitimate
// low-chroma value sits from its own arc is 12.0° (every copper that fades —
// Accent, AccentBright, AccentFillBG, ArtBody, ArtDetail), and the nearest
// proven leak is the sage green at 94.0°, with a blue-violet accent at 132.0°.
// That leaves 18° of headroom above the real values and 64° below the first
// false pass.
const neutralHueSlack = 30

// family declares the intended hue family for every token All() returns, next
// to the palette table above so the intent is readable in one place.
// TestEveryTokenHasAHueFamily enforces that nothing is missing, the same
// discipline TestEveryTokenHasAFloor already applies to contrast.
var family = map[string]hueFamily{
	"Text": hueNeutral, "Dim": hueNeutral, "Faint": hueNeutral,
	"Accent": hueCopper, "AccentBright": hueCopper, "Success": hueGreen,
	"Warning": hueAmber, "Danger": hueRed, "Info": hueBlue, "Highlight": hueMagenta,
	"Selected":  hueCopper,
	"Divider":   hueNeutral,
	"ArtBright": hueCopper, "ArtBody": hueCopper, "ArtMid": hueCopper, "ArtDetail": hueCopper,
	"ArtShadow": hueNeutral, "ArtEye": hueCyan,
	"AccentFillBG": hueCopper, "WarnFillBG": hueAmber, "DangerFillBG": hueRed, "InfoFillBG": hueBlue,
	"SurfaceBG": hueNeutral, "OnSurface": hueNeutral, "OnFill": hueNeutral,
}

// hueChroma returns hex's hue in [0,360) and its chroma in [0,1] — the plain
// max-minus-min distance between channels, which unlike HSL saturation does
// not change with lightness.
func hueChroma(hex string) (hue, chroma float64) {
	r, g, b := parseHex(hex)
	max := math.Max(r, math.Max(g, b))
	min := math.Min(r, math.Min(g, b))
	if max == min {
		return 0, 0 // achromatic: hue is undefined, chroma is correctly 0
	}
	d := max - min
	chroma = d
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
	return hue, chroma
}

// hueArcDistance is how many degrees hue sits outside arc, and 0 when it is
// inside. Hue is circular and hueRed's arc deliberately runs past 360, so the
// distance is measured at hue−360, hue and hue+360 and the nearest wins — a 2°
// red and a 358° red both score 0 without any caller needing to know which
// arcs wrap.
func hueArcDistance(hue float64, arc [2]float64) float64 {
	best := math.Inf(1)
	for _, h := range []float64{hue - 360, hue, hue + 360} {
		d := 0.0
		switch {
		case h < arc[0]:
			d = arc[0] - h
		case h > arc[1]:
			d = h - arc[1]
		}
		best = math.Min(best, d)
	}
	return best
}

// inFamily reports whether hex belongs to f. Three tiers, because "how much
// colour is left" and "which colour it is" are separate questions:
//
//	chroma 0             a true grey — hue does not exist, so every family takes it
//	chroma ≤ the ceiling faded — hue still points somewhere, and it must point
//	                     within neutralHueSlack of the family's arc
//	chroma above it      saturated — it must land inside the arc outright
//
// The middle tier is the load-bearing one. Treating low chroma as an unqualified
// pass, which is what this did before, makes every hue assertion in the file
// vacuous for any washed-out value: a sage green in the copper motif measures
// chroma 0.157 and sails straight through.
func inFamily(hex string, f hueFamily) (ok bool, hue, chroma float64) {
	hue, chroma = hueChroma(hex)
	if f == hueNeutral {
		return chroma <= neutralChromaMax, hue, chroma
	}
	if chroma == 0 {
		return true, hue, chroma
	}
	d := hueArcDistance(hue, hueArcs[f])
	if chroma <= neutralChromaMax {
		return d <= neutralHueSlack, hue, chroma
	}
	return d == 0, hue, chroma
}

// TestFadedColoursKeepTheirHue pins the rule the palette test depends on but
// cannot state: how much a value is allowed to drift once it fades. Every hex
// here is a real 256-colour cube entry, and the first two are the whole
// argument — identical chroma, 94° apart, and only one of them is a copper.
func TestFadedColoursKeepTheirHue(t *testing.T) {
	for _, c := range []struct {
		hex  string
		f    hueFamily
		want bool
		why  string
	}{
		{"#875F5F", hueCopper, true, "faded copper, 12° out: inside the slack"},
		{"#5F875F", hueCopper, false, "faded sage green, 94° out: same chroma, wrong colour"},
		{"#5F5F87", hueCopper, false, "faded blue-violet, 132° out"},
		{"#5F5F5F", hueCopper, true, "chroma 0: no hue exists to be wrong about"},
		{"#D7875F", hueCopper, true, "saturated copper, inside the arc"},
		{"#AF0000", hueCopper, false, "saturated and 12° out: slack is for faded values only"},
		{"#AF0000", hueRed, true, "hue 0 matches red's 350–370 arc across the wrap"},
		{"#FFAFAF", hueRed, true, "the other end of red, also across the wrap"},
		{"#875F5F", hueNeutral, true, "faded enough to pass as grey"},
		{"#D7875F", hueNeutral, false, "too much colour left to be a neutral"},
	} {
		if got, hue, chroma := inFamily(c.hex, c.f); got != c.want {
			t.Errorf("inFamily(%s, %s) = %v, want %v — %s (hue %.1f, chroma %.3f)",
				c.hex, c.f, got, c.want, c.why, hue, chroma)
		}
	}
}

// sharedFamily is the exhaustive list of hue families more than one token is
// allowed to sit in, and why. It enforces rule 4 of the design language — never
// let two roles share a hue family — the only way a rule with real exceptions
// can be enforced: by naming every exception rather than by not checking.
//
// Membership is compared for EQUALITY, not containment, so the entry is a
// decision and not a wildcard: adding a token to a listed family fails this
// test until someone writes down what the shared hue is for, and removing the
// last extra member fails it too rather than leaving a stale excuse behind.
// Families with one token need no entry.
var sharedFamily = map[hueFamily]struct {
	members []string
	why     string
}{
	hueNeutral: {
		members: []string{"Text", "Dim", "Faint", "Divider", "ArtShadow", "SurfaceBG", "OnSurface", "OnFill"},
		why: "Greys are the absence of a role, not a role. Rule 4 is about not " +
			"making two MEANINGS look alike, and none of these means anything by hue.",
	},
	hueCopper: {
		members: []string{"Accent", "AccentBright", "Selected", "AccentFillBG", "ArtBright", "ArtBody", "ArtMid", "ArtDetail"},
		why: "One brand hue, deliberately: the accent, its emphasis, the row you are " +
			"on, the surface it fills, and the mascot are all the same idea, and the " +
			"design language asks for exactly one brand colour rather than a family of them.",
	},
	hueAmber: {
		members: []string{"Warning", "WarnFillBG"},
		why: "Warning and the surface that carries it are one meaning in two positions. " +
			"They share the light literal #8A5300 outright because it is the only " +
			"saturated warm value in the ANSI-256 cube dark enough to hold 4.5:1 on " +
			"every light terminal background this file measures.",
	},
	hueRed: {
		members: []string{"Danger", "DangerFillBG"},
		why: "Danger and the surface that carries it are one meaning in two " +
			"positions, not two roles competing for the same hue.",
	},
	hueBlue: {
		members: []string{"Info", "InfoFillBG"},
		why: "Info and the surface that carries it are one meaning in two " +
			"positions, not two roles competing for the same hue.",
	},
}

func TestNoTwoRolesShareAHueFamily(t *testing.T) {
	got := map[hueFamily][]string{}
	for name := range All() {
		f, ok := family[name]
		if !ok {
			continue // covered by TestEveryTokenHasAHueFamily
		}
		got[f] = append(got[f], name)
	}
	for f, names := range got {
		allowed, listed := sharedFamily[f]
		if len(names) < 2 {
			if listed {
				t.Errorf("sharedFamily lists %s, but only %v is in it now — delete the entry",
					f, names)
			}
			continue
		}
		sort.Strings(names)
		want := append([]string(nil), allowed.members...)
		sort.Strings(want)
		if !listed {
			t.Errorf("%v all sit in the %s family: two roles that look alike are two "+
				"roles the reader cannot tell apart. Give one its own hue, or add %s "+
				"to sharedFamily with a reason.", names, f, f)
			continue
		}
		if !reflect.DeepEqual(names, want) {
			t.Errorf("%s family holds %v but sharedFamily excuses %v — reconcile the two, "+
				"and say why any newcomer belongs", f, names, want)
		}
		if len(allowed.why) < 40 {
			t.Errorf("sharedFamily[%s] needs a reason, not a label: %q", f, allowed.why)
		}
	}
	for f := range sharedFamily {
		if len(got[f]) == 0 {
			t.Errorf("sharedFamily excuses %s, which no token is in any more", f)
		}
	}
}

func TestEveryTokenHasAHueFamily(t *testing.T) {
	for name := range All() {
		if _, ok := family[name]; !ok {
			t.Errorf("theme.%s has no declared hue family in this file", name)
		}
	}
}

// TestEveryValueStaysInItsHueFamily is the regression test for the class of bug
// where a value clears its contrast floor but is the wrong colour — either
// wrong as written, or wrong only after a 256-colour terminal rounds it into a
// different semantic colour's territory. The other tests in this file are
// luminance-only and structurally cannot see that, which is exactly why the
// teal-for-green regression shipped past them.
//
// Both the value and its degradation are checked, because either alone has a
// blind spot: #4A6B4A is a visibly green motif colour that degrades to the pure
// grey #5F5F5F, so a degraded-only check reads it as a harmless neutral.
func TestEveryValueStaysInItsHueFamily(t *testing.T) {
	for name, f := range family {
		c, ok := All()[name]
		if !ok {
			continue // covered by TestEveryTokenHasAHueFamily
		}
		for _, side := range []struct {
			mode string
			hex  string
		}{{"light", c.Light}, {"dark", c.Dark}} {
			for _, v := range []struct {
				how, hex string
			}{{"is", side.hex}, {"degrades to", degradeTo256(side.hex)}} {
				if ok, hue, chroma := inFamily(v.hex, f); !ok {
					t.Errorf("%s.%s (%s) %s %s, hue %.1f (chroma %.3f) is outside the %s family",
						name, side.mode, side.hex, v.how, v.hex, hue, chroma, f)
				}
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

// TestAllIsComplete parses theme.go and fails when it declares a colour that
// All() does not return. All() is what the render tests check screens against,
// so a colour missing from it is a colour nothing can police.
func TestAllIsComplete(t *testing.T) {
	all := All()
	fset := gotoken.NewFileSet()
	f, err := parser.ParseFile(fset, "theme.go", nil, 0)
	if err != nil {
		t.Fatalf("cannot parse theme.go: %v", err)
	}
	r := newColourDecls(colourPkgsOf(t, "theme.go", f), f)
	for _, name := range r.order {
		if !r.isColour(name) {
			continue
		}
		if _, ok := all[name]; !ok {
			t.Errorf("theme.%s is declared but All() does not return it", name)
		}
	}
}

// TestColourDeclsResolvesByType is the guard on the guard: every spelling below
// declares a colour, and only the first was visible to the shape-matching
// version of this check, so the rest could be added to theme.go and go
// unmeasured with green CI. The last three are the controls — a resolver that
// says yes to everything cannot catch anything.
func TestColourDeclsResolvesByType(t *testing.T) {
	const src = `package theme

import "github.com/charmbracelet/lipgloss"

func pair(l, d string) lipgloss.AdaptiveColor { return lipgloss.AdaptiveColor{Light: l, Dark: d} }
func name() string                            { return "accent" }

var literal = lipgloss.AdaptiveColor{Light: "#0B6E2D", Dark: "#3FD98A"}
var alias = literal
var twoHops = alias
var fromHelper = pair("#0B6E2D", "#3FD98A")
var converted = lipgloss.Color("#3FB950")
var annotated lipgloss.AdaptiveColor
var loopA = loopB
var loopB = loopA

var label = name()
var count = 3
`
	fset := gotoken.NewFileSet()
	f, err := parser.ParseFile(fset, "synthetic.go", src, 0)
	if err != nil {
		t.Fatalf("cannot parse synthetic source: %v", err)
	}
	r := newColourDecls(colourPkgsOf(t, "synthetic.go", f), f)
	for name, want := range map[string]bool{
		"literal":    true,
		"alias":      true,
		"twoHops":    true,
		"fromHelper": true,
		"converted":  true,
		"annotated":  true,
		"loopA":      false, // must answer, not recurse forever
		"label":      false,
		"count":      false,
	} {
		if got := r.isColour(name); got != want {
			t.Errorf("isColour(%s) = %v, want %v", name, got, want)
		}
	}
}

// colourDecls answers "is this package-level name a colour" by the TYPE it
// resolves to rather than the shape it is written in. Requiring a composite
// literal — which is what this did before — is a test of punctuation, not of
// meaning: `var BrandTint = Accent` and `var BrandTint = pair("#0B6E2D",
// "#3FD98A")` are both perfectly good colours and neither is a composite
// literal, so both used to declare a token that All() never returned and no
// test in this package ever measured.
type colourDecls struct {
	pkgs  map[string]string
	typ   map[string]ast.Expr // name -> its declared type, when written
	val   map[string]ast.Expr // name -> its initialiser
	fnRes map[string]ast.Expr // func name -> its single result type
	order []string
	memo  map[string]bool
	busy  map[string]bool
}

func newColourDecls(pkgs map[string]string, f *ast.File) *colourDecls {
	r := &colourDecls{
		pkgs: pkgs, typ: map[string]ast.Expr{}, val: map[string]ast.Expr{},
		fnRes: map[string]ast.Expr{}, memo: map[string]bool{}, busy: map[string]bool{},
	}
	for _, d := range f.Decls {
		switch d := d.(type) {
		case *ast.FuncDecl:
			if d.Recv == nil && d.Type.Results != nil && len(d.Type.Results.List) == 1 {
				r.fnRes[d.Name.Name] = d.Type.Results.List[0].Type
			}
		case *ast.GenDecl:
			for _, s := range d.Specs {
				vs, ok := s.(*ast.ValueSpec)
				if !ok {
					continue
				}
				for i, n := range vs.Names {
					r.order = append(r.order, n.Name)
					r.typ[n.Name] = vs.Type
					if i < len(vs.Values) {
						r.val[n.Name] = vs.Values[i]
					}
				}
			}
		}
	}
	return r
}

func (r *colourDecls) isColour(name string) bool {
	if got, ok := r.memo[name]; ok {
		return got
	}
	if r.busy[name] {
		return false // `var a = b; var b = a` is not a colour, and must not hang
	}
	r.busy[name] = true
	defer func() { r.busy[name] = false }()
	got := false
	if t := r.typ[name]; t != nil {
		got = isColourTypeExpr(r.pkgs, t) // an explicit type settles it
	} else if v := r.val[name]; v != nil {
		got = r.exprIsColour(v)
	}
	r.memo[name] = got
	return got
}

func (r *colourDecls) exprIsColour(e ast.Expr) bool {
	switch e := e.(type) {
	case *ast.ParenExpr:
		return r.exprIsColour(e.X)
	case *ast.CompositeLit:
		return isColourTypeExpr(r.pkgs, e.Type)
	case *ast.Ident:
		return r.isColour(e.Name) // an alias of a colour is a colour
	case *ast.CallExpr:
		if isColourTypeExpr(r.pkgs, e.Fun) {
			return true // a conversion, lipgloss.Color("…")
		}
		if id, ok := e.Fun.(*ast.Ident); ok {
			return isColourTypeExpr(r.pkgs, r.fnRes[id.Name])
		}
	}
	return false
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
