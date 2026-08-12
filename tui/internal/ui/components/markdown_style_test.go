// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"strings"
	"testing"

	"github.com/charmbracelet/glamour/styles"
	"github.com/charmbracelet/x/ansi"
)

// renderDark builds the dark GAIA renderer and returns one document, styled.
func renderDark(t *testing.T, doc string) string {
	t.Helper()
	mu.Lock()
	styleName = styles.DarkStyle
	built = false
	wordWrap = 84
	mu.Unlock()
	out := RenderMarkdown(doc)
	if out == doc {
		t.Fatal("the renderer fell through to plain text")
	}
	return out
}

// Glamour's stock dark style renders an answer as one flat wash of grey:
// headings, body, and list markers all the same colour, and no table rules at
// all. An answer styled less than the status bar above it is the complaint this
// theme answers.
func TestAgentMarkdownIsActuallyStyled(t *testing.T) {
	out := renderDark(t, strings.Join([]string{
		"## Triage summary",
		"",
		"Found **3 issues**. The worst is in `lemonade_client.py`.",
		"",
		"- first finding",
		"",
		"| Issue | Action |",
		"|-------|--------|",
		"| 2924  | fix    |",
		"",
		"```python",
		"def pull(model): ...",
		"```",
		"",
		"> Ship it after the eval passes.",
	}, "\n"))

	// Single words, not phrases: glamour splits a styled run at every word
	// boundary, so "Triage summary" never appears contiguously in the output.
	heading := styledRun(out, "Triage")
	body := styledRun(out, "Found")
	if heading == "" || heading == body {
		t.Errorf("headings are indistinguishable from body text (%q vs %q)", heading, body)
	}
	if strong := styledRun(out, "3 issues"); strong == body {
		t.Errorf("bold text carries no colour shift, so it vanishes in a font with no real bold: %q", strong)
	}
	if code := styledRun(out, "lemonade_client.py"); !strings.Contains(code, "48;5;") {
		t.Errorf("inline code has no background tint, so paths blend into prose: %q", code)
	}
	// Chroma must still colour fenced code — the builtin's syntax highlighting
	// is good and the overrides must not have dropped it.
	if kw := styledRun(out, "def"); kw == "" || kw == body {
		t.Errorf("fenced code lost its syntax highlighting: %q", kw)
	}
	plain := ansi.Strip(out)
	if !strings.Contains(plain, "─") || !strings.Contains(plain, "│") {
		t.Errorf("tables render with no rules:\n%s", plain)
	}
	if !strings.Contains(plain, "│ Ship it") {
		t.Errorf("block quotes render with no bar:\n%s", plain)
	}
}

// The chat pane indents the answer itself; a second margin from glamour wastes
// four columns of an already-capped measure AND made the text jump right when
// `final` replaced the (unrendered) streamed tokens.
func TestRenderedMarkdownAddsNoMarginOfItsOwn(t *testing.T) {
	out := ansi.Strip(renderDark(t, "Just one plain sentence."))
	for _, line := range strings.Split(out, "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		if strings.HasPrefix(line, " ") {
			t.Errorf("glamour is still indenting; the chat pane owns that: %q", line)
		}
		break
	}
}

// A user who set GLAMOUR_STYLE asked for THAT style, not ours layered over it.
func TestAnExplicitGlamourStyleIsLeftAlone(t *testing.T) {
	t.Setenv(EnvStyle, styles.NoTTYStyle)
	mu.Lock()
	styleName = styles.NoTTYStyle
	built = false
	mu.Unlock()

	out := RenderMarkdown("# Heading\n")
	if out != ansi.Strip(out) {
		t.Errorf("the notty style emitted ANSI, so the override ignored GLAMOUR_STYLE: %q", out)
	}
}

// styledRun returns the ANSI parameters in force over the first occurrence of
// text — enough to tell "this span is styled differently from that one" without
// asserting an exact colour, which would make the palette impossible to tune.
func styledRun(rendered, text string) string {
	i := strings.Index(rendered, text)
	if i < 0 {
		return ""
	}
	start := strings.LastIndex(rendered[:i], "\x1b[")
	if start < 0 {
		return ""
	}
	end := strings.Index(rendered[start:], "m")
	if end < 0 {
		return ""
	}
	return rendered[start : start+end+1]
}
