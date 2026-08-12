// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"github.com/charmbracelet/glamour/ansi"
	"github.com/charmbracelet/glamour/styles"
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
// rules, code, links, emphasis. Everything not named here keeps the builtin's
// behaviour, including chroma's per-language syntax colours inside fenced code
// blocks, which are already good.
func gaiaStyle(dark bool) ansi.StyleConfig {
	base := styles.LightStyleConfig
	p := lightPalette
	if dark {
		base = styles.DarkStyleConfig
		p = darkPalette
	}

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

	// A coloured bullet is what makes a list scan as a list at a glance.
	base.Item.BlockPrefix = "• "
	base.Item.Color = strPtr(p.marker)
	base.Enumeration.BlockPrefix = ". "
	base.Enumeration.Color = strPtr(p.marker)
	base.Task.Ticked = "[x] "
	base.Task.Unticked = "[ ] "

	// Inline code is the single most common styled span in an agent answer —
	// every file path, flag, and symbol. The builtin's red-on-grey reads as an
	// error; this is a quiet tint that says "literal".
	base.Code.Color = strPtr(p.code)
	base.Code.BackgroundColor = strPtr(p.codeBG)
	base.CodeBlock.Margin = uintPtr(1)

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

// palette holds the ANSI-256 codes one variant paints with. Glamour takes
// colours as strings, not lipgloss.AdaptiveColor, so light and dark are two
// concrete tables rather than one adaptive set.
type palette struct {
	body     string
	heading  string
	strong   string
	emph     string
	marker   string
	code     string
	codeBG   string
	link     string
	linkText string
	quote    string
	rule     string
}

var (
	// Dark: body lifted from the builtin's 252 to near-white so the answer is
	// the brightest thing in the pane — the user's own question is deliberately
	// dimmed, and the contrast between them is the point.
	darkPalette = palette{
		body:     "254",
		heading:  "81",  // bright cyan
		strong:   "231", // white
		emph:     "222", // warm sand
		marker:   "81",
		code:     "215", // amber
		codeBG:   "236",
		link:     "75",
		linkText: "81",
		quote:    "245",
		rule:     "240",
	}

	lightPalette = palette{
		body:     "234",
		heading:  "25", // deep blue
		strong:   "232",
		emph:     "94", // brown
		marker:   "25",
		code:     "130", // burnt orange
		codeBG:   "254",
		link:     "26",
		linkText: "25",
		quote:    "241",
		rule:     "250",
	}
)

func strPtr(s string) *string { return &s }
func boolPtr(b bool) *bool    { return &b }
func uintPtr(u uint) *uint    { return &u }
