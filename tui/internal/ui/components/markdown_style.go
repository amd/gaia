// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"github.com/charmbracelet/glamour/ansi"
	"github.com/charmbracelet/glamour/styles"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// gaiaStyle derives GAIA's markdown look from a glamour builtin.
//
// Glamour's stock dark style renders an agent answer as one flat wash of grey
// 252: headings barely separate from body text, list bullets are the same
// colour as the words after them, tables have no rules at all, and `strong` is
// bold-only, which most terminal fonts render as a barely-visible weight shift.
// An answer that is mostly prose therefore arrives looking like a log line — the
// thing the user actually asked for, styled less than the status bar above it.
//
// So the overrides below are structural rather than decorative: they give the
// eye something to land on when scanning an answer — headings, bullets, table
// rules, code, links, emphasis.
func gaiaStyle(dark bool) ansi.StyleConfig {
	base := styles.LightStyleConfig
	if dark {
		base = styles.DarkStyleConfig
	}
	p := gaiaPalette(dark)

	// The chat pane already indents the whole answer (answerPanelStyle), so
	// glamour must not add its own margin on top. Two indents deep wastes four
	// columns of an already-capped measure, and — because the streaming buffer
	// is NOT markdown-rendered — the text visibly jumped two columns to the
	// right the moment `final` replaced the streamed tokens with the rendered
	// copy.
	base.Document.Margin = uintPtr(0)
	base.Document.Color = strPtr(p.body)

	// Headings carry the answer's structure, so they get the accent and a blank
	// line above. The builtin's h1 is white-on-purple, a filled block that
	// dominates any answer that opens with a title.
	base.Heading.Color = strPtr(p.heading)
	base.Heading.Bold = boolPtr(true)
	base.H1.BackgroundColor = nil
	base.H1.Color = strPtr(p.heading)
	base.H1.Bold = boolPtr(true)
	base.H1.Prefix = ""
	base.H1.Suffix = ""
	base.H1.BlockPrefix = "\n"
	base.H2.Prefix = ""
	base.H2.BlockPrefix = "\n"
	base.H3.Prefix = ""
	base.H4.Prefix = ""
	base.H5.Prefix = ""
	base.H6.Prefix = ""

	// Emphasis has to survive a font with no real bold or italic, so both carry
	// a colour shift as well.
	base.Strong.Bold = boolPtr(true)
	base.Strong.Color = strPtr(p.strong)
	base.Emph.Italic = boolPtr(true)
	base.Emph.Color = strPtr(p.emph)

	// Prefix only: glamour paints list items in the Document colour and ignores
	// Item.Color entirely (TestAListMarkerIsAGlyphNotAColour).
	base.Item.BlockPrefix = "• "
	base.Enumeration.BlockPrefix = ". "
	base.Task.Ticked = "[x] "
	base.Task.Unticked = "[ ] "

	// Inline code is the single most common styled span in an agent answer —
	// every file path, flag, and symbol. Colour alone marks it: a tinted box
	// around each one turns a paragraph about code into a patchwork of little
	// rectangles, and an answer naming six symbols was mostly boxes.
	base.Code.Color = strPtr(p.code)
	// Explicit nil, not merely un-set: the glamour builtin sets one of its own,
	// and dropping our override just let THAT through (the same reason H1 above
	// clears its purple fill).
	base.Code.BackgroundColor = nil

	// One column of inset, so the block's tinted background starts clear of the
	// prose margin. Glamour reflows code to the same measure as the rest of the
	// document, so a long line inside a fence wraps rather than running past
	// the width the caller set with SetWordWrap.
	base.CodeBlock.Margin = uintPtr(1)
	// Distinct from body on purpose: with no fill and no lexer, an unlabelled
	// fence has nothing else left to say it is not a paragraph.
	base.CodeBlock.Color = strPtr(p.syntax.punct)
	base.CodeBlock.BackgroundColor = nil
	base.CodeBlock.Chroma = gaiaChroma(p.syntax)

	// Tables arrive from tool results often enough to be worth real rules.
	base.Table.CenterSeparator = strPtr("┼")
	base.Table.ColumnSeparator = strPtr("│")
	base.Table.RowSeparator = strPtr("─")

	base.Link.Color = strPtr(p.link)
	base.Link.Underline = boolPtr(true)
	base.LinkText.Color = strPtr(p.linkText)
	base.LinkText.Bold = boolPtr(true)

	base.BlockQuote.Color = strPtr(p.quote)
	base.BlockQuote.IndentToken = strPtr("│ ")
	base.BlockQuote.Italic = boolPtr(true)

	base.HorizontalRule.Color = strPtr(p.rule)
	base.HorizontalRule.Format = "\n────────\n"

	return base
}

// palette is what one variant of the markdown renderer paints prose with.
// Glamour takes colours as plain strings, not lipgloss.AdaptiveColor, so the
// mode has to be resolved before the config is built — see gaiaPalette.
type palette struct {
	body     string
	heading  string
	strong   string
	emph     string
	code     string
	link     string
	linkText string
	quote    string
	rule     string
	syntax   syntax
}

// gaiaPalette flattens the theme roles into the strings glamour wants.
//
// An answer is the largest thing on the screen, so it is also the loudest place
// an off-palette colour can hide: this table used to paint headings bright
// cyan, emphasis warm sand and inline code amber — three hues the product does
// not otherwise own, inside a copper-and-graphite UI. Every prose colour is now
// a role, and the only mode-reach in the TUI lives here (literals_test.go
// exempts this function by name and says why).
func gaiaPalette(dark bool) palette {
	pick := func(c lipgloss.AdaptiveColor) string {
		if dark {
			return c.Dark
		}
		return c.Light
	}
	syn := lightSyntax
	if dark {
		syn = darkSyntax
	}
	return palette{
		body:    pick(theme.Text),
		heading: pick(theme.AccentBright),
		// Bold and italic stay neutral. A model bolds half a dozen phrases per
		// answer, so painting them copper turns running prose into the speckle
		// the palette reserves for the prompt and the one primary action; the
		// SGR attribute is the signal, the colour would only be decoration.
		strong: pick(theme.Text),
		emph:   pick(theme.Text),
		// Inline code is the one span with nothing else left: the tinted box
		// was dropped because an answer naming six symbols came out as a row of
		// little rectangles, so colour alone has to say "this is a literal".
		code: pick(theme.Accent),
		// Blue for links is the one convention older than this palette, and
		// Info is the role that already carries it elsewhere in the TUI.
		link:     pick(theme.Info),
		linkText: pick(theme.Info),
		quote:    pick(theme.Dim),
		rule:     pick(theme.Divider),
		syntax:   syn,
	}
}

// syntax is the fenced-code half of a palette. Its values are hex, not
// ANSI-256 indices, because chroma parses these strings itself and rejects a
// bare number — glamour hands them straight to chroma.MustNewStyle, which
// panics on anything it cannot parse.
type syntax struct {
	bg       string
	text     string // plain identifiers, and every token of an unlabelled fence
	comment  string
	keyword  string
	typeName string
	function string
	str      string
	number   string
	builtin  string
	punct    string
	meta     string // decorators, preprocessor lines, attributes
	added    string
	removed  string
}

// The syntax hues are One Half Dark / One Half Light — the same pair the rest
// of the TUI's palette is derived from (see internal/ui/theme), and a scheme
// that ships with Windows Terminal, GNOME Terminal and macOS Terminal, so code
// in the chat pane looks like code in the user's editor rather than invented.
//
// Every colour clears 4.5:1 against its own slab background, INCLUDING the
// comment. Glamour's builtin puts comments at #676767, which is 2.8:1 on a dark
// pane — the classic "comments are technically rendered" failure, where the
// line the author wrote to explain the code is the one line nobody can read.
//
// These two tables are the TUI's only sanctioned colour literals outside
// theme.go, and literals_test.go names them one by one. They cannot become
// roles: a role is measured against the TERMINAL's background, and these are
// measured against the fence's own slab, which is neither terminal background
// (TestEverySyntaxColourIsLegibleOnItsOwnBackground). A fence also has to stay
// a different colour from prose, so `text` is specifically NOT theme.Text.
var (
	darkSyntax = syntax{
		bg:       "#262626",
		text:     "#ABB2BF", // distinct from body — see TestAnUnlabelledFenceStillReadsAsCode
		comment:  "#8C93A1",
		keyword:  "#C678DD", // purple
		typeName: "#E5C07B", // yellow
		function: "#61AFEF", // blue
		str:      "#98C379", // green
		number:   "#D19A66", // orange
		builtin:  "#56B6C2", // cyan
		punct:    "#ABB2BF",
		meta:     "#E5C07B",
		added:    "#98C379",
		removed:  "#E06C75",
	}

	lightSyntax = syntax{
		bg:       "#E4E4E4",
		text:     "#4B5563", // distinct from body — see TestAnUnlabelledFenceStillReadsAsCode
		comment:  "#5F6369",
		keyword:  "#8B208A",
		typeName: "#6B4900",
		function: "#10548A",
		str:      "#276024",
		number:   "#8F4108",
		builtin:  "#0A5866",
		punct:    "#4A4F58",
		meta:     "#6B4900",
		added:    "#276024",
		removed:  "#96232F",
	}
)

// gaiaChroma builds the per-token table glamour hands to chroma.
//
// Every entry names its own background, which looks redundant next to the
// Background entry but is not: chroma emits the background only where a token
// asks for one, so a table that sets it once at the top produces no tint at all
// — which is exactly what the builtin does, and why a fence with no language
// used to arrive as prose-coloured text with no block around it. Painting each
// token means an unlabelled fence (all Text) still reads as a slab.
//
// Note glamour registers this table under the fixed chroma style name "charm"
// and only once per process, so a single process gets a single variant. That is
// true of the TUI, which resolves light-or-dark once at startup — but not of a
// test binary, where the first render wins and a later variant is ignored.
func gaiaChroma(s syntax) *ansi.Chroma {
	// Foreground only. Painting each token's own background tinted the block
	// from the inside, so removing the block's fill alone would have left the
	// slab behind — and the syntax colours are what mark this as code.
	on := func(hex string) ansi.StylePrimitive {
		return ansi.StylePrimitive{Color: strPtr(hex)}
	}
	bold := func(hex string) ansi.StylePrimitive {
		e := on(hex)
		e.Bold = boolPtr(true)
		return e
	}
	return &ansi.Chroma{
		Background: ansi.StylePrimitive{},
		Text:       on(s.text),
		Error:      on(s.removed),

		Comment:        on(s.comment),
		CommentPreproc: on(s.meta),

		// Reserved words and namespaces are the same weight of thing as a
		// keyword; splitting them across three hues turns an import block into
		// confetti.
		Keyword:          on(s.keyword),
		KeywordReserved:  on(s.keyword),
		KeywordNamespace: on(s.keyword),
		KeywordType:      on(s.typeName),

		Operator:    on(s.punct),
		Punctuation: on(s.punct),

		Name:          on(s.text),
		NameBuiltin:   on(s.builtin),
		NameTag:       on(s.keyword),  // HTML/XML/YAML keys
		NameAttribute: on(s.function), // and their attributes
		NameClass:     bold(s.typeName),
		NameConstant:  on(s.builtin),
		NameDecorator: on(s.meta),
		NameException: on(s.removed),
		NameFunction:  on(s.function),
		NameOther:     on(s.text),

		Literal:             on(s.str),
		LiteralNumber:       on(s.number),
		LiteralDate:         on(s.number),
		LiteralString:       on(s.str),
		LiteralStringEscape: on(s.builtin),

		// A diff is the one language where colour carries the meaning rather
		// than decorating it, so red and green have to be unmistakable.
		GenericDeleted:    on(s.removed),
		GenericInserted:   on(s.added),
		GenericEmph:       ansi.StylePrimitive{Color: strPtr(s.text), Italic: boolPtr(true)},
		GenericStrong:     bold(s.text),
		GenericSubheading: on(s.comment),
	}
}

func strPtr(s string) *string { return &s }
func boolPtr(b bool) *bool    { return &b }
func uintPtr(u uint) *uint    { return &u }
