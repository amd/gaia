// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

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
	{"/hub", "Return to the agent hub"},
	{"/clear", "Clear this conversation"},
	{"/memory", "View this agent's memory"},
	{"/bypass", "Run every tool without asking first — shows a warning before it turns on"},
	{"/setup", "Run first-time setup (gaia flagship agent only)"},
	{"/model", "Switch the model this session runs on (gaia flagship agent only)"},
}

// filterPaletteCommands narrows paletteCommands to the ones whose name
// starts with the composer's current text, case-insensitively. Recomputed
// fresh on every keystroke rather than cached — the list is 7 entries long,
// and caching it correctly would mean invalidating on every input change
// anyway.
func filterPaletteCommands(value string) []paletteCommand {
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
		name := filtered[idx].Name
		m.palette.open = false
		m.input.Reset()
		m.syncComposerHeight()
		// Mirrors the plain Enter path in handleKey exactly: a selection
		// made mid-turn queues behind it, same as manually typing the
		// command and pressing Enter would — see that path's own comment
		// for why (queued slash commands run once the turn they were typed
		// during is actually over, not immediately). Picking a row here is
		// a shortcut for typing its name, so it has to behave like that in
		// every other way too, including never posting as a chat message —
		// submit() is the same dispatcher a typed command goes through.
		if m.streaming || m.setupChecking || m.setupRunning {
			m.queued = append(m.queued, name)
			m.updateViewport()
			return m, nil, true
		}
		updated, cmd := m.submit(name)
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
// columns) plus a two-column gutter.
const paletteNameColumn = 9

// paletteChromeRows/paletteTightChromeRows/paletteTightHeight mirror
// helpoverlay.go's helpChromeRows family: the box costs one border row and
// one padding row at each end normally, and drops the padding on a short
// window rather than clip.
const (
	paletteChromeRows      = 4
	paletteTightChromeRows = 2
	paletteTightHeight     = 14
)

// renderCommandPalette renders the palette full-window, the same way
// RenderHelpOverlay does (components/helpoverlay.go): the box is placed on a
// fresh width x height canvas, not composited over the live background —
// background is returned untouched only when there is no room to draw a box
// at all. query is the raw composer text, echoed at the top of the box so
// the reader can still see what they typed once it takes over the screen.
func renderCommandPalette(background, query string, items []paletteCommand, selected, width, height int) string {
	if len(items) == 0 {
		return background
	}

	boxWidth := width - 4
	if boxWidth > paletteBoxMaxWidth {
		boxWidth = paletteBoxMaxWidth
	}
	inner := boxWidth - 4
	if inner < 1 || height < 3 {
		return background
	}

	style := paletteBoxStyle
	chrome := paletteChromeRows
	if height < paletteTightHeight {
		style = style.Padding(0, 2)
		chrome = paletteTightChromeRows
	}

	lines := paletteBodyLines(query, items, selected, inner)
	if len(lines)+chrome > height {
		// No scrolling here (unlike help): the list is 7 commands long at
		// most, so a window too short to hold it is too short for a usable
		// palette at all — leave the composer visible instead of clipping.
		return background
	}

	box := style.Width(boxWidth).Render(strings.Join(lines, "\n"))
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
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
		name := c.Name
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
