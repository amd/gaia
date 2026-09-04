// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"go/ast"
	"go/parser"
	"go/token"
	"strconv"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The panel used to be bounded to 20 body lines so the box plus its border
// and padding would fit 24 rows, the shortest terminal the TUI targets. That
// budget didn't survive contact with a growing feature set — see
// fitHelpLines and RenderHelpOverlay's scroll parameter, which let the panel
// hold as much as it needs and reach the rest with the keyboard. What still
// has to hold is per-line width: a line lipgloss soft-wraps adds a row nobody
// asked for and throws off the exact row count RenderHelpOverlay promises.
func TestHelpTextFitsItsBudget(t *testing.T) {
	const maxWidth = helpBoxMaxWidth - 4

	for name, text := range map[string]string{"chat": chatHelpText} {
		for i, line := range strings.Split(text, "\n") {
			if w := ansi.StringWidth(line); w > maxWidth {
				t.Errorf("%s help line %d is %d columns, over %d — it will soft-wrap and cost an extra row: %q",
					name, i+1, w, maxWidth, line)
			}
		}
	}
}

// chatModelKeys parses the real `switch msg.Type` inside handleKey out of
// tui/internal/ui/chat/model.go, rather than a hand-copied list — so this
// test is checking the bindings the chat view actually answers to. A case
// added there and forgotten here used to just not show up in the panel; now
// it fails the build of this test.
func chatModelKeys(t *testing.T) []string {
	t.Helper()
	fn := findChatModelFunc(t, "handleKey")
	var keys []string
	ast.Inspect(fn, func(n ast.Node) bool {
		sw, ok := n.(*ast.SwitchStmt)
		if !ok || !isMsgTypeSwitch(sw.Tag) {
			return true
		}
		for _, stmt := range sw.Body.List {
			cc, ok := stmt.(*ast.CaseClause)
			if !ok {
				continue
			}
			for _, expr := range cc.List {
				sel, ok := expr.(*ast.SelectorExpr)
				if !ok {
					continue
				}
				if id, ok := sel.X.(*ast.Ident); ok && id.Name == "tea" {
					keys = append(keys, sel.Sel.Name)
				}
			}
		}
		return false // that's the one switch that matters; don't recurse into it
	})
	if len(keys) == 0 {
		t.Fatal("found no tea.Key cases in handleKey's switch — the AST walk broke, not the source")
	}
	return keys
}

func isMsgTypeSwitch(tag ast.Expr) bool {
	sel, ok := tag.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	id, ok := sel.X.(*ast.Ident)
	return ok && id.Name == "msg" && sel.Sel.Name == "Type"
}

// chatModelCommands parses the string literals out of submit's `switch
// query` the same way, plus normalizes the four /bypass variants (each is a
// distinct case value) down to the one command they all belong to.
func chatModelCommands(t *testing.T) []string {
	t.Helper()
	fn := findChatModelFunc(t, "submit")
	var cmds []string
	ast.Inspect(fn, func(n ast.Node) bool {
		sw, ok := n.(*ast.SwitchStmt)
		if !ok {
			return true
		}
		id, ok := sw.Tag.(*ast.Ident)
		if !ok || id.Name != "query" {
			return true
		}
		for _, stmt := range sw.Body.List {
			cc, ok := stmt.(*ast.CaseClause)
			if !ok {
				continue
			}
			for _, expr := range cc.List {
				lit, ok := expr.(*ast.BasicLit)
				if !ok || lit.Kind != token.STRING {
					continue
				}
				v, err := strconv.Unquote(lit.Value)
				if err != nil {
					t.Fatalf("could not unquote command literal %s: %v", lit.Value, err)
				}
				cmds = append(cmds, v)
			}
		}
		return false
	})
	if len(cmds) == 0 {
		t.Fatal("found no string cases in submit's command switch — the AST walk broke, not the source")
	}
	return cmds
}

func findChatModelFunc(t *testing.T, name string) *ast.FuncDecl {
	t.Helper()
	fset := token.NewFileSet()
	// go test's working directory is this package's own directory.
	file, err := parser.ParseFile(fset, "../chat/model.go", nil, 0)
	if err != nil {
		t.Fatalf("could not parse chat/model.go: %v", err)
	}
	for _, decl := range file.Decls {
		if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == name {
			return fn
		}
	}
	t.Fatalf("chat/model.go no longer has a %s function", name)
	return nil
}

// Everything the chat view answers to has to be in here. The panel claimed
// only Enter / Esc / Ctrl+C / PgUp-PgDn long after the transcript gained line
// scrolling, half-page scrolling, jump-to-end, the mouse wheel, and a queue
// for typing while the agent is mid-turn — and Ctrl+Y/Ctrl+B went missing
// from it entirely for a while despite this test's own name.
func TestChatHelpNamesEveryChatBinding(t *testing.T) {
	// keyBindingText maps a tea.Key constant handleKey's switch matches to
	// the substring the panel must contain for it. A key handled in code but
	// missing here fails loudly instead of silently shipping undocumented —
	// add the row here AND the text in chatHelpText when handleKey grows one.
	keyBindingText := map[string]string{
		"KeyCtrlC":  "Ctrl+C",
		"KeyEsc":    "Esc",
		"KeyCtrlJ":  "Ctrl+J",
		"KeyEnter":  "Enter",
		"KeyCtrlT":  "Ctrl+T",
		"KeyCtrlY":  "Ctrl+Y",
		"KeyCtrlB":  "Ctrl+B",
		"KeyCtrlV":  "Ctrl+V",
		"KeyPgUp":   "PgUp",
		"KeyPgDown": "PgDn",
		"KeyUp":     "↑",
		"KeyDown":   "↓",
		"KeyHome":   "Home",
		"KeyEnd":    "End",
	}
	for _, key := range chatModelKeys(t) {
		want, ok := keyBindingText[key]
		if !ok {
			t.Errorf("handleKey now handles tea.%s but keyBindingText in this test does not know it — "+
				"document it in chatHelpText and add a row here", key)
			continue
		}
		if !strings.Contains(chatHelpText, want) {
			t.Errorf("chat help never mentions %q (tea.%s)", want, key)
		}
	}

	// commandText does the same for submit's local commands.
	commandText := map[string]string{
		"/help":    "/help",
		"/clear":   "/clear",
		"/memory":  "/memory",
		"/setup":   "/setup",
		"/bypass":  "/bypass",
		"/gateway": "/gateway",
	}
	for _, cmd := range chatModelCommands(t) {
		key := cmd
		if strings.HasPrefix(cmd, "/bypass") {
			key = "/bypass"
		}
		want, ok := commandText[key]
		if !ok {
			t.Errorf("submit now handles %q but commandText in this test does not know it — "+
				"document it in chatHelpText and add a row here", cmd)
			continue
		}
		if !strings.Contains(chatHelpText, want) {
			t.Errorf("chat help never mentions %q", want)
		}
	}

	// /model takes a free-form model id argument, so submit dispatches it via
	// isModelCommand before the switch above — it never appears as a case
	// literal there and has to be asserted directly.
	if !strings.Contains(chatHelpText, "/model") {
		t.Error("chat help never mentions /model")
	}

	// None of these are switch cases either: type-ahead queueing, the
	// give-up-waiting second Esc, and the mouse wheel are prose, not a case.
	for _, want := range []string{"queues", "Give up", "Mouse wheel"} {
		if !strings.Contains(chatHelpText, want) {
			t.Errorf("chat help never mentions %q", want)
		}
	}
}

// The overlay is composited over the live view, so it must return exactly the
// screen it was handed — no taller, no wider, whatever the window size or
// scroll position.
func TestHelpOverlayNeverOutgrowsTheWindow(t *testing.T) {
	sizes := []struct{ w, h int }{
		{100, 40}, // roomy
		{80, 24},  // the size the budget is written for
		{80, 14},  // one row under the budget: padding has to go
		{80, 10},  // shorter than the body: lines have to go
		{80, 6},
		{40, 24}, // narrow enough that lines need truncating
		{20, 12},
	}
	background := strings.TrimSuffix(strings.Repeat("background\n", 40), "\n")

	assertExact := func(t *testing.T, ctx HelpContext, w, h, scroll int) {
		t.Helper()
		out := RenderHelpOverlay(ctx, background, w, h, scroll)
		rows := strings.Split(out, "\n")
		if len(rows) != h {
			t.Errorf("ctx %d at %dx%d scroll=%d rendered %d rows, want %d", ctx, w, h, scroll, len(rows), h)
			return
		}
		for i, row := range rows {
			if wd := ansi.StringWidth(row); wd != w {
				t.Errorf("ctx %d at %dx%d scroll=%d: row %d is %d columns, want %d", ctx, w, h, scroll, i, wd, w)
				break
			}
		}
	}

	for _, ctx := range []HelpContext{HelpContextChat} {
		for _, s := range sizes {
			assertExact(t, ctx, s.w, s.h, 0)
		}
	}

	// Scrolling is a new axis of variation on top of size — walk it across an
	// overflowing size (including out-of-range offsets RenderHelpOverlay has
	// to clamp itself, same as HelpMaxScroll clamps for the caller) to prove
	// the exact-row-and-column invariant holds at every position, not just
	// the top.
	for _, ctx := range []HelpContext{HelpContextChat} {
		const w, h = 80, 10
		maxScroll := HelpMaxScroll(ctx, w, h)
		for scroll := -2; scroll <= maxScroll+2; scroll++ {
			assertExact(t, ctx, w, h, scroll)
		}
	}
}

// Scrolling has to actually reach the end of the content, and the indicator
// has to tell the truth about which direction still has more: "below" at the
// top, "above" at the bottom, both in between, and nothing once the content
// simply fits.
func TestHelpScrollReachesTheLastLineAndTheIndicatorAgrees(t *testing.T) {
	const w, h = 80, 10 // short enough that chatHelpText overflows it
	maxScroll := HelpMaxScroll(HelpContextChat, w, h)
	if maxScroll == 0 {
		t.Fatal("test setup: chatHelpText needs to overflow an 80x10 panel for this test to mean anything")
	}

	lastLine := strings.Split(chatHelpText, "\n")[len(strings.Split(chatHelpText, "\n"))-1]
	trimmed := strings.TrimSpace(lastLine)

	top := ansi.Strip(RenderHelpOverlay(HelpContextChat, "", w, h, 0))
	if !strings.Contains(top, "more below") {
		t.Errorf("scrolled to the top, the panel doesn't say there's more below:\n%s", top)
	}
	if strings.Contains(top, "more above") {
		t.Errorf("scrolled to the top, the panel wrongly claims there's more above:\n%s", top)
	}
	if trimmed != "" && strings.Contains(top, trimmed) {
		t.Errorf("the last line is visible without scrolling at all — test no longer exercises overflow:\n%s", top)
	}

	bottom := ansi.Strip(RenderHelpOverlay(HelpContextChat, "", w, h, maxScroll))
	if !strings.Contains(bottom, "more above") {
		t.Errorf("scrolled to the bottom, the panel doesn't say there's more above:\n%s", bottom)
	}
	if strings.Contains(bottom, "more below") {
		t.Errorf("scrolled to the bottom, the panel wrongly claims there's more below:\n%s", bottom)
	}
	if trimmed != "" && !strings.Contains(bottom, trimmed) {
		t.Errorf("scrolling to HelpMaxScroll never reached the last line %q:\n%s", trimmed, bottom)
	}

	if maxScroll >= 2 {
		mid := ansi.Strip(RenderHelpOverlay(HelpContextChat, "", w, h, maxScroll/2))
		if !strings.Contains(mid, "more above") || !strings.Contains(mid, "more below") {
			t.Errorf("mid-scroll, the panel should point both ways:\n%s", mid)
		}
	}

	// Content that fits needs no indicator and no scrolling at all.
	full := ansi.Strip(RenderHelpOverlay(HelpContextChat, "", 100, 40, 0))
	if strings.Contains(full, "more above") || strings.Contains(full, "more below") {
		t.Errorf("a panel with room for everything still shows a scroll indicator:\n%s", full)
	}
	if HelpMaxScroll(HelpContextChat, 100, 40) != 0 {
		t.Error("HelpMaxScroll reports scroll room in a panel with none")
	}
}

// A window with no room for a panel gets the view it already had, not a box
// with one column of border in it.
func TestATinyWindowKeepsTheViewItHas(t *testing.T) {
	const background = "the chat view"
	if got := RenderHelpOverlay(HelpContextChat, background, 8, 20, 0); got != background {
		t.Errorf("a 8-column window rendered a panel anyway: %q", got)
	}
	if got := RenderHelpOverlay(HelpContextChat, background, 80, 2, 0); got != background {
		t.Errorf("a 2-row window rendered a panel anyway: %q", got)
	}
}
