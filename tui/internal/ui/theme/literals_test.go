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

// A field that takes a colour. Not every colour sink is a lipgloss
// constructor: glamour's style config holds them as plain *string, so
// `Color: strPtr("81")` — an ANSI-256 index, exactly what the markdown
// renderer used to paint headings with — is a colour no other rule here sees.
// Anything ending in Color is one: Color, BackgroundColor, CenterColor.
func isColourField(name string) bool { return strings.HasSuffix(name, "Color") }

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

func specName(s ast.Spec) string {
	if vs, ok := s.(*ast.ValueSpec); ok && len(vs.Names) > 0 {
		return vs.Names[0].Name
	}
	return ""
}

func excused(list []exemption, file, decl string) bool {
	for _, e := range list {
		if e.file == file && e.decl == decl {
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
func findings(t *testing.T, file, src string, modeReach bool, list []exemption) []hit {
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
				if n.Kind != gotoken.STRING {
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
			case *ast.CallExpr:
				if pkg, name, ok := qualified(n.Fun); ok && colourCtor[pkg][name] {
					report(n.Pos(), "builds a colour with "+pkg+"."+name)
				}
			case *ast.CompositeLit:
				if pkg, name, ok := qualified(n.Type); ok && colourCtor[pkg][name] {
					report(n.Pos(), "builds a colour with "+pkg+"."+name)
				}
			case *ast.AssignStmt:
				for i, lhs := range n.Lhs {
					if isColourField(fieldName(lhs)) && i < len(n.Rhs) {
						reportPlainColour(report, fieldName(lhs), n.Rhs[i])
					}
				}
			case *ast.KeyValueExpr:
				if isColourField(fieldName(n.Key)) {
					reportPlainColour(report, fieldName(n.Key), n.Value)
				}
			}
			return true
		})
	}

	for _, d := range f.Decls {
		if gd, ok := d.(*ast.GenDecl); ok {
			for _, spec := range gd.Specs {
				if excused(list, file, specName(spec)) {
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

func TestOnlyOnePlaceFlattensARoleToOneMode(t *testing.T) {
	for _, o := range sweep(t, true, modeReachAllowed) {
		t.Errorf("%s\n\tpass the whole AdaptiveColor, or add the declaration to `modeReachAllowed` with a reason", o)
	}
}

// sweep runs one check over the whole module, theme.go excepted.
func sweep(t *testing.T, modeReach bool, list []exemption) []string {
	t.Helper()
	var offenders []string
	for path, src := range goSources(t) {
		if path == themeFile {
			continue
		}
		for _, h := range findings(t, path, src, modeReach, list) {
			offenders = append(offenders, h.String())
		}
	}
	sort.Strings(offenders)
	return offenders
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
				if specName(spec) == name {
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
	for _, h := range findings(t, file, src, modeReach, nil) {
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
				if specName(spec) == decl {
					return span(spec)
				}
			}
		}
	}
	return 0, -1
}
