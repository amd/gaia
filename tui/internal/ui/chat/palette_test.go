// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"go/ast"
	"go/parser"
	"go/token"
	"strconv"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// --- discoverability: "/" opens, mid-word doesn't ---------------------------

func TestSlashOnAnEmptyComposerOpensThePalette(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/")

	if !m.palette.open {
		t.Fatal("\"/\" as the first character of an empty composer must open the palette")
	}
	if got := len(m.paletteFiltered()); got != len(paletteCommands) {
		t.Errorf("an empty filter should offer every command, got %d of %d", got, len(paletteCommands))
	}
}

func TestSlashMidSentenceDoesNotOpenThePalette(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "meet me at 5/")

	if m.palette.open {
		t.Error("a \"/\" typed mid-sentence must not hijack the composer into a command palette")
	}
}

// Backspacing the leading "/" away has to close the palette same as never
// having opened it — the user is back to plain text either way.
func TestBackspacingTheSlashAwayClosesThePalette(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/mo")
	if !m.palette.open {
		t.Fatal("test setup: \"/mo\" should have opened the palette")
	}

	m, _ = press(t, m, tea.KeyBackspace)
	m, _ = press(t, m, tea.KeyBackspace)
	m, _ = press(t, m, tea.KeyBackspace)
	if m.input.Value() != "" {
		t.Fatalf("test setup: composer should be empty, got %q", m.input.Value())
	}
	if m.palette.open {
		t.Error("an empty composer must close the palette")
	}
}

// --- filtering ---------------------------------------------------------------

func TestPaletteFiltersAsYouType(t *testing.T) {
	m, _ := newTestModel(t)

	m = typeInto(t, m, "/m")
	names := paletteNames(m.paletteFiltered())
	if !containsAll(names, "/model", "/memory") || len(names) != 2 {
		t.Fatalf("\"/m\" should narrow to /model and /memory, got %v", names)
	}

	m = typeInto(t, m, "o")
	names = paletteNames(m.paletteFiltered())
	if len(names) != 1 || names[0] != "/model" {
		t.Fatalf("\"/mo\" should narrow to just /model, got %v", names)
	}
}

// A filter that matches nothing closes the palette rather than showing an
// empty box — there is nothing left to suggest.
func TestPaletteClosesWhenNothingMatches(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/zzz")

	if m.palette.open {
		t.Error("a filter with no matches must close the palette")
	}
}

// --- ↑/↓ navigation, with wraparound ------------------------------------------

func TestPaletteUpDownWrapsAtTheEnds(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/")
	n := len(m.paletteFiltered())
	if n < 2 {
		t.Fatalf("test setup: need at least two commands to test wraparound, got %d", n)
	}
	if m.palette.selected != 0 {
		t.Fatalf("test setup: expected selection to start at 0, got %d", m.palette.selected)
	}

	// Up from the top wraps to the last row.
	m, _ = press(t, m, tea.KeyUp)
	if m.palette.selected != n-1 {
		t.Errorf("Up from the top row should wrap to row %d, got %d", n-1, m.palette.selected)
	}

	// Down from there returns to the top.
	m, _ = press(t, m, tea.KeyDown)
	if m.palette.selected != 0 {
		t.Errorf("Down from the last row should wrap to row 0, got %d", m.palette.selected)
	}

	// Down all the way around lands back on 0.
	for i := 0; i < n; i++ {
		m, _ = press(t, m, tea.KeyDown)
	}
	if m.palette.selected != 0 {
		t.Errorf("stepping Down exactly %d times should return to row 0, got %d", n, m.palette.selected)
	}
}

// --- Enter runs the SELECTED command, not the literal text -------------------

// Mirrors TestSetupCommandIsNeverSentAsAQuery (setup_test.go): picking a row
// must dispatch through submit()'s local-command handling, never fall to
// sendQuery — which would post it as a chat bubble and ship it to the agent.
func TestPaletteEnterRunsTheSelectedCommandNotTheTypedText(t *testing.T) {
	m, _ := newTestModel(t)
	m = typeInto(t, m, "/h") // matches /help then /hub, in that order

	names := paletteNames(m.paletteFiltered())
	if len(names) != 2 || names[0] != "/help" || names[1] != "/hub" {
		t.Fatalf("test setup: expected [/help /hub], got %v", names)
	}

	// Move the selection down to /hub without ever typing more than "/h".
	m, _ = press(t, m, tea.KeyDown)
	if m.palette.selected != 1 {
		t.Fatalf("test setup: expected row 1 selected, got %d", m.palette.selected)
	}

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)

	if m.palette.open {
		t.Error("Enter must close the palette")
	}
	if m.input.Value() != "" {
		t.Errorf("Enter must clear the composer, got %q", m.input.Value())
	}
	for _, msg := range m.messages {
		if msg.Role == RoleUser {
			t.Errorf("the command must never be posted as a chat message, found: %q", msg.Content)
		}
	}
	// /hub on a model not launched from the hub (newTestModel) declines with
	// a status note rather than switching views — proof it ran /hub, the
	// SELECTED row, and not the literally-typed "/h" (which isn't a command
	// at all and would have gone to sendQuery instead).
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || !strings.Contains(last.Content, "Not launched from hub") {
		t.Errorf("expected the /hub decline note, got: %+v", last)
	}
}

// A selection made while a turn is running queues exactly like manually
// typing the command and pressing Enter would (see handleKey's own Enter
// case) — it runs once that turn is actually over, not immediately.
func TestPaletteEnterMidTurnQueuesLikeAnyOtherLine(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m = typeInto(t, m, "/c") // matches only /clear

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)

	if m.palette.open {
		t.Error("Enter must close the palette even mid-turn")
	}
	if len(m.messages) != 0 {
		t.Error("/clear must not run yet — the turn is still streaming")
	}
	if len(m.queued) != 1 || m.queued[0] != "/clear" {
		t.Fatalf("expected /clear queued behind the running turn, got %v", m.queued)
	}
}

// --- Esc closes without quitting or touching the turn -------------------------

func TestPaletteEscClosesWithoutQuittingOrCancelling(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.cancelFn = func() {} // present, so a real cancel path is reachable if wrongly triggered
	m = typeInto(t, m, "/mo")
	if !m.palette.open {
		t.Fatal("test setup: palette should be open")
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if m.palette.open {
		t.Error("Esc must close the palette")
	}
	// Not "no Cmd at all": the palette closing legitimately releases the
	// mouse it captured for its own clicks (mousecapture.go), and THAT is a
	// Cmd too. What must never happen is a quit or a cancel request — the
	// cancelPending/streaming checks below already cover the latter
	// directly, regardless of what the returned Cmd resolves to.
	if quits(cmd) {
		t.Error("Esc on an open palette must not quit")
	}
	if !m.streaming || m.cancelPending {
		t.Error("Esc on an open palette must not touch a running turn")
	}
	if m.input.Value() != "/mo" {
		t.Errorf("Esc must not clear the composer, got %q", m.input.Value())
	}
}

// --- anti-drift: every command submit() handles is offered here --------------

// submitCommandLiterals parses the real `switch query` inside submit (in this
// same package's model.go), the same way
// components/helpoverlay_test.go's chatModelCommands parses handleKey's
// switch — so this test is checking the commands submit ACTUALLY answers to,
// not a hand-copied list that can silently fall behind it.
func submitCommandLiterals(t *testing.T) []string {
	t.Helper()
	fset := token.NewFileSet()
	// go test's working directory is this package's own directory.
	file, err := parser.ParseFile(fset, "model.go", nil, 0)
	if err != nil {
		t.Fatalf("could not parse model.go: %v", err)
	}
	var fn *ast.FuncDecl
	for _, decl := range file.Decls {
		if f, ok := decl.(*ast.FuncDecl); ok && f.Name.Name == "submit" {
			fn = f
		}
	}
	if fn == nil {
		t.Fatal("chat/model.go no longer has a submit function")
	}

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
		return false // that's the one switch that matters; don't recurse into it
	})
	if len(cmds) == 0 {
		t.Fatal("found no string cases in submit's command switch — the AST walk broke, not the source")
	}
	return cmds
}

// TestPaletteListsEverySubmitCommand fails the moment submit() grows a
// command paletteCommands does not know about — the anti-drift test the task
// requires in place of deriving the list at runtime (a description belongs
// with each entry, which the switch's case labels don't carry).
func TestPaletteListsEverySubmitCommand(t *testing.T) {
	known := make(map[string]bool, len(paletteCommands))
	for _, c := range paletteCommands {
		known[c.Name] = true
	}

	seen := make(map[string]bool)
	for _, cmd := range submitCommandLiterals(t) {
		base := cmd
		if strings.HasPrefix(cmd, "/bypass") {
			// /bypass on|confirm|off are four distinct case values for the
			// one command the palette offers.
			base = "/bypass"
		}
		seen[base] = true
		if !known[base] {
			t.Errorf("submit now handles %q but paletteCommands (palette.go) does not offer %q — add it", cmd, base)
		}
	}
	// /model takes a free-form model-id argument, so submit dispatches it via
	// isModelCommand before the switch above and never appears as a case
	// literal there (see modelcmd.go) — assert it directly, mirroring how
	// TestChatHelpNamesEveryChatBinding checks it against chatHelpText.
	seen["/model"] = true
	if !known["/model"] {
		t.Error("paletteCommands does not offer /model")
	}

	// The other direction: an entry nobody dispatches is a stale row that
	// will mislead exactly the discoverability this feature exists for.
	for name := range known {
		if !seen[name] {
			t.Errorf("paletteCommands offers %q but submit()/isModelCommand does not dispatch it — remove it or fix the dispatch", name)
		}
	}
}

// --- layout: never taller or wider than the window ----------------------------

// Mirrors components/helpoverlay_test.go's TestHelpOverlayNeverOutgrowsTheWindow:
// the palette is composited full-window (see renderCommandPalette), so it
// must return exactly the screen it was handed, or fall back to the
// untouched background when there is truly no room.
func TestPaletteNeverOutgrowsTheWindow(t *testing.T) {
	sizes := []struct{ w, h int }{
		{100, 40},
		{80, 24}, // the size this feature is built for
		{80, 14},
		{80, 10},
		{80, 6},
		{40, 24},
		{20, 12},
		{8, 20},
		{80, 2},
	}
	background := strings.TrimSuffix(strings.Repeat("background\n", 40), "\n")

	for _, s := range sizes {
		out := renderCommandPalette(background, "/", paletteCommands, 0, s.w, s.h)
		if out == background {
			// Too small to draw a box — the documented fallback, same as
			// RenderHelpOverlay's.
			continue
		}
		rows := strings.Split(out, "\n")
		if len(rows) != s.h {
			t.Errorf("%dx%d rendered %d rows, want %d", s.w, s.h, len(rows), s.h)
			continue
		}
		for i, row := range rows {
			if wd := ansi.StringWidth(row); wd != s.w {
				t.Errorf("%dx%d: row %d is %d columns, want %d", s.w, s.h, i, wd, s.w)
				break
			}
		}
	}
}

// A window with no room for the box gets the view it already had.
func TestATinyWindowKeepsTheViewItHasForThePalette(t *testing.T) {
	const background = "the chat view"
	if got := renderCommandPalette(background, "/", paletteCommands, 0, 8, 20); got != background {
		t.Errorf("an 8-column window rendered a palette anyway: %q", got)
	}
	if got := renderCommandPalette(background, "/", paletteCommands, 0, 80, 2); got != background {
		t.Errorf("a 2-row window rendered a palette anyway: %q", got)
	}
}

// --- helpers -------------------------------------------------------------------

func paletteNames(items []paletteCommand) []string {
	names := make([]string, len(items))
	for i, c := range items {
		names[i] = c.Name
	}
	return names
}

func containsAll(haystack []string, want ...string) bool {
	set := make(map[string]bool, len(haystack))
	for _, h := range haystack {
		set[h] = true
	}
	for _, w := range want {
		if !set[w] {
			return false
		}
	}
	return true
}

// The borderless palette adds no rows of its own, so the full 7-command list
// (4 prefix lines + 7 items = 11 lines) must render on any window that can
// hold those 11 lines. The old bordered-design chrome budget (4 rows, 2 when
// "tight") made a 14-row window — taller than a 13-row one that worked —
// silently draw nothing.
func TestPaletteRendersOnEveryWindowTallEnoughForItsLines(t *testing.T) {
	items := filterPaletteCommands("/")
	lines := len(paletteBodyLines("/", items, 0, paletteBoxMaxWidth-4))
	for h := lines; h <= lines+6; h++ {
		if _, ok := buildPaletteBox("/", items, 0, 100, h); !ok {
			t.Errorf("buildPaletteBox drew nothing at height %d, body is only %d lines", h, lines)
		}
	}
	if _, ok := buildPaletteBox("/", items, 0, 100, lines-1); ok {
		t.Error("buildPaletteBox drew on a window one row too short — it should decline, not clip")
	}
}
