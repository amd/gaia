// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// The report this layout came from: "the memory view is very poorly designed …
// remove borders, use background colors … more legible and digestible."

func TestMemoryViewDrawsNoBorders(t *testing.T) {
	out := ansi.Strip(renderMemoryView(sampleMemoryDump(), 88))
	for _, glyph := range []string{"┌", "└", "│", "┐", "┘", "─"} {
		if strings.Contains(out, glyph) {
			t.Errorf("memory view still draws box-drawing character %q", glyph)
		}
	}
}

// A section is delineated by a FILLED band, which is the whole reason borders
// could go. Asserted on the style rather than the rendered escapes: lipgloss
// strips colour when it cannot see a TTY, so a rendered-output check would pass
// under `go test` no matter what the style said.
func TestSectionHeadersAreFilled(t *testing.T) {
	if memoryBandStyle.GetBackground() != theme.SurfaceBG {
		t.Error("section bands lost their fill, so nothing separates the sections")
	}
	if memoryBandStyle.GetForeground() != theme.OnSurface {
		t.Error("band text is not the colour vetted against the band's own fill")
	}
}

// The fill has to span the pane exactly — short and it reads as a ragged
// highlight rather than a section.
func TestSectionBandsSpanTheFullPane(t *testing.T) {
	for _, width := range []int{24, 60, 88, 140} {
		var found bool
		for _, line := range strings.Split(renderMemoryView(sampleMemoryDump(), width), "\n") {
			if !strings.Contains(ansi.Strip(line), "PREFERENCE") {
				continue
			}
			found = true
			if got := ansi.StringWidth(ansi.Strip(line)); got != width {
				t.Errorf("width %d: band is %d columns", width, got)
			}
		}
		if !found {
			t.Fatalf("width %d: no PREFERENCE band rendered", width)
		}
	}
}

// The point of moving metadata off the prose line: confidence and age must not
// trail the sentence, where they read as part of it.
func TestMetadataSitsOnItsOwnLine(t *testing.T) {
	dump := sampleMemoryDump()
	dump.Items = []client.MemoryItem{{
		ID: "1", Category: "fact", Content: "the user prefers tabs",
		Confidence: 0.42, UpdatedAt: "2026-08-14T15:36:00",
	}}

	for _, line := range strings.Split(ansi.Strip(renderMemoryView(dump, 88)), "\n") {
		if !strings.Contains(line, "0.42") {
			continue
		}
		if strings.Contains(line, "prefers tabs") {
			t.Errorf("metadata is still trailing the content: %q", line)
		}
		return
	}
	t.Fatal("confidence never rendered")
}

func TestEntrySubjectIsSeparatedFromItsBody(t *testing.T) {
	subject, body := splitMemorySubject("edit_file: object has no attribute 'print_diff'")
	if subject != "edit_file" {
		t.Errorf("subject = %q, want edit_file", subject)
	}
	if strings.HasPrefix(body, ":") || strings.Contains(body, "edit_file") {
		t.Errorf("body still carries the subject: %q", body)
	}
}

// An ordinary sentence that happens to contain a colon is not a subject line;
// splitting it would colour half a sentence as if it were a tool name.
func TestProseIsNotMistakenForASubject(t *testing.T) {
	for _, content := range []string{
		"the user said: use tabs",
		"no colon here at all",
		"a very long leading clause that runs well past the identifier limit: value",
	} {
		if subject, _ := splitMemorySubject(content); subject != "" {
			t.Errorf("%q was split on a subject %q", content, subject)
		}
	}
}

func TestLongPathsAreElidedToTheirIdentifyingTail(t *testing.T) {
	long := `C:\Users\14255\AppData\Local\Temp\gaia-codebench-1dg42j1q\edit-refactor\report.py`
	got := elideMemoryPaths("File " + long + " changed")

	if strings.Contains(got, "AppData") {
		t.Errorf("path was not elided: %q", got)
	}
	for _, keep := range []string{"gaia-codebench-1dg42j1q", "edit-refactor", "report.py", "…"} {
		if !strings.Contains(got, keep) {
			t.Errorf("elided path lost %q, so it no longer identifies the file: %q", keep, got)
		}
	}
}

// Memory content often stores a path as it was escaped in a Python repr, so the
// separators arrive doubled. Splitting on a single one yielded empty segments
// and the tail rendered as `…\.bin\\autoprefixer` — caught in live data.
func TestPathsWithDoubledSeparatorsElideCleanly(t *testing.T) {
	got := elideMemoryPaths(`'C:\\Users\\14255\\Work\\gaia\\src\\node_modules\\.bin\\autoprefixer'`)

	if strings.Contains(got, `\\`) {
		t.Errorf("doubled separators survived into the elided path: %q", got)
	}
	for _, keep := range []string{"node_modules", ".bin", "autoprefixer"} {
		if !strings.Contains(got, keep) {
			t.Errorf("elided path lost %q: %q", keep, got)
		}
	}
}

func TestShortPathsAreLeftAlone(t *testing.T) {
	for _, s := range []string{"src/gaia/cli.py", "a/b/c", "no path here"} {
		if got := elideMemoryPaths(s); got != s {
			t.Errorf("elideMemoryPaths(%q) = %q, want it untouched", s, got)
		}
	}
}

// A wide terminal must not turn every entry into one long scan line.
func TestProseIsSetToAReadableMeasure(t *testing.T) {
	dump := sampleMemoryDump()
	dump.Items = []client.MemoryItem{{
		ID: "1", Category: "note", Confidence: 0.5,
		Content: strings.TrimSpace(strings.Repeat("alpha bravo charlie delta echo ", 20)),
	}}

	for _, line := range strings.Split(ansi.Strip(renderMemoryView(dump, 200)), "\n") {
		if !strings.Contains(line, "alpha") {
			continue
		}
		if got := ansi.StringWidth(line); got > memoryMeasure+len(memoryIndent) {
			t.Fatalf("prose line is %d columns wide in a 200-column pane: %q", got, line)
		}
	}
}

// The width guarantee has to survive a terminal narrower than the gutter and
// measure assume — this is where a naive layout starts shearing.
func TestMemoryViewSurvivesVeryNarrowPanes(t *testing.T) {
	for _, width := range []int{1, 3, 8, 12, 20, 24} {
		out := ansi.Strip(renderMemoryView(sampleMemoryDump(), width))
		for _, line := range strings.Split(out, "\n") {
			if got := ansi.StringWidth(line); got > width {
				t.Errorf("width %d: line overflows at %d columns: %q", width, got, line)
			}
		}
	}
}

// The reason this view exists at all: a plaintext secret was sitting in memory
// with nothing on screen saying so.
func TestSensitiveEntriesAreCalledOutInWords(t *testing.T) {
	out := ansi.Strip(renderMemoryView(sampleMemoryDump(), 88))
	if !strings.Contains(out, "sensitive") {
		t.Error("nothing on screen marks the sensitive entry")
	}
	if !strings.Contains(out, "hunter2") {
		t.Error("the sensitive value stopped being shown; /memory is meant to reveal it")
	}
}
