// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package textutil

import (
	"reflect"
	"testing"
)

func TestWrap(t *testing.T) {
	for _, tc := range []struct {
		name string
		in   string
		w    int
		want []string
	}{
		{"empty is one empty line", "", 20, []string{""}},
		{"fits on one line", "run gaia init", 20, []string{"run gaia init"}},
		{"breaks on words", "one two three four", 9, []string{"one two", "three", "four"}},
		{"keeps paragraphs", "first line\nsecond", 20, []string{"first line", "second"}},
		{"collapses runs of spaces", "a   b", 20, []string{"a b"}},
		{"cuts a token longer than the width", "abcdefghijkl", 8, []string{"abcdefgh", "ijkl"}},
		{"flushes the line before a long token", "go abcdefghijkl", 8, []string{"go", "abcdefgh", "ijkl"}},
		{"width below 8 is raised to 8", "abcdefghij", 3, []string{"abcdefgh", "ij"}},
		{"counts wide runes by column", "日本語日本語", 8, []string{"日本語日", "本語"}},
	} {
		if got := Wrap(tc.in, tc.w); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%s: Wrap(%q, %d) = %q, want %q", tc.name, tc.in, tc.w, got, tc.want)
		}
	}
}

func TestFormatGB(t *testing.T) {
	for gb, want := range map[float64]string{
		23.3:   "23.3 GB",
		1:      "1.0 GB",
		63.6:   "63.6 GB",
		0.33:   "330 MB",
		0.5:    "500 MB",
		0.0001: "1 MB",
		0:      "0 MB",
		-2:     "0 MB",
	} {
		if got := FormatGB(gb); got != want {
			t.Errorf("FormatGB(%v) = %q, want %q", gb, got, want)
		}
	}
}

func TestLastLine(t *testing.T) {
	for in, want := range map[string]string{
		"Traceback\n  File x\nValueError: bad\n\n": "ValueError: bad",
		"one line":       "one line",
		"a\r\n  b  \r\n": "b",
		"":               "(no output)",
		" \n\t\n":        "(no output)",
	} {
		if got := LastLine(in); got != want {
			t.Errorf("LastLine(%q) = %q, want %q", in, got, want)
		}
	}
}
