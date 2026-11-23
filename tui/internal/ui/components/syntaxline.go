// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"strings"

	"github.com/alecthomas/chroma/v2"
	"github.com/alecthomas/chroma/v2/lexers"
	"github.com/charmbracelet/lipgloss"
)

// DiffSide says which side of a compact diff a line belongs to.
type DiffSide int

const (
	DiffContext DiffSide = iota
	DiffAdded
	DiffRemoved
)

// SyntaxLine renders one line of code from filename in the answer's syntax
// colours, on the diff tint for side. The tint spans width columns, so an added
// row reads as one green band rather than a highlighted word.
//
// Tokens are painted one by one with foreground AND background set, rather than
// colouring the line and wrapping it in a background: chroma's own resets would
// end the band at the first token.
func SyntaxLine(filename, code string, side DiffSide, width int) string {
	s := lightSyntax
	if lipgloss.HasDarkBackground() {
		s = darkSyntax
	}
	var bg lipgloss.TerminalColor = lipgloss.NoColor{}
	switch side {
	case DiffAdded:
		bg = lipgloss.Color(s.addedBg)
	case DiffRemoved:
		bg = lipgloss.Color(s.removedBg)
	}
	paint := func(text, fg string) string {
		return lipgloss.NewStyle().Foreground(lipgloss.Color(fg)).Background(bg).Render(text)
	}

	var b strings.Builder
	used := 0
	for _, tok := range tokenise(filename, code) {
		text := tok.Value
		if used+lipgloss.Width(text) > width {
			text = truncateToWidth(text, width-used)
		}
		if text == "" {
			break
		}
		b.WriteString(paint(text, tokenColour(tok.Type, s)))
		used += lipgloss.Width(text)
	}
	if pad := width - used; pad > 0 {
		b.WriteString(paint(strings.Repeat(" ", pad), s.text))
	}
	return b.String()
}

// tokenise lexes one line, or returns it as a single plain token when no lexer
// knows the file or the lexer fails.
func tokenise(filename, code string) []chroma.Token {
	code = strings.ReplaceAll(code, "\t", "    ")
	plain := []chroma.Token{{Type: chroma.Text, Value: code}}
	lexer := lexers.Match(filename)
	if lexer == nil {
		return plain
	}
	it, err := chroma.Coalesce(lexer).Tokenise(nil, code)
	if err != nil {
		return plain
	}
	var out []chroma.Token
	for tok := it(); tok != chroma.EOF; tok = it() {
		tok.Value = strings.TrimRight(tok.Value, "\n")
		if tok.Value != "" {
			out = append(out, tok)
		}
	}
	return out
}

// tokenColour maps a chroma token to the same palette gaiaChroma gives fenced
// code, so a diff and a code block in the answer read alike.
func tokenColour(t chroma.TokenType, s syntax) string {
	switch {
	case t.InCategory(chroma.Comment):
		if t == chroma.CommentPreproc {
			return s.meta
		}
		return s.comment
	case t == chroma.KeywordType:
		return s.typeName
	case t.InCategory(chroma.Keyword):
		return s.keyword
	case t == chroma.NameBuiltin || t == chroma.NameConstant:
		return s.builtin
	case t == chroma.NameFunction || t == chroma.NameAttribute:
		return s.function
	case t == chroma.NameClass:
		return s.typeName
	case t == chroma.NameDecorator:
		return s.meta
	case t == chroma.NameTag:
		return s.keyword
	case t.InSubCategory(chroma.LiteralNumber):
		return s.number
	case t == chroma.LiteralStringEscape:
		return s.builtin
	case t.InCategory(chroma.Literal):
		return s.str
	case t.InCategory(chroma.Operator) || t.InCategory(chroma.Punctuation):
		return s.punct
	}
	return s.text
}

// truncateToWidth cuts s to at most w display columns, never mid-rune.
func truncateToWidth(s string, w int) string {
	if w <= 0 {
		return ""
	}
	used := 0
	for i, r := range s {
		rw := lipgloss.Width(string(r))
		if used+rw > w {
			return s[:i]
		}
		used += rw
	}
	return s
}
