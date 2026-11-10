// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"regexp"
	"strings"
	"unicode"
)

// Mirrors src/gaia/apps/webui/src/utils/speechText.ts so the TUI and the Agent
// UI read the same reply the same way. Lengths count runes.

// FirstPieceChars keeps the first piece short so speech starts quickly;
// PieceChars is the size of the rest.
const (
	FirstPieceChars = 180
	PieceChars      = 600
	// MaxPieceChars is the most one /audio/speech request carries.
	MaxPieceChars = 1000
)

var (
	reFence       = regexp.MustCompile("(?s)```.*?(```|$)")
	reImage       = regexp.MustCompile(`!\[[^\]]*\]\([^)]*\)`)
	reLink        = regexp.MustCompile(`\[([^\]]+)\]\([^)]*\)`)
	reTag         = regexp.MustCompile(`(?i)</?[a-z][^>]*>`)
	reURL         = regexp.MustCompile(`https?://\S+`)
	reInlineCode  = regexp.MustCompile("`([^`]*)`")
	reRule        = regexp.MustCompile(`(?m)^[ \t]*([-*_][ \t]*){3,}$`)
	reHeading     = regexp.MustCompile(`(?m)^[ \t]{0,3}#{1,6}[ \t]+`)
	reQuote       = regexp.MustCompile(`(?m)^[ \t]*>[ \t]?`)
	reListMark    = regexp.MustCompile(`(?m)^[ \t]*([-*+]|\d+[.)])[ \t]+`)
	reTableRule   = regexp.MustCompile(`(?m)^[ \t]*\|?[ \t:|-]+\|[ \t:|-]*$`)
	reTableRow    = regexp.MustCompile(`(?m)^[ \t]*\|(.*?)\|?[ \t]*$`)
	sentenceMarks = ".!?…؟"
)

// SpeakableText is the words of a markdown reply, as a person would read them
// aloud. Code blocks, images, raw URLs and table rules are skipped; links read
// as their text; list, heading and emphasis marks are dropped.
func SpeakableText(markdown string) string {
	s := reFence.ReplaceAllString(markdown, "\n")
	s = reImage.ReplaceAllString(s, "")
	s = reLink.ReplaceAllString(s, "$1")
	s = reTag.ReplaceAllString(s, "")
	s = reURL.ReplaceAllString(s, "")
	s = reInlineCode.ReplaceAllString(s, "$1")
	s = reRule.ReplaceAllString(s, "")
	s = reHeading.ReplaceAllString(s, "")
	s = reQuote.ReplaceAllString(s, "")
	s = reListMark.ReplaceAllString(s, "")
	s = reTableRule.ReplaceAllString(s, "")
	s = replaceTableRows(s)
	s = stripEmphasis(s, []string{"**", "*", "~~"}, false)
	// Underscores mark emphasis only outside a word, never in snake_case.
	s = stripEmphasis(s, []string{"__", "_"}, true)

	var lines []string
	for _, line := range strings.Split(s, "\n") {
		if line = strings.TrimSpace(line); line != "" {
			lines = append(lines, line)
		}
	}
	return strings.TrimSpace(strings.Join(lines, "\n"))
}

// replaceTableRows reads "| a | b |" as "a, b".
func replaceTableRows(s string) string {
	var b strings.Builder
	last := 0
	for _, m := range reTableRow.FindAllStringSubmatchIndex(s, -1) {
		b.WriteString(s[last:m[0]])
		var cells []string
		for _, c := range strings.Split(s[m[2]:m[3]], "|") {
			if c = strings.TrimSpace(c); c != "" {
				cells = append(cells, c)
			}
		}
		b.WriteString(strings.Join(cells, ", "))
		last = m[1]
	}
	b.WriteString(s[last:])
	return b.String()
}

// isWord is JavaScript's \w: ASCII letters, digits and underscore.
func isWord(r rune) bool {
	return r == '_' || (r < 128 && (unicode.IsLetter(r) || unicode.IsDigit(r)))
}

// stripEmphasis removes paired emphasis marks, keeping what they wrap. It is
// the regex /(d1|d2|...)(\S(?:[\s\S]*?\S)?)\1/g done by hand, since Go's
// regexp has no backreferences; outsideWord adds the (?<!\w) and (?!\w)
// guards the underscore form needs.
func stripEmphasis(s string, delims []string, outsideWord bool) string {
	rs := []rune(s)
	var b strings.Builder
	i := 0
	for i < len(rs) {
		start, end, d := matchEmphasis(rs, i, delims, outsideWord)
		if d == 0 {
			b.WriteRune(rs[i])
			i++
			continue
		}
		b.WriteString(string(rs[start:end]))
		i = end + d
	}
	return b.String()
}

// matchEmphasis tries each delimiter at i in order, as the regex alternation
// does, and returns the wrapped text's bounds and the delimiter's length, or
// a zero length when nothing matches at i.
func matchEmphasis(rs []rune, i int, delims []string, outsideWord bool) (int, int, int) {
	if outsideWord && i > 0 && isWord(rs[i-1]) {
		return 0, 0, 0
	}
	for _, ds := range delims {
		d := []rune(ds)
		if !hasAt(rs, i, d) {
			continue
		}
		start := i + len(d)
		if start >= len(rs) || unicode.IsSpace(rs[start]) {
			continue
		}
		// Lazy: the first closing delimiter after a non-space wins.
		for end := start + 1; end+len(d) <= len(rs); end++ {
			if unicode.IsSpace(rs[end-1]) || !hasAt(rs, end, d) {
				continue
			}
			if outsideWord && end+len(d) < len(rs) && isWord(rs[end+len(d)]) {
				continue
			}
			return start, end, len(d)
		}
	}
	return 0, 0, 0
}

func hasAt(rs []rune, i int, d []rune) bool {
	if i+len(d) > len(rs) {
		return false
	}
	for k, r := range d {
		if rs[i+k] != r {
			return false
		}
	}
	return true
}

// splitSentences splits after sentence-ending punctuation followed by
// whitespace, and at line breaks: /(?<=[.!?…؟])\s+|\n+/.
func splitSentences(text string) []string {
	rs := []rune(text)
	var out []string
	from := 0
	for i := 0; i < len(rs); {
		r := rs[i]
		if unicode.IsSpace(r) && i > 0 && strings.ContainsRune(sentenceMarks, rs[i-1]) {
			out = append(out, string(rs[from:i]))
			for i < len(rs) && unicode.IsSpace(rs[i]) {
				i++
			}
			from = i
			continue
		}
		if r == '\n' {
			out = append(out, string(rs[from:i]))
			for i < len(rs) && rs[i] == '\n' {
				i++
			}
			from = i
			continue
		}
		i++
	}
	return append(out, string(rs[from:]))
}

// cutLong cuts a sentence into pieces no longer than max, at a comma or space
// when one falls past the first third.
func cutLong(sentence string, max int) []string {
	var pieces []string
	rest := []rune(strings.TrimSpace(sentence))
	for len(rest) > max {
		window := string(rest[:max])
		at := lastRuneIndex(window, ", ")
		if v := lastRuneIndex(window, "; "); v > at {
			at = v
		}
		if v := lastRuneIndex(window, " "); v > at {
			at = v
		}
		cut := max
		if at*3 > max {
			cut = at + 1
		}
		pieces = append(pieces, strings.TrimSpace(string(rest[:cut])))
		rest = []rune(strings.TrimSpace(string(rest[cut:])))
	}
	if len(rest) > 0 {
		pieces = append(pieces, string(rest))
	}
	return pieces
}

// lastRuneIndex is strings.LastIndex counted in runes, -1 when absent.
func lastRuneIndex(s, sub string) int {
	i := strings.LastIndex(s, sub)
	if i < 0 {
		return -1
	}
	return len([]rune(s[:i]))
}

func runeLen(s string) int { return len([]rune(s)) }

// Pieces is the reply as pieces of whole sentences to request one at a time:
// a short first piece, then longer ones, none longer than max runes. A
// sentence longer than that is cut at a comma or space.
func Pieces(markdown string, max int) []string {
	text := SpeakableText(markdown)
	if text == "" {
		return nil
	}
	var sentences []string
	for _, s := range splitSentences(text) {
		sentences = append(sentences, cutLong(s, min(PieceChars, max))...)
	}
	var pieces []string
	current := ""
	for _, sentence := range sentences {
		limit := PieceChars
		if len(pieces) == 0 {
			limit = FirstPieceChars
		}
		limit = min(limit, max)
		switch {
		case current != "" && runeLen(current)+1+runeLen(sentence) > limit:
			pieces = append(pieces, current)
			current = sentence
		case current != "":
			current += " " + sentence
		default:
			current = sentence
		}
	}
	if current != "" {
		pieces = append(pieces, current)
	}
	return pieces
}
