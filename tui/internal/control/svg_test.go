// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package control

import (
	"encoding/xml"
	"strings"
	"testing"
)

// A picture nobody can open is not evidence. Every rendering path has to
// produce a document a parser accepts.
func wellFormed(t *testing.T, doc string) {
	t.Helper()
	d := xml.NewDecoder(strings.NewReader(doc))
	for {
		_, err := d.Token()
		if err != nil {
			if err.Error() == "EOF" {
				return
			}
			t.Fatalf("not well-formed XML: %v\n%.400s", err, doc)
		}
	}
}

func TestScreenSVGIsWellFormed(t *testing.T) {
	frame := "\x1b[1;38;2;181;224;141mGAIA\x1b[0m │ dev\n──────\n  hello"
	doc := ScreenSVG(frame, 40, 3)
	wellFormed(t, doc)
	if !strings.Contains(doc, "GAIA") {
		t.Error("the frame's text never reached the picture")
	}
	if !strings.Contains(doc, "#b5e08d") {
		t.Errorf("the header's truecolor was dropped:\n%.300s", doc)
	}
}

// Markup that would break the document if it were copied through verbatim.
func TestScreenSVGEscapesMarkupInTheFrame(t *testing.T) {
	doc := ScreenSVG(`a <b> & "c"`, 20, 1)
	wellFormed(t, doc)
	if strings.Contains(doc, "<b>") {
		t.Error("a tag in the transcript was emitted as markup")
	}
}

// A hyperlink is zero-width and carries a URI that is not meant to be drawn.
func TestScreenSVGDoesNotDrawHyperlinkSequences(t *testing.T) {
	frame := "see \x1b]8;;https://example.com/x\x1b\\link\x1b]8;;\x1b\\ here"
	doc := ScreenSVG(frame, 30, 1)
	wellFormed(t, doc)
	if strings.Contains(doc, "example.com") {
		t.Errorf("the link's URI was drawn as text:\n%.400s", doc)
	}
	if !strings.Contains(doc, "link") {
		t.Error("the link's label is missing")
	}
}

func TestScreenSVGHandles256AndBasicColors(t *testing.T) {
	doc := ScreenSVG("\x1b[38;5;33mblue\x1b[0m \x1b[31mred\x1b[0m", 20, 1)
	wellFormed(t, doc)
	for _, want := range []string{"#0087ff", "#cd3131"} {
		if !strings.Contains(doc, want) {
			t.Errorf("missing colour %s:\n%.400s", want, doc)
		}
	}
}

func TestRecordingSVGPlaysEveryFrame(t *testing.T) {
	frames := []Frame{
		{Seq: 1, AtMS: 0, Screen: "first"},
		{Seq: 2, AtMS: 250, Screen: "second"},
		{Seq: 3, AtMS: 700, Screen: "third"},
	}
	doc := RecordingSVG(frames, 20, 1)
	wellFormed(t, doc)
	for _, want := range []string{"first", "second", "third"} {
		if !strings.Contains(doc, want) {
			t.Errorf("frame %q is missing from the replay", want)
		}
	}
	for _, want := range []string{"@keyframes f0", "@keyframes f1", "@keyframes f2"} {
		if !strings.Contains(doc, want) {
			t.Errorf("no timeline for %s", want)
		}
	}
	// Real time, plus the tail that holds the last frame.
	if !strings.Contains(doc, "animation-duration:2.200s") {
		t.Errorf("the replay does not run at the speed it was recorded:\n%.300s", doc)
	}
}

// One frame is a still, not a one-frame animation nobody can see.
func TestRecordingSVGWithOneFrameIsAStill(t *testing.T) {
	doc := RecordingSVG([]Frame{{Seq: 1, Screen: "only"}}, 10, 1)
	wellFormed(t, doc)
	if strings.Contains(doc, "@keyframes") {
		t.Error("a single frame was animated")
	}
}
