package components

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

type HelpContext int

const (
	// HelpContextChat is the only context there is, and deliberately the zero
	// value: a HelpContext nobody set opens the panel the user is looking at.
	HelpContextChat HelpContext = iota
)

var helpBoxStyle = lipgloss.NewStyle().Padding(1, 2)

const (
	// helpBoxMaxWidth keeps the panel readable on a wide terminal: a help list
	// stretched to 200 columns is one long scan line per binding.
	helpBoxMaxWidth = 60
	// helpChromeRows is what the panel costs beyond its content — one padding
	// row at each end. The border it used to also count is gone; a frame drew
	// the eye to the chrome instead of the bindings inside it.
	helpChromeRows = 2
	// helpTightChromeRows is the same with the vertical padding dropped, which
	// is what a short window gets instead of a clipped panel.
	helpTightChromeRows = 0
	// helpTightHeight is the window height below which the padding goes.
	helpTightHeight = 14
)

// RenderHelpOverlay renders a help panel centered over a background view.
//
// The panel is bounded by BOTH dimensions. It used to be bounded only by width:
// on a short terminal the box came out taller than the screen and lipgloss.Place
// clipped it from both ends at once, so the reader lost the title AND the last
// bindings with nothing on screen saying so. Now it drops its vertical padding
// first, then scrolls — see fitHelpLines.
//
// scroll is how many body lines are hidden above the visible window; 0 shows
// the top. The caller (root model) owns and clamps this across key presses —
// HelpMaxScroll reports the ceiling it should clamp to.
func RenderHelpOverlay(ctx HelpContext, background string, width, height, scroll int) string {
	return RenderHelpOverlayForCommands(ctx, background, width, height, scroll, nil)
}

// RenderHelpOverlayForCommands is RenderHelpOverlay, but with the panel's
// Commands line narrowed to commands — nil means "show every command",
// exactly RenderHelpOverlay's own behavior. HelpState is the caller that
// actually has a session's available-command set to pass; every other
// caller (context-free rendering, most tests) wants the unfiltered master
// list and goes through RenderHelpOverlay instead.
func RenderHelpOverlayForCommands(ctx HelpContext, background string, width, height, scroll int, commands []string) string {
	content := helpTextFor(ctx, commands)

	boxWidth, inner, rows, ok := helpBoxSize(width, height)
	if !ok {
		return background
	}

	style := helpBoxStyle
	if height < helpTightHeight {
		style = style.Padding(0, 2)
	}

	lines := fitHelpLines(content, inner, rows, scroll)
	box := style.Width(boxWidth).Render(strings.Join(lines, "\n"))
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
}

// HelpMaxScroll reports the furthest a panel at this size can scroll before
// it reaches its last line — 0 when the whole thing already fits, or when
// there is no room to draw a panel at all. Root uses this to clamp the
// scroll offset it drives on ↑/↓/PgUp/PgDn/Home/End while help is open; it
// has to use EXACTLY the row math fitHelpLines uses below, or a Home jump on
// one and a real line count on the other disagree.
func HelpMaxScroll(ctx HelpContext, width, height int) int {
	return HelpMaxScrollForCommands(ctx, width, height, nil)
}

// HelpMaxScrollForCommands is HelpMaxScroll for a panel rendered with
// RenderHelpOverlayForCommands — the two must agree on commands, or a
// filtered panel's Home/End jump clamps against a line count that assumes
// the unfiltered text.
func HelpMaxScrollForCommands(ctx HelpContext, width, height int, commands []string) int {
	_, _, rows, ok := helpBoxSize(width, height)
	if !ok {
		return 0
	}
	lines := strings.Split(helpTextFor(ctx, commands), "\n")
	if len(lines) <= rows {
		return 0
	}
	return maxScrollFor(len(lines), helpContentRows(rows))
}

func helpTextFor(_ HelpContext, commands []string) string {
	return renderCommandsSection(chatHelpText, commands)
}

// helpCommandsLabel/helpCommandsIndent match the fixed two-line layout
// chatHelpText's Commands block uses, so a filtered list wraps the same way
// the full one always has.
const (
	helpCommandsLabel  = "  Commands    "
	helpCommandsIndent = "              "
)

// renderCommandsSection swaps chatHelpText's Commands block for commands. An
// empty/nil commands set means the caller has nothing to filter (no session
// yet, or a context that isn't gated) — the master list passes through
// unchanged rather than showing a blank block.
//
// The block is known to be exactly two physical lines in chatHelpText (the
// label line and one continuation), so the second line is always skipped by
// position, not by matching its indent — several unrelated lines share the
// same 14-space continuation indent (the Esc and "/" entries above and
// below it), and matching by indent would eat those too.
func renderCommandsSection(text string, commands []string) string {
	if len(commands) == 0 {
		return text
	}
	lines := strings.Split(text, "\n")
	out := make([]string, 0, len(lines))
	for i := 0; i < len(lines); i++ {
		if strings.HasPrefix(lines[i], helpCommandsLabel) {
			out = append(out, wrapCommandLines(commands)...)
			i++ // skip the fixed layout's second Commands line
			continue
		}
		out = append(out, lines[i])
	}
	return strings.Join(out, "\n")
}

// wrapCommandLines lays commands out the same way chatHelpText's own
// Commands block is hand-wrapped: a label-prefixed first line, continuation
// lines indented to match, each kept under the panel's width budget.
func wrapCommandLines(commands []string) []string {
	const maxWidth = helpBoxMaxWidth - 4 // same budget TestHelpTextFitsItsBudget enforces
	var out []string
	line := helpCommandsLabel
	empty := true
	for _, c := range commands {
		candidate := line
		if !empty {
			candidate += " "
		}
		candidate += c
		if !empty && ansi.StringWidth(candidate) > maxWidth {
			out = append(out, line)
			line = helpCommandsIndent + c
			empty = false
			continue
		}
		line = candidate
		empty = false
	}
	out = append(out, line)
	return out
}

// helpBoxSize returns the box's own width, the content columns inside its
// padding, and the content row budget for a panel at width x height — the
// same three numbers RenderHelpOverlay and HelpMaxScroll both need, computed
// once so they cannot drift apart.
func helpBoxSize(width, height int) (boxWidth, inner, rows int, ok bool) {
	boxWidth = width - 4
	if boxWidth > helpBoxMaxWidth {
		boxWidth = helpBoxMaxWidth
	}
	// The horizontal padding (2 each side) leaves this much for the text. Any
	// less and there is no panel to draw, so leave the view alone.
	inner = boxWidth - 4
	if inner < 1 || height < 3 {
		return 0, 0, 0, false
	}

	chrome := helpChromeRows
	if height < helpTightHeight {
		chrome = helpTightChromeRows
	}
	rows = height - chrome
	if rows < 1 {
		rows = 1
	}
	return boxWidth, inner, rows, true
}

// helpContentRows is how many of the row budget go to real text once one row
// is set aside for a scroll indicator — all of it, if there is only one row
// to begin with and no room to spare.
func helpContentRows(rows int) int {
	if rows <= 1 {
		return rows
	}
	return rows - 1
}

func maxScrollFor(totalLines, contentRows int) int {
	m := totalLines - contentRows
	if m < 0 {
		return 0
	}
	return m
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// fitHelpLines forces the panel body to a known number of rows and columns.
// Row count has to be exact: a line that lipgloss soft-wraps adds a row nobody
// counted, and the panel silently grows past the clamp it was just given.
//
// When the content is longer than the row budget, one row goes to a scroll
// indicator instead of a content line — replacing the old hard truncation,
// which just cut the list and said "too short for the rest" with no way to
// see what got cut. scroll picks the window; the indicator always says
// whether there is more above, below, or both, so cutting the list is never
// silent.
func fitHelpLines(content string, inner, rows, scroll int) []string {
	lines := strings.Split(content, "\n")
	for i, line := range lines {
		if ansi.StringWidth(line) > inner {
			lines[i] = ansi.Truncate(line, inner, "…")
		}
	}
	if rows < 1 {
		rows = 1
	}
	if len(lines) <= rows {
		return lines
	}

	contentRows := helpContentRows(rows)
	maxScroll := maxScrollFor(len(lines), contentRows)
	scroll = clampInt(scroll, 0, maxScroll)

	visible := append([]string{}, lines[scroll:scroll+contentRows]...)
	if contentRows < rows {
		visible = append(visible, ansi.Truncate(helpScrollIndicator(scroll, maxScroll), inner, "…"))
	}
	return visible
}

// helpScrollIndicator names which direction(s) still have hidden content.
// Only called once maxScroll > 0 has already been established by the caller.
func helpScrollIndicator(scroll, maxScroll int) string {
	switch {
	case scroll <= 0:
		return "  ── ↓ more below · PgDn ──"
	case scroll >= maxScroll:
		return "  ── ↑ more above · PgUp ──"
	default:
		return "  ── ↑ more above · ↓ more below ──"
	}
}

// chatHelpText is no longer bounded to a fixed line count — RenderHelpOverlay
// scrolls whatever does not fit (see fitHelpLines) — but every line still has
// to fit helpBoxMaxWidth-4 columns, or it soft-wraps and throws off the row
// count the box was told to draw.
const chatHelpText = `  GAIA Chat
  ──────────────────
  Enter       Send (queues if the agent is busy)
  Alt+Enter   New line in the composer (Ctrl+J too)
  Esc         Cancel the turn (clears the composer
              when there is nothing running)
  Esc twice   Give up waiting on the cancel
  Ctrl+C      Quit

  Commands    /help /clear /bypass /cost
              /setup /memory /model /provider /agents
  /           On an empty line, browse commands —
              hover/click or ↑/↓ to pick, Enter or
              click to run, Esc or click out to close

  Scroll        ↑ / ↓ line · PgUp/PgDn page
  Home / End    Top / bottom, if the composer is
                empty — otherwise cursor keys
  Mouse wheel   Scrolls (Ctrl+T for drag-select)
  Click         Opens a printed link (your terminal
                may underline it on hover) · picks a
                palette row or question option
  Double-click  Copies that message

  Copy and paste
  ──────────────────
  Shift+drag (Option in iTerm2) selects text, then
  copy with your terminal's own Ctrl+Shift+C/Cmd+C.
  Ctrl+T      Select mode — hands the mouse back
              for plain drag-select; Esc ends it.
  Ctrl+V      Paste — a clipboard screenshot pastes
              as a file path · Ctrl+Y copy answer ·
              Ctrl+B code
  Drag a file/folder in — pastes as its path`
