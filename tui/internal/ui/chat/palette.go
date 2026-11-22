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
	"github.com/amd/gaia/tui/internal/event"
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
	{"/full-access", "Run every tool without asking first — shows a warning before it turns on"},
	{"/setup", "Run first-time setup (gaia flagship agent only)"},
	{"/connectors", "See connected apps and accounts; then personalize, revoke, add, remove, reconnect or default <id>"},
	{"/models", "Choose the models on this PC: download, skip, or pick the chat model"},
	{"/gpu", "Give the GPU more memory so bigger models fit (integrated-GPU PCs)"},
	{"/slack", "Connect this agent to Slack, or show the connection's status"},
	{"/discord", "Message GAIA from a Discord DM: set it up, or show its status"},
	{"/automations", "List reminders, alerts and triggers; pause, resume or remove one"},
	{"/model", "Switch the model this session runs on (gaia flagship agent only)"},
	{"/skills", "List skills, or add one or a library: /skills add <GitHub or skills.sh link>"},
	{"/research", "Research a topic on the web for a cited report: /research <topic>; alone, switches research mode"},
	{"/provider", "Choose Local, Fireworks AI, or AMD LLM Gateway; configure a key"},
	{"/gateway", "Connect GAIA to the AMD LLM gateway and pick its models"},
	{"/cost", "What this session has spent; /cost help for rates"},
	{"/context", "How full the model's context is, and what fills it"},
	{"/copy", "Pick any message with ↑/↓ and copy it with Enter"},
	{"/copy all", "Copy the whole conversation as markdown"},
	{"/read-aloud", "Read the last reply aloud; again (or /read-aloud stop) stops it"},
	{"/voice", "Pick the read-aloud voice and speed, or read every reply"},
	{agentCommand, "See or switch the agent program: gaiapy, or gaiacpp (Beta)"},
	{"/agents", "List installed agents and switch this session to one"},
	{"/image", "Choose the image model, size and steps; /image open shows the last image"},
	{"/update", "Check for a newer GAIA, install one (/update now, /update v0.25.0), or set auto-update"},
	{"/sessions", "Reopen a past session — ones waiting on you come first"},
	{"/sessions all", "Every session, eval and scheduled runs included"},
}

// modelPalettePrefix is what turns the palette into the model picker: the
// command name plus the space that starts its argument.
//
// `/model` takes a free-form model id, so it cannot be a flat palette row the
// way `/full-access on` could — one row per id at the top level would bury the
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

// agentPalettePrefix turns the palette into the agent-program picker, the way
// modelPalettePrefix does for models.
const agentPalettePrefix = agentCommand + " "

// agentPaletteCommands is the agent-program picker's rows.
func agentPaletteCommands() []paletteCommand {
	return []paletteCommand{
		{agentCommand, "Show both programs and which one is running"},
		{agentPalettePrefix + "gaiapy", "Python agent: every tool, well tested, heavier"},
		{agentPalettePrefix + "gaiacpp", "Native agent (Beta): fast, light, fewer tools"},
	}
}

// filterAgentCommands narrows the picker to rows matching what was typed
// after `/agent `.
func filterAgentCommands(arg string) []paletteCommand {
	var out []paletteCommand
	for _, c := range agentPaletteCommands() {
		if arg == "" || strings.Contains(c.Name, arg) {
			out = append(out, c)
		}
	}
	return out
}

// filterPaletteCommands narrows paletteCommands to the ones whose name
// starts with the composer's current text, case-insensitively, AND that
// available marks as offered. Recomputed fresh on every keystroke rather
// than cached — the list is 7 entries long, and caching it correctly would
// mean invalidating on every input change anyway.
//
// available is nil-safe: a nil map (no gating context, e.g. a bare palette
// unit test) offers everything, same as before per-agent gating existed.
func filterPaletteCommands(value string, available map[string]bool) []paletteCommand {
	// The space after `/model` is the switch into the model picker, so this
	// one case is matched BEFORE trimming — trimming would erase the very
	// character that distinguishes "/model" (still naming a command) from
	// "/model " (now naming its argument). Gated too: an agent that refuses
	// /model must not let typing the argument form open the picker anyway.
	if arg, ok := strings.CutPrefix(strings.ToLower(value), modelPalettePrefix); ok {
		if available != nil && !available[modelCommandPrefix] {
			return nil
		}
		return filterModelCommands(strings.TrimSpace(arg))
	}
	if arg, ok := strings.CutPrefix(strings.ToLower(value), agentPalettePrefix); ok {
		if available != nil && !available[agentCommand] {
			return nil
		}
		return filterAgentCommands(strings.TrimSpace(arg))
	}
	q := strings.ToLower(strings.TrimSpace(value))
	var out []paletteCommand
	for _, c := range paletteCommands {
		if available != nil && !available[c.Name] {
			continue
		}
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

// paletteFiltered is the command list for the composer's CURRENT text,
// narrowed to what this session's agent actually supports (availability.go).
func (m ChatModel) paletteFiltered() []paletteCommand {
	value := m.input.Value()
	out := filterPaletteCommands(value, m.availableCommandSet())
	if m.skillsListOnly() {
		for i := range out {
			if out[i].Name == skillsCommandPrefix {
				out[i].Desc = skillsListOnlyDesc
			}
		}
	}
	return append(out, filterSkillCommands(value, m.skillCommands, out)...)
}

// skillDescMax keeps a skill's row short: skill descriptions are written for
// models and often run to a paragraph.
const skillDescMax = 56

// filterSkillCommands is one palette row per installed skill whose /<name>
// starts with what is typed, skipping a name a built-in command already uses
// (submit runs the built-in, so the skill row would lie). Picking a row sends
// /<name>, which the agent runs on that skill.
func filterSkillCommands(value string, skills []event.SkillCommand, builtins []paletteCommand) []paletteCommand {
	q := strings.ToLower(strings.TrimSpace(value))
	if strings.Contains(q, " ") {
		return nil
	}
	taken := make(map[string]bool, len(paletteCommands))
	for _, c := range paletteCommands {
		taken[c.Name] = true
	}
	for _, c := range builtins {
		taken[c.Name] = true
	}
	var out []paletteCommand
	for _, s := range skills {
		name := "/" + s.Name
		if taken[name] || !strings.HasPrefix(name, q) {
			continue
		}
		out = append(out, paletteCommand{Name: name, Desc: "Skill · " + skillSummary(s.Description)})
	}
	return out
}

// skillSummary is a description's first sentence, cut to skillDescMax.
func skillSummary(desc string) string {
	desc = strings.Join(strings.Fields(desc), " ")
	if i := strings.Index(desc, ". "); i >= 0 {
		desc = desc[:i+1]
	}
	return ansi.Truncate(desc, skillDescMax, "…")
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
	if len(m.paletteFiltered()) == 0 {
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
	case tea.KeyRunes, tea.KeySpace, tea.KeyBackspace, tea.KeyDelete, tea.KeyLeft, tea.KeyRight,
		tea.KeyCtrlH, tea.KeyCtrlW, tea.KeyCtrlDelete:
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

	paletteCursorStyle = lipgloss.NewStyle().Reverse(true)
	paletteQueryStyle  = lipgloss.NewStyle().
				Foreground(theme.Dim)

	paletteNameStyle = lipgloss.NewStyle().
				Foreground(theme.Text)

	paletteDescStyle = lipgloss.NewStyle().
				Foreground(theme.Dim)

	// Marked by a caret and colour alone — no filled background — matching the
	// rest of the TUI's move away from background-tinted rows.
	paletteSelectedNameStyle = lipgloss.NewStyle().
					Bold(true).
					Foreground(theme.Selected)
	paletteSelectedDescStyle = lipgloss.NewStyle().
					Foreground(theme.Selected)
)

// paletteBoxMaxWidth is the panel's width on a wide terminal, matching the help
// panel's own budget (helpoverlay.go's helpBoxMaxWidth) so the two overlays
// read as one family; it grows past this only to fit a description whole.
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
	box, _, ok := buildPaletteBox(query, items, selected, width, height)
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
func buildPaletteBox(query string, items []paletteCommand, selected, width, height int) (box string, start int, ok bool) {
	if len(items) == 0 {
		return "", 0, false
	}

	// Wider than the usual cap only to show descriptions whole, never past the window.
	boxWidth := paletteBoxMaxWidth
	if need := paletteContentWidth(items) + 4; need > boxWidth {
		boxWidth = need
	}
	if boxWidth > width-4 {
		boxWidth = width - 4
	}
	inner := boxWidth - 4
	if inner < 1 || height < 3 {
		return "", 0, false
	}

	// Installed skills can make the list longer than the window, so it shows
	// the rows around the selection. Fewer than paletteMinRows is too short to
	// use: leave the composer visible instead.
	for rows := len(items); rows >= min(paletteMinRows, len(items)); rows-- {
		start = paletteWindowStart(len(items), selected, rows)
		lines := paletteBodyLines(query, items[start:start+rows], selected-start, inner)
		box = paletteBoxStyle.Width(boxWidth).Render(strings.Join(lines, "\n"))
		// Measured, not counted: Width() soft-wraps any line wider than the
		// box, so a title too long for a narrow terminal costs a row a line
		// count cannot see.
		if lipgloss.Height(box)+paletteChromeRows <= height {
			return box, start, true
		}
	}
	return "", 0, false
}

// paletteMinRows is the fewest item rows the palette will draw.
const paletteMinRows = 3

// paletteWindowStart is the first of rows visible items that keeps selected
// in view, scrolling only once the selection passes the bottom row.
func paletteWindowStart(n, selected, rows int) int {
	if rows >= n || selected < rows {
		return 0
	}
	start := selected - rows + 1
	if start > n-rows {
		start = n - rows
	}
	return start
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
	box, start, ok := buildPaletteBox(query, items, selected, width, height)
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
	shown := lipgloss.Height(box) - paletteBodyPrefixRows
	item := bodyLine - paletteBodyPrefixRows
	if item < 0 || item >= shown || start+item >= len(items) {
		return -1, true
	}
	return start + item, true
}

// paletteColumn is where descriptions start: past the widest listed name, so
// they line up in one column.
func paletteColumn(items []paletteCommand) int {
	column := paletteNameColumn
	for _, c := range items {
		if w := lipgloss.Width(c.Name) + paletteNameGutter; w > column {
			column = w
		}
	}
	return column
}

// paletteContentWidth is the widest item row, marker included.
func paletteContentWidth(items []paletteCommand) int {
	column, widest := paletteColumn(items), 0
	for _, c := range items {
		if w := lipgloss.Width(c.Desc); w > widest {
			widest = w
		}
	}
	return 2 + column + widest
}

// paletteBodyLines lays out the box's content: a title, a divider, the
// typed filter text, a blank line, then one row per matching command.
func paletteBodyLines(query string, items []paletteCommand, selected, inner int) []string {
	lines := []string{
		// Truncated like every other line: at a narrow width the title wrapped
		// onto two rows and pushed the box past the window.
		ansi.Truncate(paletteTitleStyle.Render("Slash Commands"), inner, "…"),
		dividerStyle.Render(strings.Repeat("─", inner)),
		ansi.Truncate(paletteQueryStyle.Render(query)+paletteCursorStyle.Render(" "), inner, "…"),
		"",
	}
	column := paletteColumn(items)
	for i, c := range items {
		name := c.Name + strings.Repeat(" ", paletteNameGutter)
		for lipgloss.Width(name) < column {
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
