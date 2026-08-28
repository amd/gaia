// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"math"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// paletteCommand is one row the "/" palette can offer.
type paletteCommand struct {
	Name string // the literal command text, e.g. "/model" — what Enter sends
	Desc string // one line: what it does, so the name alone never has to carry that
}

// paletteCommands is the palette's full list. It is a hand-written mirror of
// submit's command switch (plus /model, which submit dispatches separately
// before the switch — see isModelCommand) rather than something derived from
// the AST at runtime: a real command palette needs a description for every
// entry, which the switch's case labels don't carry. palette_test.go's
// TestPaletteListsEverySubmitCommand parses submit the same way
// helpoverlay_test.go parses handleKey, and fails the build the day submit
// grows a command this list doesn't know about.
var paletteCommands = []paletteCommand{
	{"/help", "Show the keyboard shortcuts and commands panel"},
	{"/clear", "Clear this conversation"},
	{"/memory", "View this agent's memory"},
	{"/bypass", "Run every tool without asking first — shows a warning before it turns on"},
	{"/setup", "Run first-time setup (gaia flagship agent only)"},
	{"/model", "Switch the model this session runs on (gaia flagship agent only)"},
}

// modelPalettePrefix is what turns the palette into the model picker: the
// command name plus the space that starts its argument.
//
// `/model` takes a free-form model id, so it cannot be a flat palette row the
// way `/bypass on` could — one row per id at the top level would bury the
// real commands under a model list nobody was looking for. So the palette
// gets a second level instead: `/model` stays one row, and typing the space
// after it swaps the list for the ids. The set is closed and known at compile
// time (client.ClaudeModels), which is exactly what makes it listable.
//
// Local ids are deliberately absent: only the agent knows which Lemonade
// models are actually downloaded, and a palette that offered one the machine
// does not have would be worse than not offering any. Bare `/model` — the
// first row here — asks the agent for that list.
const modelPalettePrefix = modelCommandPrefix + " "

// modelPaletteCommands is the model picker's rows, built fresh per call so
// the list can never drift from client.ClaudeModels.
func modelPaletteCommands() []paletteCommand {
	out := []paletteCommand{
		{modelCommandPrefix, "List every model, including the local ones this machine has"},
	}
	for _, cm := range client.ClaudeModels {
		out = append(out, paletteCommand{
			Name: modelPalettePrefix + cm.ID,
			// Short on purpose: the panel is capped at 60 columns and these
			// names are the longest in it, so a longer line is a truncated
			// line. Naming the destination is what has to survive the
			// budget — the launch banner and the header chip already carry
			// the longer "this conversation is sent to Anthropic".
			Desc: cm.Label + " · Anthropic API",
		})
	}
	return out
}

// filterModelCommands narrows the model picker to rows matching what has been
// typed after `/model `. Matched by substring, not prefix: nobody types
// "claude-" to find Haiku, they type "haiku".
func filterModelCommands(arg string) []paletteCommand {
	if arg == "" {
		return modelPaletteCommands()
	}
	var out []paletteCommand
	for _, c := range modelPaletteCommands() {
		if strings.Contains(strings.ToLower(c.Name), arg) {
			out = append(out, c)
		}
	}
	return out
}

// filterPaletteCommands narrows paletteCommands to the ones whose name
// starts with the composer's current text, case-insensitively. Recomputed
// fresh on every keystroke rather than cached — the list is 7 entries long,
// and caching it correctly would mean invalidating on every input change
// anyway.
func filterPaletteCommands(value string) []paletteCommand {
	// The space after `/model` is the switch into the model picker, so this
	// one case is matched BEFORE trimming — trimming would erase the very
	// character that distinguishes "/model" (still naming a command) from
	// "/model " (now naming its argument).
	if arg, ok := strings.CutPrefix(strings.ToLower(value), modelPalettePrefix); ok {
		return filterModelCommands(strings.TrimSpace(arg))
	}
	q := strings.ToLower(strings.TrimSpace(value))
	var out []paletteCommand
	for _, c := range paletteCommands {
		if strings.HasPrefix(c.Name, q) {
			out = append(out, c)
		}
	}
	return out
}

// commandPalette is the "/" palette's own state: whether it is showing, and
// which filtered row is selected. It carries no copy of the typed text —
// that lives in m.input, the composer, the single source of truth (see
// syncPalette) — so the palette and what is actually on screen can never
// disagree.
type commandPalette struct {
	open     bool
	selected int
}

// paletteFiltered is the command list for the composer's CURRENT text.
func (m ChatModel) paletteFiltered() []paletteCommand {
	return filterPaletteCommands(m.input.Value())
}

// syncPalette opens, filters, or closes the palette to match the composer's
// current text. It is folded into syncComposerHeight (model.go) rather than
// called at each key-handling site directly, so it runs after every
// composer-mutating event without anyone having to remember it.
//
// There is no separate "did the user just press /" flag to maintain: "/" as
// the first character of an empty composer and "/" typed mid-sentence
// produce different composer VALUES ("/" vs "hello /"), and only the first
// one starts with "/" — so deriving open-ness from the value alone already
// keeps the palette from hijacking a slash typed mid-word, with no history
// to track. Selection resets to the top match on every text change; only
// ↑/↓ (handlePaletteKey, which never touches m.input) move it after that.
func (m *ChatModel) syncPalette() {
	value := m.input.Value()
	if !strings.HasPrefix(value, "/") || strings.Contains(value, "\n") {
		m.palette.open = false
		m.palette.selected = 0
		return
	}
	if len(filterPaletteCommands(value)) == 0 {
		m.palette.open = false
		m.palette.selected = 0
		return
	}
	m.palette.open = true
	m.palette.selected = 0
}

// palettePassThroughKey reports whether a keystroke is still editing or
// navigating within a command name — the palette stays open (and
// re-filters) for these; everything else closes it (see handlePaletteKey).
func palettePassThroughKey(t tea.KeyType) bool {
	switch t {
	case tea.KeyRunes, tea.KeySpace, tea.KeyBackspace, tea.KeyDelete, tea.KeyLeft, tea.KeyRight:
		return true
	}
	return false
}

// handlePaletteKey routes one keystroke while the palette is open. It
// returns the (possibly updated) model, a Cmd, and whether it fully handled
// the key. false means "let handleKey's normal switch run too" — used both
// for pass-through editing keys (so typing keeps filtering) and for the
// close-and-continue case (so e.g. PgUp still scrolls once the palette is
// out of the way). Ctrl+C is handled by the caller before this is ever
// reached (see handleKey) — it must always fall through to its own case.
func (m ChatModel) handlePaletteKey(msg tea.KeyMsg) (ChatModel, tea.Cmd, bool) {
	switch msg.Type {
	case tea.KeyUp:
		if n := len(m.paletteFiltered()); n > 0 {
			m.palette.selected = (m.palette.selected - 1 + n) % n
		}
		return m, nil, true

	case tea.KeyDown:
		if n := len(m.paletteFiltered()); n > 0 {
			m.palette.selected = (m.palette.selected + 1) % n
		}
		return m, nil, true

	case tea.KeyEsc:
		// Closes the palette only — never the turn, never the composer. A
		// pending confirmation/question already owns Esc before this is ever
		// reached (see handleKey), so this is always the idle-composer case,
		// and idle Esc's own "clear the composer" meaning (#2932) does not
		// apply here: dismissing a suggestion list is not the same act as
		// discarding what was typed.
		m.palette.open = false
		return m, nil, true

	case tea.KeyEnter:
		if msg.Alt {
			// Alt+Enter means "new line" everywhere else in the composer,
			// and a multi-line composer can never be a slash command
			// (syncPalette closes on the first "\n") — close and let the
			// ordinary Alt+Enter case below insert it.
			m.palette.open = false
			return m, nil, false
		}
		filtered := m.paletteFiltered()
		if len(filtered) == 0 {
			m.palette.open = false
			return m, nil, false
		}
		idx := m.palette.selected
		if idx < 0 || idx >= len(filtered) {
			idx = 0
		}
		updated, cmd := m.runPaletteRow(filtered[idx].Name)
		next, ok := updated.(ChatModel)
		if !ok {
			return m, cmd, true
		}
		return next, cmd, true
	}

	if palettePassThroughKey(msg.Type) {
		return m, nil, false
	}

	// Any other key (PgUp/PgDn, Ctrl+T/Y/B/V, Home/End, Ctrl+J, ...) isn't
	// part of typing or navigating a command name — close the palette and
	// let that key go on to do whatever it already does.
	m.palette.open = false
	return m, nil, false
}

// runPaletteRow dispatches a command the way picking it is defined to behave —
// shared by Enter (handlePaletteKey) and a mouse click on the already-selected
// row (handlePaletteMouse), so the two can never disagree about what "run
// this row" means.
func (m ChatModel) runPaletteRow(name string) (tea.Model, tea.Cmd) {
	m.palette.open = false
	m.input.Reset()
	m.syncComposerHeight()
	// Mirrors the plain Enter path in handleKey: a selection made mid-turn
	// queues behind it rather than running immediately, and submit() is the
	// same dispatcher a typed command goes through — this must never post as
	// a chat message (see TestPaletteEnterRunsTheSelectedCommandNotTheTypedText).
	if m.streaming || m.setupChecking || m.setupRunning {
		m.queued = append(m.queued, name)
		m.updateViewport()
		return m, nil
	}
	return m.submit(name)
}

// handlePaletteMouse routes one mouse event while the palette is open.
// Hovering (or clicking) a row moves the selection to it, same as ↑/↓;
// clicking the row that is ALREADY selected runs it, same as Enter — so
// hover-then-click (the common case once motion tracking is on) reads as one
// click, and a click with no prior hover just selects. A click outside the
// box closes the palette without touching the composer, same as Esc. The
// wheel is left to the transcript underneath rather than swallowed, since
// the palette itself never scrolls (see paletteBodyLines' doc comment).
func (m ChatModel) handlePaletteMouse(msg tea.MouseMsg) (tea.Model, tea.Cmd) {
	if isWheelEvent(msg) {
		var cmd tea.Cmd
		m.viewport, cmd = m.viewport.Update(msg)
		return m.afterScroll(), cmd
	}

	items := m.paletteFiltered()
	row, insideBox := paletteHitTest(m.input.Value(), items, m.palette.selected, m.width, m.height, msg.X, msg.Y)

	switch msg.Action {
	case tea.MouseActionMotion:
		if row >= 0 {
			m.palette.selected = row
		}
		return m, nil

	case tea.MouseActionPress:
		if msg.Button != tea.MouseButtonLeft {
			return m, nil
		}
		if !insideBox {
			m.palette.open = false
			return m, nil
		}
		if row < 0 {
			return m, nil
		}
		if row == m.palette.selected {
			return m.runPaletteRow(items[row].Name)
		}
		m.palette.selected = row
		return m, nil
	}
	return m, nil
}

var (
	// No border and no fill. A bordered box centred in the window read as a
	// dialog floating in nothing; a fill subtle enough to be tasteful degrades
	// to invisible on several stock terminal themes (the theme package's
	// contrast suite measures it). Position and colour carry it instead.
	paletteBoxStyle = lipgloss.NewStyle().Padding(0, 2)

	paletteTitleStyle = lipgloss.NewStyle().
				Bold(true).
				Foreground(theme.Dim)

	paletteQueryStyle = lipgloss.NewStyle().
				Foreground(theme.Dim)

	paletteNameStyle = lipgloss.NewStyle().
				Foreground(theme.Text)

	paletteDescStyle = lipgloss.NewStyle().
				Foreground(theme.Dim)

	// The selected row is marked by a caret and colour alone — bold
	// AccentBright text, no filled background — matching the rest of the
	// TUI's move away from background-tinted rows (see the code-block and
	// status-bar fixes this pairs with).
	paletteSelectedNameStyle = lipgloss.NewStyle().
					Bold(true).
					Foreground(theme.Selected)
	paletteSelectedDescStyle = lipgloss.NewStyle().
					Foreground(theme.Selected)
)

// paletteBoxMaxWidth caps the panel on a wide terminal, matching the help
// panel's own budget (helpoverlay.go's helpBoxMaxWidth) so the two overlays
// read as one family.
const paletteBoxMaxWidth = 60

// paletteNameColumn is how many columns a command's name gets before its
// description starts — wide enough for the longest name ("/memory", 7
// columns) plus paletteNameGutter.
//
// It is a floor, not a cap: the model picker's rows ("/model
// claude-haiku-4-5") are more than twice this wide, and they keep the gutter
// rather than being squeezed into the column.
const paletteNameColumn = 9

// paletteNameGutter is the blank space every row keeps between its name and
// its description. Without it a name wider than paletteNameColumn ran
// straight into its own description — "/model claude-opus-5Claude Opus 5 …"
// on screen, which reads as one mangled string rather than two fields.
const paletteNameGutter = 2

// paletteBoxStyle is Padding(0, 2): the borderless box adds NO rows of its
// own, so the fits-check budgets zero chrome. The old bordered-design values
// (4 normally, 2 under 14 rows) made a window of exactly 14 rows draw
// nothing while a 13-row one drew fine.
const paletteChromeRows = 0

// renderCommandPalette renders the palette full-window, the same way
// RenderHelpOverlay does (components/helpoverlay.go): the box is placed on a
// fresh width x height canvas, not composited over the live background —
// background is returned untouched only when there is no room to draw a box
// at all. query is the raw composer text, echoed at the top of the box so
// the reader can still see what they typed once it takes over the screen.
func renderCommandPalette(background, query string, items []paletteCommand, selected, width, height int) string {
	box, ok := buildPaletteBox(query, items, selected, width, height)
	if !ok {
		return background
	}
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
}

// buildPaletteBox lays out the palette's box (border, padding and all) — the
// one place that decides its size and content, shared by renderCommandPalette
// (which places it on screen) and paletteHitTest (which has to know exactly
// where it landed to map a click back to a row). ok is false when there is no
// room to draw at all, mirroring renderCommandPalette's background fallback.
func buildPaletteBox(query string, items []paletteCommand, selected, width, height int) (box string, ok bool) {
	if len(items) == 0 {
		return "", false
	}

	boxWidth := width - 4
	if boxWidth > paletteBoxMaxWidth {
		boxWidth = paletteBoxMaxWidth
	}
	inner := boxWidth - 4
	if inner < 1 || height < 3 {
		return "", false
	}

	lines := paletteBodyLines(query, items, selected, inner)
	if len(lines)+paletteChromeRows > height {
		// No scrolling here (unlike help): the list is 7 commands long at
		// most, so a window too short to hold it is too short for a usable
		// palette at all — leave the composer visible instead of clipping.
		return "", false
	}

	return paletteBoxStyle.Width(boxWidth).Render(strings.Join(lines, "\n")), true
}

// paletteBodyPrefixRows is how many rendered lines (title, divider, echoed
// query, blank) come before the first item row in buildPaletteBox's content —
// see paletteBodyLines. paletteHitTest needs this to translate a rendered row
// back into an item index.
const paletteBodyPrefixRows = 4

// centerOffset replicates lipgloss.Place's centering math (position.go's
// placeHorizontal/placeVertical: split := round(gap*0.5), offset := gap -
// split) for one axis, so a click's screen coordinate can be mapped back onto
// the box lipgloss.Place drew without lipgloss exposing where it put it.
func centerOffset(outer, inner int) int {
	gap := outer - inner
	if gap <= 0 {
		return 0
	}
	split := int(math.Round(float64(gap) * 0.5))
	return gap - split
}

// paletteHitTest maps an absolute screen (x, y) — the coordinates a
// tea.MouseMsg carries, since the palette is placed on the full window canvas
// — to the item row it landed on. insideBox is false for anything outside the
// box entirely (the click-outside-closes case); row is -1 when insideBox is
// true but the row is chrome (title, divider, blank) rather than an item.
func paletteHitTest(query string, items []paletteCommand, selected, width, height, x, y int) (row int, insideBox bool) {
	box, ok := buildPaletteBox(query, items, selected, width, height)
	if !ok {
		return -1, false
	}

	boxW, boxH := lipgloss.Width(box), lipgloss.Height(box)
	left := centerOffset(width, boxW)
	top := centerOffset(height, boxH)
	if x < left || x >= left+boxW || y < top || y >= top+boxH {
		return -1, false
	}

	// Padding(0, 2) adds no rows above the content, so the box's first
	// screen row is already lines[0] — no chrome offset to subtract.
	bodyLine := y - top
	item := bodyLine - paletteBodyPrefixRows
	if item < 0 || item >= len(items) {
		return -1, true
	}
	return item, true
}

// paletteBodyLines lays out the box's content: a title, a divider, the
// typed filter text, a blank line, then one row per matching command.
func paletteBodyLines(query string, items []paletteCommand, selected, inner int) []string {
	lines := []string{
		paletteTitleStyle.Render("Slash Commands"),
		dividerStyle.Render(strings.Repeat("─", inner)),
		ansi.Truncate(paletteQueryStyle.Render(query)+"▏", inner, "…"),
		"",
	}
	for i, c := range items {
		name := c.Name + strings.Repeat(" ", paletteNameGutter)
		for lipgloss.Width(name) < paletteNameColumn {
			name += " "
		}
		nameStyle, descStyle, marker := paletteNameStyle, paletteDescStyle, "  "
		if i == selected {
			nameStyle, descStyle, marker = paletteSelectedNameStyle, paletteSelectedDescStyle, "▸ "
		}
		row := marker + nameStyle.Render(name) + descStyle.Render(c.Desc)
		lines = append(lines, ansi.Truncate(row, inner, "…"))
	}
	return lines
}
