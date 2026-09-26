// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// "A surface asks for a role and never for a hex literal" — the first rule in
// docs/spec/gaia-design-language.mdx, made enforceable for the TUI.
//
// contrast_test.go measures what the tokens owe the reader; this file makes
// sure a component cannot opt out of them by writing the colour inline, which
// is the only way a value can reach a terminal unmeasured. Without it a
// `lipgloss.Color("#3FB950")` in any Go file ships a green mark into a palette
// whose spec says the brand motif is not green, with fully green CI.
//
// The two web surfaces carry the same guard over their own trees —
// website/src/design/literals.test.ts and
// src/gaia/apps/webui/src/styles/__tests__/contrast.test.ts — with the same
// allowlist discipline: one narrowly-scoped entry apiece, each saying why a
// role cannot carry it, plus a staleness test so an entry cannot outlive what
// it excused. Where those two name one literal in one file, this one names one
// declaration, which is the tightest scope Go source offers.
//
// Go gives this sweep one thing the CSS ones have to work for: literals are
// read off the AST, so a `#3062` issue number in a comment is never a colour
// and no comment-stripping pass is needed.

package theme

import (
	"fmt"
	"go/ast"
	"go/parser"
	gotoken "go/token"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// moduleRoot is the tui/ module, three levels up from internal/ui/theme.
const moduleRoot = "../../.."

// themeFile is the one file allowed to hold colour literals: it is where the
// roles are defined. Everything else in the module has to go through it.
const themeFile = "internal/ui/theme/theme.go"

// exemption excuses ONE top-level declaration in one file. Scoping to the
// declaration rather than the file is what keeps an exemption from quietly
// covering the next literal somebody adds three functions down.
type exemption struct {
	file string // module-relative, forward slashes
	decl string // top-level const/var/func name
	why  string
}

var allowed = []exemption{
	{
		file: "internal/control/svg.go",
		decl: "svgBasePalette",
		why: "the 16 ANSI colours a TERMINAL paints, used to redraw a captured frame as a picture; " +
			"repainting SGR 32 in GAIA's green would make the screenshot lie about the bug it captured",
	},
	{
		file: "internal/control/svg.go",
		decl: "svgDefaultFG",
		why:  "the emulated terminal's own default foreground for cells the captured frame never coloured",
	},
	{
		file: "internal/control/svg.go",
		decl: "svgDefaultBG",
		why:  "the emulated terminal's own default background, same reason as svgDefaultFG",
	},
	{
		file: "internal/ui/components/markdown_style.go",
		decl: "darkSyntax",
		why: "syntax highlighting is measured against the fence's own slab, not the terminal background " +
			"a role is measured against, and a fence has to stay a different colour from prose — " +
			"markdown_syntax_test.go holds every one of them to 4.5:1 on that slab",
	},
	{
		file: "internal/ui/components/markdown_style.go",
		decl: "lightSyntax",
		why:  "the light half of the same table, held to the same floor on its own slab",
	},
}

// modeReachAllowed excuses reading a role's .Light/.Dark side directly. A role
// exists so a caller never has to pick; a caller that picks has hardcoded one
// terminal. Exactly one place in the TUI legitimately does.
var modeReachAllowed = []exemption{
	{
		file: "internal/ui/components/markdown_style.go",
		decl: "gaiaPalette",
		why: "glamour's style config holds colours as plain strings, not AdaptiveColor, " +
			"so the mode has to be resolved before the renderer is built",
	},
}

// ── what counts as a colour ────────────────────────────────────────────────

// #rgb, #rgba, #rrggbb, #rrggbbaa — anywhere inside a string literal, so a URL
// fragment or an SVG template carrying one is caught too.
//
// The 3-and-4 branch also matches an all-digit run, so a string holding an
// issue ref like "#4186" trips this. That over-match is deliberate: excluding
// digit-only runs would stop policing #008 and #0088, which are real colours.
var hexColour = regexp.MustCompile(`#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b`)

// A raw SGR sequence that sets a colour: 30-37, 38, 39, 40-47, 48, 49, 90-97,
// 100-107. Hand-written escapes bypass lipgloss entirely, so they bypass the
// palette, the adaptive mode, and the 256-colour degradation the theme tests
// measure.
var (
	sgrSeq   = regexp.MustCompile(`\x1b\[([0-9;]*)m`)
	sgrParam = regexp.MustCompile(`^(?:3[0-9]|4[0-9]|9[0-7]|10[0-7])$`)
)

// Constructions that turn a value into a colour. A bare type reference
// (`func (k PanelKind) fill() lipgloss.AdaptiveColor`) is not one of these —
// only a call or a composite literal actually mints a colour.
//
// Keyed on the package an import PATH resolves to, never on the identifier a
// file spells — see colourPkg.
var colourCtor = map[string]map[string]bool{
	"lipgloss": {
		"Color": true, "ANSIColor": true, "AdaptiveColor": true,
		"CompleteColor": true, "CompleteAdaptiveColor": true,
	},
	"termenv": {
		"RGBColor": true, "ANSIColor": true, "ANSI256Color": true,
	},
	"colorful": {"Hex": true, "Color": true},
}

// colourType is every type in those packages whose values ARE colours — the
// same set plus the ones you cannot call. This is what makes the sweep
// type-directed instead of spelling-directed, and it is the difference between
// catching a colour and catching one way of writing a colour: lipgloss.Color is
// a STRING type, so `var c lipgloss.Color = "81"` mints a hardcoded cyan with
// no constructor, no hex, and no field named …Color anywhere in it.
var colourType = map[string]map[string]bool{
	"lipgloss": {
		"Color": true, "ANSIColor": true, "AdaptiveColor": true,
		"CompleteColor": true, "CompleteAdaptiveColor": true,
		"TerminalColor": true,
	},
	"termenv": {
		"RGBColor": true, "ANSIColor": true, "ANSI256Color": true, "Color": true,
	},
	"colorful": {"Color": true},
}

var majorVersionSeg = regexp.MustCompile(`^v[0-9]+$`)

// colourPkg maps an import path to its colourCtor key, "" for anything else.
// Resolving the path is what closes the alias bypass: `import lg ".../lipgloss"`
// then `lg.ANSIColor(81)` is a hardcoded cyan with no string in it for the hex
// sweep to see, so a rule keyed on the word "lipgloss" would report green on it.
// Matching on the trailing selector name alone would close it too, but would
// then flag every unrelated `foo.Color(...)` in the tree.
func colourPkg(path string) string {
	seg := path[strings.LastIndex(path, "/")+1:]
	if majorVersionSeg.MatchString(seg) {
		rest := strings.TrimSuffix(path, "/"+seg)
		seg = rest[strings.LastIndex(rest, "/")+1:]
	}
	// go-colorful's directory carries the go- prefix; its package does not.
	seg = strings.TrimPrefix(seg, "go-")
	if colourCtor[seg] == nil {
		return ""
	}
	return seg
}

// A field that takes a colour. Not every colour sink is a lipgloss
// constructor: glamour's style config holds them as plain *string, so
// `Color: strPtr("81")` — an ANSI-256 index, exactly what the markdown
// renderer used to paint headings with — is a colour no other rule here sees.
// Anything ending in Color is one: Color, BackgroundColor, CenterColor.
//
// The suffix is the SUPPLEMENT, for sinks whose declared type is a plain
// string. `declared` carries the primary rule: every field name in the module
// whose type really is a colour, so `fg lipgloss.Color` — how a Go programmer
// would actually spell it — is covered without being named after its type.
func isColourField(name string, declared map[string]bool) bool {
	return strings.HasSuffix(name, "Color") || declared[name]
}

// isColourTypeExpr reports whether a type EXPRESSION denotes a colour, given
// how this file spells the colour packages. Pointers and parentheses are
// transparent; a container is not (its ELEMENT type is what holds the colour,
// which compositeSinks below handles).
func isColourTypeExpr(pkgs map[string]string, e ast.Expr) bool {
	switch e := e.(type) {
	case *ast.ParenExpr:
		return isColourTypeExpr(pkgs, e.X)
	case *ast.StarExpr:
		return isColourTypeExpr(pkgs, e.X)
	case *ast.SelectorExpr:
		pkg, name, ok := qualified(e)
		return ok && colourType[pkgs[pkg]][name]
	}
	return false
}

// colourConst folds e to the constant a slot would receive, or reports false
// for anything computed at run time. Only literals fold — a call is not a
// constant, so `pickPair("accent")` is left alone rather than reported for the
// string inside it. Concatenation folds too, which is what closes the
// `"#" + "3FB950"` spelling: the hex rule below reads whole string literals, so
// it sees two halves and neither is a colour.
func colourConst(e ast.Expr) (string, bool) {
	switch e := e.(type) {
	case *ast.ParenExpr:
		return colourConst(e.X)
	case *ast.BasicLit:
		switch e.Kind {
		case gotoken.STRING:
			s, err := strconv.Unquote(e.Value)
			if err != nil {
				return e.Value, true
			}
			return s, true
		case gotoken.INT:
			return e.Value, true
		}
	case *ast.BinaryExpr:
		if e.Op != gotoken.ADD {
			return "", false
		}
		l, lok := colourConst(e.X)
		r, rok := colourConst(e.Y)
		if !lok || !rok {
			return "", false
		}
		return l + r, true
	}
	return "", false
}

// fieldName is the field being assigned or keyed, if that is what this is.
func fieldName(e ast.Expr) string {
	switch e := e.(type) {
	case *ast.SelectorExpr:
		return e.Sel.Name
	case *ast.Ident:
		return e.Name
	}
	return ""
}

func hasSGRColour(s string) bool {
	for _, m := range sgrSeq.FindAllStringSubmatch(s, -1) {
		for _, p := range strings.Split(m[1], ";") {
			if sgrParam.MatchString(p) {
				return true
			}
		}
	}
	return false
}

// ── the sweep ──────────────────────────────────────────────────────────────

// goSources returns every non-test .go file in the module, keyed by its
// module-relative path. Tests are out: nothing in one is painted, and a test
// that pins what a token resolves to has to name the value it expects.
func goSources(t *testing.T) map[string]string {
	t.Helper()
	out := map[string]string{}
	err := filepath.WalkDir(moduleRoot, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			// filepath.Base("../../..") is "..", so the root has to be let
			// through explicitly or the dotfile skip below swallows the module.
			if path == moduleRoot {
				return nil
			}
			if name := d.Name(); name == "testdata" || strings.HasPrefix(name, ".") {
				return fs.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(moduleRoot, path)
		if err != nil {
			return err
		}
		out[filepath.ToSlash(rel)] = string(src)
		return nil
	})
	if err != nil {
		t.Fatalf("walking %s: %v", moduleRoot, err)
	}
	return out
}

// declName is the name an exemption would have to use to excuse this
// declaration, or "" for one that cannot be named (an unnamed import, say).
func declName(d ast.Decl) string {
	switch d := d.(type) {
	case *ast.FuncDecl:
		return d.Name.Name
	case *ast.GenDecl:
		return "" // handled per-spec, so a var block excuses one entry at a time
	}
	return ""
}

// specNames is every name a spec declares. All of them, not the first: an
// entry naming `a` must not quietly excuse `b` in `var a, b = …, …`, which is
// an allowlist widening the staleness check cannot see.
func specNames(s ast.Spec) []string {
	vs, ok := s.(*ast.ValueSpec)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(vs.Names))
	for _, n := range vs.Names {
		out = append(out, n.Name)
	}
	return out
}

func excused(list []exemption, file, decl string) bool {
	for _, e := range list {
		if e.file == file && e.decl == decl {
			return true
		}
	}
	return false
}

// excusedAll excuses a spec only when EVERY name it declares is named in the
// allowlist. An empty list is not an excuse.
func excusedAll(list []exemption, file string, decls []string) bool {
	if len(decls) == 0 {
		return false
	}
	for _, d := range decls {
		if !excused(list, file, d) {
			return false
		}
	}
	return true
}

func namesContain(names []string, want string) bool {
	for _, n := range names {
		if n == want {
			return true
		}
	}
	return false
}

// hit is one colour that did not come from a role.
type hit struct {
	file string
	line int
	msg  string
}

func (h hit) String() string { return fmt.Sprintf("%s:%d  %s", h.file, h.line, h.msg) }

// findings walks one file and reports every colour that did not come from a
// role, skipping the declarations `list` excuses. modeReach selects the second
// check (reading .Light/.Dark off a role) instead of the literal sweep. The
// allowlist is passed in rather than read from the package, so the staleness
// check can re-run the same sweep with the exemptions switched off.
func findings(t *testing.T, file, src string, modeReach bool, list []exemption, declaredFields map[string]bool) []hit {
	t.Helper()
	fset := gotoken.NewFileSet()
	f, err := parser.ParseFile(fset, file, src, 0)
	if err != nil {
		t.Fatalf("cannot parse %s: %v", file, err)
	}

	var found []hit

	report := func(pos gotoken.Pos, what string) {
		found = append(found, hit{file: file, line: fset.Position(pos).Line, msg: what})
	}

	// How THIS file spells each colour package, alias included.
	colourPkgs := colourPkgsOf(t, file, f)
	if !modeReach {
		for _, im := range f.Imports {
			path, err := strconv.Unquote(im.Path.Value)
			if err != nil || colourPkg(path) == "" || im.Name == nil || im.Name.Name != "." {
				continue
			}
			report(im.Pos(), "dot-imports "+path+", so its colour constructors are callable unqualified")
		}
	}

	// Colour-typed field names this file declares, on top of whatever the
	// caller found module-wide, so a synthetic source with no module behind it
	// still gets the type-directed rule.
	fields := map[string]bool{}
	for k := range declaredFields {
		fields[k] = true
	}
	for k := range colourFieldsOf(colourPkgs, f) {
		fields[k] = true
	}

	// A concatenation is read as one constant, so the halves must not also be
	// reported as two literals that happen to say nothing on their own.
	folded := map[ast.Node]bool{}

	inspect := func(n ast.Node) {
		ast.Inspect(n, func(n ast.Node) bool {
			if modeReach {
				sel, ok := n.(*ast.SelectorExpr)
				if ok && (sel.Sel.Name == "Light" || sel.Sel.Name == "Dark") {
					report(sel.Pos(), "reads ."+sel.Sel.Name+" off a colour instead of letting the role adapt")
				}
				return true
			}
			switch n := n.(type) {
			case *ast.BasicLit:
				if n.Kind != gotoken.STRING || folded[n] {
					return true
				}
				v, err := strconv.Unquote(n.Value)
				if err != nil {
					v = n.Value
				}
				if m := hexColour.FindString(v); m != "" {
					report(n.Pos(), "hex colour literal "+m)
				} else if hasSGRColour(v) {
					report(n.Pos(), "hand-written SGR colour escape")
				}
			case *ast.BinaryExpr:
				if folded[n] {
					return true
				}
				s, ok := colourConst(n)
				if !ok {
					return true
				}
				var msg string
				if m := hexColour.FindString(s); m != "" {
					msg = "hex colour literal " + m + ", spelled as a concatenation"
				} else if hasSGRColour(s) {
					msg = "hand-written SGR colour escape, spelled as a concatenation"
				}
				if msg == "" {
					return true
				}
				report(n.Pos(), msg)
				ast.Inspect(n, func(k ast.Node) bool { folded[k] = true; return true })
			case *ast.CallExpr:
				if pkg, name, ok := qualified(n.Fun); ok && colourCtor[colourPkgs[pkg]][name] {
					report(n.Pos(), "builds a colour with "+pkg+"."+name)
				}
			case *ast.CompositeLit:
				if pkg, name, ok := qualified(n.Type); ok && colourCtor[colourPkgs[pkg]][name] {
					report(n.Pos(), "builds a colour with "+pkg+"."+name)
				}
				reportColourSlots(report, colourPkgs, n)
			case *ast.ValueSpec:
				// `var c lipgloss.Color = "81"` — the declared type is the
				// only thing that says this string is a colour.
				if !isColourTypeExpr(colourPkgs, n.Type) {
					return true
				}
				for _, v := range n.Values {
					reportColourConst(report, "declares a colour", v)
				}
			case *ast.AssignStmt:
				for i, lhs := range n.Lhs {
					if isColourField(fieldName(lhs), fields) && i < len(n.Rhs) {
						reportPlainColour(report, fieldName(lhs), n.Rhs[i])
					}
				}
			case *ast.KeyValueExpr:
				if isColourField(fieldName(n.Key), fields) {
					reportPlainColour(report, fieldName(n.Key), n.Value)
				}
			}
			return true
		})
	}

	for _, d := range f.Decls {
		if gd, ok := d.(*ast.GenDecl); ok {
			for _, spec := range gd.Specs {
				if excusedAll(list, file, specNames(spec)) {
					continue
				}
				inspect(spec)
			}
			continue
		}
		if excused(list, file, declName(d)) {
			continue
		}
		inspect(d)
	}
	return found
}

// colourPkgsOf maps how THIS file spells each colour package to the key
// colourCtor and colourType are indexed by. Dot- and blank-imports are left
// out: neither gives a name anything can be qualified with.
func colourPkgsOf(t *testing.T, file string, f *ast.File) map[string]string {
	t.Helper()
	out := map[string]string{}
	for _, im := range f.Imports {
		path, err := strconv.Unquote(im.Path.Value)
		if err != nil {
			t.Fatalf("%s: unreadable import path %s: %v", file, im.Path.Value, err)
		}
		key := colourPkg(path)
		if key == "" {
			continue
		}
		name := key // unaliased, so the package name is the key
		if im.Name != nil {
			name = im.Name.Name
		}
		if name == "_" || name == "." {
			continue
		}
		out[name] = key
	}
	return out
}

// colourFieldsOf returns every struct field name in f whose declared type is a
// colour. Name-keyed rather than type-keyed on purpose: resolving which struct
// a composite literal means needs the whole package, and a guard that
// over-reports is a five-second fix while one that under-reports ships the
// colour.
func colourFieldsOf(pkgs map[string]string, f *ast.File) map[string]bool {
	out := map[string]bool{}
	ast.Inspect(f, func(n ast.Node) bool {
		st, ok := n.(*ast.StructType)
		if !ok || st.Fields == nil {
			return true
		}
		for _, fld := range st.Fields.List {
			if !isColourTypeExpr(pkgs, fld.Type) {
				continue
			}
			for _, nm := range fld.Names {
				out[nm.Name] = true
			}
		}
		return true
	})
	return out
}

// reportColourSlots flags a constant written into a container whose ELEMENT
// type is a colour — `map[string]lipgloss.Color{"heading": "81"}`, where the
// key says nothing and the value is a bare ANSI index.
func reportColourSlots(report func(gotoken.Pos, string), pkgs map[string]string, cl *ast.CompositeLit) {
	var elem ast.Expr
	switch t := cl.Type.(type) {
	case *ast.MapType:
		elem = t.Value
	case *ast.ArrayType:
		elem = t.Elt
	default:
		return
	}
	if !isColourTypeExpr(pkgs, elem) {
		return
	}
	for _, el := range cl.Elts {
		v := el
		if kv, ok := el.(*ast.KeyValueExpr); ok {
			v = kv.Value
		}
		reportColourConst(report, "fills a colour slot", v)
	}
}

// reportColourConst flags a literal that lands somewhere whose TYPE is a
// colour. Where the hex rule names the value it found, this one names the
// slot: "81" is a colour only because of where it is written.
func reportColourConst(report func(gotoken.Pos, string), where string, v ast.Expr) {
	s, ok := colourConst(v)
	if !ok || s == "" {
		return
	}
	report(v.Pos(), where+" with the literal "+strconv.Quote(s))
}

// reportPlainColour flags a colour written as a bare string into a colour
// field. Hex is left to the sweep's own rule, which names the value; this one
// exists for the spellings that rule cannot see — an ANSI-256 index, a colour
// name, anything a role should have carried.
func reportPlainColour(report func(gotoken.Pos, string), field string, v ast.Expr) {
	ast.Inspect(v, func(n ast.Node) bool {
		lit, ok := n.(*ast.BasicLit)
		if !ok || lit.Kind != gotoken.STRING {
			return true
		}
		s, err := strconv.Unquote(lit.Value)
		if err != nil || s == "" || hexColour.MatchString(s) {
			return true
		}
		report(lit.Pos(), "writes "+field+" as the literal "+strconv.Quote(s))
		return true
	})
}

// qualified splits `pkg.Name` out of an expression, if that is what it is.
func qualified(e ast.Expr) (pkg, name string, ok bool) {
	sel, ok := e.(*ast.SelectorExpr)
	if !ok {
		return "", "", false
	}
	id, ok := sel.X.(*ast.Ident)
	if !ok {
		return "", "", false
	}
	return id.Name, sel.Sel.Name, true
}

// ── the checks ─────────────────────────────────────────────────────────────

// A silent walk failure would make every assertion below vacuously pass, which
// is the one way this guard could be worse than not having it: green CI and no
// coverage. Probe for structure, not for any one line — a file can legitimately
// be deleted, and this failing for that reason would send the reader hunting a
// walk bug that is not there.
func TestTheSweepActuallyFoundTheSource(t *testing.T) {
	src := goSources(t)
	if len(src) < 100 {
		t.Fatalf("the walk found %d Go files; the module has well over a hundred — the sweep is not reaching the tree", len(src))
	}
	if _, ok := src[themeFile]; !ok {
		t.Fatalf("%s was not read, so the one exempt file is not even in the sweep", themeFile)
	}
	if !hexColour.MatchString(src[themeFile]) {
		t.Errorf("%s holds no hex colour — the palette moved, and this file is exempting the wrong place", themeFile)
	}
	for _, want := range []string{
		"internal/ui/components/markdown_style.go",
		"internal/control/svg.go",
		"cmd/gaia/main.go",
	} {
		if _, ok := src[want]; !ok {
			t.Errorf("%s is missing from the sweep", want)
		}
	}
	for path := range src {
		if strings.HasSuffix(path, "_test.go") {
			t.Errorf("%s is a test file and should not be swept", path)
		}
	}
}

func TestColourReachesTheTerminalThroughARoleNeverALiteral(t *testing.T) {
	for _, o := range sweep(t, false, allowed) {
		t.Errorf("%s\n\tadd a role to %s and use it, or add the declaration to `allowed` with a reason",
			o, themeFile)
	}
}

// The alias rule has to be proved on synthetic source: nothing in tui/ aliases
// a colour package today, so the sweep over the real tree cannot show it bites.
func TestHowAFileSpellsAColourPackageDoesNotChangeWhatIsPoliced(t *testing.T) {
	for _, tc := range []struct {
		name string
		src  string
		want bool
	}{
		{"alias, and no string for the hex sweep to read", `package p
import lg "github.com/charmbracelet/lipgloss"
var x = lg.ANSIColor(81)
`, true},
		{"alias on a composite literal", `package p
import tv "github.com/muesli/termenv"
var x = tv.RGBColor("")
`, true},
		{"alias on a versioned module path", `package p
import lg "github.com/charmbracelet/lipgloss/v2"
var x = lg.ANSIColor(81)
`, true},
		{"the directory carries go-, the package does not", `package p
import cf "github.com/lucasb-eyer/go-colorful"
var x = cf.Color{}
`, true},
		{"dot import puts the constructors in scope unqualified", `package p
import . "github.com/charmbracelet/lipgloss"
var x = ANSIColor(81)
`, true},
		{"an unrelated package that happens to be spelled lipgloss", `package p
import lipgloss "example.com/not/a/palette"
var x = lipgloss.Color(81)
`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := findings(t, tc.name+".go", tc.src, false, nil, nil)
			if (len(got) > 0) != tc.want {
				t.Errorf("want a finding: %v; got %v", tc.want, got)
			}
		})
	}
}

// A colour is a colour however it is spelled. Every case below was written
// against the tree, compiled, and shipped a hardcoded colour past this sweep
// with green CI — the sweep read the shape of the expression (a call, a
// composite literal, a field named …Color) rather than the type of the slot the
// value lands in, and none of these has that shape. The last three are the
// controls: a guard that fires on everything is no guard.
func TestAColourIsCaughtHoweverItIsSpelled(t *testing.T) {
	for _, tc := range []struct {
		name string
		src  string
		want bool
	}{
		{"a plain var, because lipgloss.Color is a string type", `package p
import "github.com/charmbracelet/lipgloss"
var x lipgloss.Color = "81"
`, true},
		{"a map whose KEY says nothing and whose value type says everything", `package p
import "github.com/charmbracelet/lipgloss"
var x = map[string]lipgloss.Color{"heading": "81"}
`, true},
		{"a struct field named for its job, not for its type", `package p
import "github.com/charmbracelet/lipgloss"
type pal struct{ fg lipgloss.Color }
var x = pal{fg: "46"}
`, true},
		{"a hex split across a concatenation", `package p
import "github.com/charmbracelet/lipgloss"
var x lipgloss.Color = "#" + "3FB950"
`, true},
		{"a slice of colours", `package p
import "github.com/charmbracelet/lipgloss"
var x = []lipgloss.Color{"81", "46"}
`, true},
		{"a role passed through, which is the whole point of the package", `package p
import "github.com/amd/gaia/tui/internal/ui/theme"
var x = theme.Accent
`, false},
		{"a colour-typed var built at run time", `package p
import "github.com/charmbracelet/lipgloss"
func pick(name string) lipgloss.Color
var x lipgloss.Color = pick("accent")
`, false},
		{"a concatenation that is not a colour", `package p
var msg = "see issue " + "in the tracker"
`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := findings(t, tc.name+".go", tc.src, false, nil, nil)
			if (len(got) > 0) != tc.want {
				t.Errorf("want a finding: %v; got %v", tc.want, got)
			}
		})
	}
}

func TestOnlyOnePlaceFlattensARoleToOneMode(t *testing.T) {
	for _, o := range sweep(t, true, modeReachAllowed) {
		t.Errorf("%s\n\tpass the whole AdaptiveColor, or add the declaration to `modeReachAllowed` with a reason", o)
	}
}

// adaptiveColourType is the subset of colourType that carries BOTH modes.
// Everything the package measures — the contrast floors, the hue families, the
// mode-parity sweep — works by walking All() and checking a Light against a
// Dark, so a value with only one side in it is a value no guard can see.
var adaptiveColourType = map[string]map[string]bool{
	"lipgloss": {"AdaptiveColor": true, "CompleteAdaptiveColor": true},
}

// TestThemeExportsRolesNotColours closes the one door the sweep above leaves
// open. theme.go is exempt from both sweeps — it is the one file allowed to
// write hex and to name a mode — and that exemption is load-bearing, so it
// stays. But it means anything theme.go hands out is unexamined by definition,
// and an exported `func Pick(c lipgloss.AdaptiveColor) lipgloss.Color` would
// launder the exemption to every caller: the component that calls it has a
// single-mode colour with no literal, no ctor and no `.Light` for the sweeps to
// find, and the contrast tests never learn the value exists.
//
// So: theme.go exports ROLES. Returning an AdaptiveColor is fine, a container
// of them is fine (All() does exactly that), and a Style is fine because a
// style still resolves per-mode at render time. A bare colour is not.
//
// Unexported helpers are left alone on purpose — flattening inside a function
// body is how Init() has to work, and a component cannot reach it. The bypass
// is specifically a reachable exported escape hatch. theme.go is also the whole
// non-test package today, so scoping to the file and scoping to the package are
// the same scope.
func TestThemeExportsRolesNotColours(t *testing.T) {
	src := goSources(t)
	body, ok := src[themeFile]
	if !ok {
		t.Fatalf("%s not found in the module sources", themeFile)
	}
	for _, o := range flatExports(t, themeFile, body) {
		t.Errorf("%s\n\tReturn lipgloss.AdaptiveColor, or move the flattening into the component.", o)
	}
}

// flatExports returns one message per exported function or method in the file
// whose results can hand a caller a single-mode colour.
func flatExports(t *testing.T, file, src string) []string {
	t.Helper()
	fset := gotoken.NewFileSet()
	f, err := parser.ParseFile(fset, file, src, 0)
	if err != nil {
		t.Fatalf("cannot parse %s: %v", file, err)
	}
	pkgs := colourPkgsOf(t, file, f)

	var out []string
	for _, d := range f.Decls {
		fn, ok := d.(*ast.FuncDecl)
		if !ok || !fn.Name.IsExported() || fn.Type.Results == nil {
			continue
		}
		for _, res := range fn.Type.Results.List {
			bare := bareColourIn(pkgs, res.Type)
			if bare == "" {
				continue
			}
			out = append(out, fmt.Sprintf("%s:%d: theme.%s returns %s — %s may only export "+
				"adaptive roles, because the contrast and parity guards measure "+
				"AdaptiveColor pairs.",
				file, fset.Position(fn.Pos()).Line, fnName(fn), bare, filepath.Base(file)))
		}
	}
	sort.Strings(out)
	return out
}

// The real theme.go exports three functions today, so the rule has almost no
// surface to prove itself on. These are the shapes it has to get right, each
// one a mutation that was run against the live file and killed — kept here so
// the next person to touch bareColourIn finds out immediately, rather than the
// next time somebody adds an escape hatch.
func TestARoleIsNotAColourHoweverItIsWrapped(t *testing.T) {
	const head = "package theme\n\nimport lg \"github.com/charmbracelet/lipgloss\"\n\n"
	for _, tc := range []struct {
		name string
		src  string
		want bool
	}{
		{"the bypass itself", `func Pick(c lg.AdaptiveColor) lg.Color { return lg.Color(c.Dark) }`, true},
		{"as a method, so the receiver carries it", `type Palette struct{ Tint lg.AdaptiveColor }
func (p Palette) Flat() lg.Color { return lg.Color(p.Tint.Dark) }`, true},
		{"as a pointer method", `type Palette struct{ Tint lg.AdaptiveColor }
func (p *Palette) Flat() lg.Color { return lg.Color(p.Tint.Dark) }`, true},
		{"hidden in a map value", `func Flat() map[string]lg.Color { return nil }`, true},
		{"hidden in a slice", `func Ramp() []lg.Color { return nil }`, true},
		{"the interface, which erases the pair from the type", `func Any() lg.TerminalColor { return nil }`, true},
		{"alongside a result that is fine", `func Named() (string, lg.Color) { return "", "" }`, true},

		{"a role", `func Brand() lg.AdaptiveColor { return lg.AdaptiveColor{} }`, false},
		{"a container of roles, which is what All() is", `func All() map[string]lg.AdaptiveColor { return nil }`, false},
		{"the complete form, still both modes", `func C() lg.CompleteAdaptiveColor { return lg.CompleteAdaptiveColor{} }`, false},
		{"a style, which resolves per-mode at render", `func Emphasis() lg.Style { return lg.NewStyle() }`, false},
		{"unexported, so no component can reach it", `func pick(c lg.AdaptiveColor) lg.Color { return lg.Color(c.Dark) }`, false},
		{"no results at all", `func Init() {}`, false},
		{"a result that is not a colour", `func IsDark() bool { return true }`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := flatExports(t, "synthetic.go", head+tc.src+"\n")
			if (len(got) > 0) != tc.want {
				t.Errorf("flagged=%v, want %v\n%s", len(got) > 0, tc.want, strings.Join(got, "\n"))
			}
		})
	}
}

// bareColourIn returns the first single-mode colour type reachable in e, or ""
// when there is none. It recurses through containers because `map[string]
// lipgloss.Color` hands out colours just as surely as returning one does, and
// stops at an adaptive type because a container of roles is what All() is.
func bareColourIn(pkgs map[string]string, e ast.Expr) string {
	switch e := e.(type) {
	case *ast.ParenExpr:
		return bareColourIn(pkgs, e.X)
	case *ast.StarExpr:
		return bareColourIn(pkgs, e.X)
	case *ast.ArrayType:
		return bareColourIn(pkgs, e.Elt)
	case *ast.ChanType:
		return bareColourIn(pkgs, e.Value)
	case *ast.MapType:
		if k := bareColourIn(pkgs, e.Key); k != "" {
			return k
		}
		return bareColourIn(pkgs, e.Value)
	case *ast.SelectorExpr:
		id, ok := e.X.(*ast.Ident)
		if !ok {
			return ""
		}
		pkg := pkgs[id.Name]
		if adaptiveColourType[pkg][e.Sel.Name] {
			return "" // a role: both modes present, every guard can see it
		}
		if colourType[pkg][e.Sel.Name] {
			return id.Name + "." + e.Sel.Name
		}
	}
	return ""
}

func fnName(fn *ast.FuncDecl) string {
	if fn.Recv == nil || len(fn.Recv.List) == 0 {
		return fn.Name.Name
	}
	return recvTypeName(fn.Recv.List[0].Type) + "." + fn.Name.Name
}

func recvTypeName(e ast.Expr) string {
	switch e := e.(type) {
	case *ast.StarExpr:
		return recvTypeName(e.X)
	case *ast.Ident:
		return e.Name
	case *ast.IndexExpr: // a generic receiver, Palette[T]
		return recvTypeName(e.X)
	}
	return "?"
}

// sweep runs one check over the whole module, theme.go excepted.
func sweep(t *testing.T, modeReach bool, list []exemption) []string {
	t.Helper()
	src := goSources(t)
	fields := moduleColourFields(t, src)
	var offenders []string
	for path, body := range src {
		if path == themeFile {
			continue
		}
		for _, h := range findings(t, path, body, modeReach, list, fields) {
			offenders = append(offenders, h.String())
		}
	}
	sort.Strings(offenders)
	return offenders
}

// moduleColourFields unions the colour-typed field names of every file,
// theme.go included: the struct a literal fills does not have to be declared
// in the file that fills it.
func moduleColourFields(t *testing.T, src map[string]string) map[string]bool {
	t.Helper()
	out := map[string]bool{}
	for path, body := range src {
		fset := gotoken.NewFileSet()
		f, err := parser.ParseFile(fset, path, body, 0)
		if err != nil {
			t.Fatalf("cannot parse %s: %v", path, err)
		}
		for k := range colourFieldsOf(colourPkgsOf(t, path, f), f) {
			out[k] = true
		}
	}
	return out
}

// An exemption that no longer excuses anything is an exemption that will
// silently cover the NEXT thing to take that name. Both lists are checked the
// same way: the declaration still has to exist, and it still has to contain the
// thing it was excused for.
func TestEveryExemptionStillExcusesSomething(t *testing.T) {
	src := goSources(t)
	for _, set := range []struct {
		name      string
		list      []exemption
		modeReach bool
	}{
		{"allowed", allowed, false},
		{"modeReachAllowed", modeReachAllowed, true},
	} {
		for _, e := range set.list {
			body, ok := src[e.file]
			if !ok {
				t.Errorf("%s: %s no longer exists — drop the entry for %s", set.name, e.file, e.decl)
				continue
			}
			if !declaresName(t, e.file, body, e.decl) {
				t.Errorf("%s: %s has no top-level %s — drop or rename the entry", set.name, e.file, e.decl)
				continue
			}
			if got := findingsFor(t, e.file, body, e.decl, set.modeReach); len(got) == 0 {
				t.Errorf("%s: %s in %s no longer holds anything to excuse — drop the entry",
					set.name, e.decl, e.file)
			}
		}
	}
}

func TestEveryExemptionSaysWhy(t *testing.T) {
	for _, e := range append(append([]exemption{}, allowed...), modeReachAllowed...) {
		if len(e.why) < 40 {
			t.Errorf("%s %s: %q is not a reason a reader can act on", e.file, e.decl, e.why)
		}
	}
}

// declaresName reports whether the file has a top-level declaration by that
// name — a func, or a const/var spec.
func declaresName(t *testing.T, file, src, name string) bool {
	t.Helper()
	fset := gotoken.NewFileSet()
	f, err := parser.ParseFile(fset, file, src, 0)
	if err != nil {
		t.Fatalf("cannot parse %s: %v", file, err)
	}
	for _, d := range f.Decls {
		if declName(d) == name {
			return true
		}
		if gd, ok := d.(*ast.GenDecl); ok {
			for _, spec := range gd.Specs {
				if namesContain(specNames(spec), name) {
					return true
				}
			}
		}
	}
	return false
}

// findingsFor runs the sweep over ONE declaration with the allowlist switched
// off, which is how the staleness check asks "is there still anything here?".
func findingsFor(t *testing.T, file, src, decl string, modeReach bool) []hit {
	t.Helper()
	// The sweep reports a line, not a declaration, so narrow to the lines the
	// declaration actually spans.
	lo, hi := declLines(t, file, src, decl)
	var in []hit
	for _, h := range findings(t, file, src, modeReach, nil, nil) {
		if h.line >= lo && h.line <= hi {
			in = append(in, h)
		}
	}
	return in
}

func declLines(t *testing.T, file, src, decl string) (lo, hi int) {
	t.Helper()
	fset := gotoken.NewFileSet()
	f, err := parser.ParseFile(fset, file, src, 0)
	if err != nil {
		t.Fatalf("cannot parse %s: %v", file, err)
	}
	span := func(n ast.Node) (int, int) {
		return fset.Position(n.Pos()).Line, fset.Position(n.End()).Line
	}
	for _, d := range f.Decls {
		if declName(d) == decl {
			return span(d)
		}
		if gd, ok := d.(*ast.GenDecl); ok {
			for _, spec := range gd.Specs {
				if namesContain(specNames(spec), decl) {
					return span(spec)
				}
			}
		}
	}
	return 0, -1
}
