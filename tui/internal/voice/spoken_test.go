// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package voice

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The cases every implementation of the split must agree on — Python, the
// webui and this one read the same file.
var casesPath = filepath.Join("..", "..", "..", "tests", "fixtures", "voice", "spoken_cases.json")

type spokenCase struct {
	Name    string            `json:"name"`
	Stream  []json.RawMessage `json:"stream"`
	Spoken  string            `json:"spoken"`
	Detail  string            `json:"detail"`
	Missing bool              `json:"missing"`
}

func loadCases(t *testing.T) []spokenCase {
	t.Helper()
	raw, err := os.ReadFile(casesPath)
	if err != nil {
		t.Fatalf("read the shared spoken cases: %v", err)
	}
	var file struct {
		Cases []spokenCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatalf("parse %s: %v", casesPath, err)
	}
	if len(file.Cases) == 0 {
		t.Fatalf("%s has no cases", casesPath)
	}
	return file.Cases
}

func runCase(t *testing.T, c spokenCase, byChar bool) (string, string, bool) {
	t.Helper()
	var s SpokenSplitter
	var detail []string
	collect := func(pieces []Piece) {
		for _, p := range pieces {
			if p.Channel == Detail {
				detail = append(detail, p.Text)
			}
		}
	}
	for _, item := range c.Stream {
		var text string
		if err := json.Unmarshal(item, &text); err != nil {
			var marker struct {
				Reset bool `json:"reset"`
			}
			if err := json.Unmarshal(item, &marker); err != nil || !marker.Reset {
				t.Fatalf("case %q: stream item %s is neither text nor a reset", c.Name, item)
			}
			s.Reset()
			detail = nil
			continue
		}
		if byChar {
			for _, r := range text {
				collect(s.Feed(string(r)))
			}
		} else {
			collect(s.Feed(text))
		}
	}
	collect(s.Finish())
	return s.SpokenText(), strings.TrimSpace(strings.Join(detail, "")), s.Missing()
}

func TestSharedSpokenCases(t *testing.T) {
	for _, c := range loadCases(t) {
		for _, byChar := range []bool{false, true} {
			spoken, detail, missing := runCase(t, c, byChar)
			if spoken != c.Spoken || detail != c.Detail || missing != c.Missing {
				t.Errorf("case %q (one char at a time: %t): got (%q, %q, %t), want (%q, %q, %t)",
					c.Name, byChar, spoken, detail, missing, c.Spoken, c.Detail, c.Missing)
			}
		}
	}
}

func TestSpokenNeverLeaksATagFragment(t *testing.T) {
	var s SpokenSplitter
	pieces := s.Feed("<spoken>Hi there</spo")
	for _, p := range pieces {
		if strings.Contains(p.Text, "<") {
			t.Fatalf("piece %q leaked part of the closing tag", p.Text)
		}
	}
	pieces = append(pieces, s.Feed("ken>rest")...)
	if s.SpokenText() != "Hi there" {
		t.Fatalf("spoken = %q", s.SpokenText())
	}
	if last := pieces[len(pieces)-1]; last.Channel != Detail || last.Text != "rest" {
		t.Fatalf("last piece = %+v", last)
	}
}

func TestDisplayPartsLeavesPlainAnswersAlone(t *testing.T) {
	lead, detail := DisplayParts("Plain **answer**.")
	if lead != "" || detail != "Plain **answer**." {
		t.Fatalf("got (%q, %q)", lead, detail)
	}
	lead, detail = DisplayParts("<spoken>Two files.</spoken>\n\nThey are **here**.")
	if lead != "Two files." || detail != "They are **here**." {
		t.Fatalf("got (%q, %q)", lead, detail)
	}
}
