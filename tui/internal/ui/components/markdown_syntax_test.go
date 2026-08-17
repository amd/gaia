// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"math"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/charmbracelet/glamour/styles"
	"github.com/charmbracelet/x/ansi"
)

// Rendered assertions in this file are DARK only, on purpose. Glamour registers
// the per-token table under the fixed chroma style name "charm" and skips the
// registration if that name is already taken, so the first variant rendered in
// a process is the only one that can be observed in it. The real TUI resolves
// light-or-dark once at startup and never renders both; a test binary would
// quietly compare the light palette against dark's output. The light table is
// therefore checked where it is defined, not where it is drawn.

// Code carries NO background, anywhere. A tinted slab behind every inline span
// turned a paragraph about code into a patchwork of little rectangles, and the
// same fill behind a fence is a second shade of near-black on a black terminal.
// Colour is what marks code now — see gaiaChroma.
func TestCodeIsNeverPaintedOnABackground(t *testing.T) {
	out := renderDark(t, strings.Join([]string{
		"```python",
		"# warm the model first",
		"def pull(name):",
		"    return post(URL, json={\"n\": 42})",
		"```",
	}, "\n"))

	// Single words, not phrases: glamour splits a styled run at every word
	// boundary (see markdown_style_test.go).
	for _, word := range []string{"# warm", "def", "pull", "42", "\"n\""} {
		if run := styleBefore(out, word); strings.Contains(run, "48;5;") {
			t.Errorf("%q still carries a background fill: %q", word, run)
		}
	}
}

// An agent emits a bare ``` fence constantly — a log excerpt, a traceback, a
// path listing. Chroma has no lexer to colour it, so the background is the only
// thing that still says "this is not prose".
func TestAnUnlabelledFenceStillReadsAsCode(t *testing.T) {
	out := renderDark(t, "Body sentence.\n\n```\nplainfence\n```\n")

	fence := styleBefore(out, "plainfence")
	if strings.Contains(fence, "48;5;") {
		t.Errorf("an unlabelled fence is still painted on a background: %q", fence)
	}
	if body := styleBefore(out, "Body"); foreground(fence) == foreground(body) {
		t.Errorf("an unlabelled fence is the same colour as prose: %q", fence)
	}
}

// Five token families a reader actually separates on. If any two of them land
// on the same colour, the highlighting is decoration rather than information.
func TestTheTokenFamiliesAreToldApart(t *testing.T) {
	out := renderDark(t, strings.Join([]string{
		"```python",
		"# a comment",
		"def total(rows):",
		"    return sum(rows) + 42 + len(\"tail\")",
		"```",
	}, "\n"))

	families := map[string]string{
		"comment":  styleBefore(out, "# a"),
		"keyword":  styleBefore(out, "def"),
		"function": styleBefore(out, "total"),
		"number":   styleBefore(out, "42"),
		"string":   styleBefore(out, "\"tail\""),
	}
	for name, run := range families {
		if run == "" {
			t.Fatalf("no styled run found for %s — the probe is wrong, not the theme", name)
		}
	}
	for a, runA := range families {
		for b, runB := range families {
			if a < b && foreground(runA) == foreground(runB) {
				t.Errorf("%s and %s are the same colour (%s), so the highlighting carries no information",
					a, b, foreground(runA))
			}
		}
	}
	if foreground(families["comment"]) == foreground(styleBefore(out, "rows")) {
		t.Error("comments are the same colour as plain identifiers")
	}
}

// Inline code and a fenced block are the same kind of thing at two scales, so
// they share a background — but a path inside a sentence must never be mistaken
// for a code block, so the foreground differs.
func TestInlineCodeIsRelatedToFencedCodeWithoutBeingIt(t *testing.T) {
	out := renderDark(t, "Edit `lemonade_client.py`.\n\n```go\nfmt.Println(1)\n```\n")

	inline := styleBefore(out, "lemonade_client.py")
	fenced := styleBefore(out, "fmt")
	if background(inline) != "" || background(fenced) != "" {
		t.Errorf("code is still painted: inline=%q fenced=%q", inline, fenced)
	}
	if foreground(inline) == foreground(fenced) {
		t.Errorf("inline code is indistinguishable from a fenced block: %q", inline)
	}
}

// Code is not wrapped by hand, so glamour has to be the one holding it to the
// measure the chat pane set. A fence that runs wide would push the whole answer
// past the pane and let the viewport hard-wrap it mid-token.
func TestALongLineInAFenceStaysInsideTheMeasure(t *testing.T) {
	const measure = 84 // renderDark's word wrap
	out := renderDark(t, "```go\nfunc Handle(alpha int, beta string, gamma bool, delta float64) (Result, error) { return r, nil }\n```\n")
	for i, line := range strings.Split(ansi.Strip(out), "\n") {
		if w := ansi.StringWidth(line); w > measure {
			t.Errorf("code line %d is %d columns, over the %d-column measure: %q", i, w, measure, line)
		}
	}
}

// Comments are the line the author wrote to explain the code, and glamour's
// builtin renders them at #676767 — 2.8:1 on a dark pane, which is "technically
// drawn". Every syntax colour is held to WCAG AA against its own slab, in both
// variants, because the light table is never rendered in the same process as
// the dark one and this is where a regression in it would otherwise hide.
func TestEverySyntaxColourIsLegibleOnItsOwnBackground(t *testing.T) {
	const floor = 4.5

	for name, s := range map[string]syntax{"dark": darkSyntax, "light": lightSyntax} {
		for token, hex := range map[string]string{
			"text": s.text, "comment": s.comment, "keyword": s.keyword,
			"typeName": s.typeName, "function": s.function, "str": s.str,
			"number": s.number, "builtin": s.builtin, "punct": s.punct,
			"meta": s.meta, "added": s.added, "removed": s.removed,
		} {
			if hex == "" {
				t.Errorf("%s syntax leaves %s unset, so that token falls back to the builtin", name, token)
				continue
			}
			if got := contrast(t, hex, s.bg); got < floor {
				t.Errorf("%s %s (%s) is %.2f:1 on %s, under the %.1f:1 floor", name, token, hex, got, s.bg, floor)
			}
		}
		// Dim, but not by disappearing: the comment must still be visibly
		// quieter than the code around it.
		if contrast(t, s.comment, s.bg) >= contrast(t, s.text, s.bg) {
			t.Errorf("%s comments are as loud as the code they annotate", name)
		}
	}
}

// A colour that chroma cannot parse panics inside glamour's renderer, at render
// time, on the user's screen — MustNewStyle, not an error return. Hex only.
func TestSyntaxColoursAreSpelledTheWayChromaParsesThem(t *testing.T) {
	hex := regexp.MustCompile(`^#[0-9A-Fa-f]{6}$`)
	for name, s := range map[string]syntax{"dark": darkSyntax, "light": lightSyntax} {
		for _, hexColour := range []string{
			s.bg, s.text, s.comment, s.keyword, s.typeName, s.function,
			s.str, s.number, s.builtin, s.punct, s.meta, s.added, s.removed,
		} {
			if !hex.MatchString(hexColour) {
				t.Errorf("%s syntax has %q, which chroma rejects — it must be #RRGGBB", name, hexColour)
			}
		}
	}
}

// Chroma builds its style with MustNewStyle, so a malformed colour is a panic
// inside the renderer at the moment an answer arrives — not an error anyone can
// handle. Run this test on its own (`-run TestTheLightVariantRenders`) to prove
// the light table survives that call: in the full suite the dark table has
// already claimed the "charm" registration and light's is never parsed.
func TestTheLightVariantRendersWithoutPanicking(t *testing.T) {
	mu.Lock()
	styleName = styles.LightStyle
	built = false
	wordWrap = 84
	mu.Unlock()
	t.Cleanup(func() {
		mu.Lock()
		styleName = styles.DarkStyle
		built = false
		wordWrap = 100
		mu.Unlock()
	})

	out := RenderMarkdown("```python\n# note\ndef f(): return 1\n```\n")
	if out == "" || ansi.Strip(out) == out {
		t.Errorf("the light variant produced no styled output: %q", out)
	}
}

// styleBefore returns every ANSI sequence immediately preceding text. Unlike
// styledRun it keeps the whole run: a code token carries its foreground and its
// background as two separate sequences, and reading only the last one reports
// the same background for every token in the block.
func styleBefore(rendered, text string) string {
	// The trailing ` *` matters for inline code: glamour pads the span with a
	// space INSIDE the styled run, so the escapes are not flush against the text.
	re := regexp.MustCompile(`((?:\x1b\[[0-9;]*m)+) *` + regexp.QuoteMeta(text))
	m := re.FindStringSubmatch(rendered)
	if m == nil {
		return ""
	}
	return m[1]
}

var (
	fgParam = regexp.MustCompile(`38;[25];[0-9;]+`)
	bgParam = regexp.MustCompile(`48;[25];[0-9;]+`)
)

func foreground(run string) string { return fgParam.FindString(run) }
func background(run string) string { return bgParam.FindString(run) }

// contrast is the WCAG 2.x ratio between two #RRGGBB colours.
func contrast(t *testing.T, a, b string) float64 {
	t.Helper()
	la, lb := luminance(t, a), luminance(t, b)
	if la < lb {
		la, lb = lb, la
	}
	return (la + 0.05) / (lb + 0.05)
}

func luminance(t *testing.T, hex string) float64 {
	t.Helper()
	if len(hex) != 7 || hex[0] != '#' {
		t.Fatalf("not a #RRGGBB colour: %q", hex)
	}
	channel := func(i int) float64 {
		v, err := strconv.ParseUint(hex[i:i+2], 16, 8)
		if err != nil {
			t.Fatalf("cannot parse %q: %v", hex, err)
		}
		c := float64(v) / 255
		if c <= 0.04045 {
			return c / 12.92
		}
		return math.Pow((c+0.055)/1.055, 2.4)
	}
	return 0.2126*channel(1) + 0.7152*channel(3) + 0.0722*channel(5)
}
