// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package textutil

import (
	"testing"

	"github.com/charmbracelet/lipgloss"
)

func TestTruncate(t *testing.T) {
	for _, tc := range []struct {
		name string
		s    string
		w    int
		want string
	}{
		{"zero width", "hello", 0, ""},
		{"negative width", "hello", -3, ""},
		{"empty", "", 5, ""},
		{"fits", "hello", 5, "hello"},
		{"cut", "hello world", 8, "hello w…"},
		{"width one", "hello", 1, "…"},
		{"width two", "hello", 2, "h…"},
		{"wide runes", "日本語テキスト", 7, "日本語…"},
		{"wide rune cannot split", "日本語", 4, "日…"},
		{"wide rune at width one", "日本", 1, "…"},
		{"mixed width", "a日b本c", 5, "a日b…"},
		{"combining marks", "éééé", 3, "éé…"},
		{"zwj emoji", "👩‍👩‍👧x👩‍👩‍👧y", 4, "👩‍👩‍👧x…"},
		{"ellipsis input", "…………", 3, "………"},
		{"multiline fits", "one\ntwo", 3, "one\ntwo"},
		{"multiline cuts overflowing line", "line one\nline two longer", 10, "line one\nline two …"},
		{"multiline cuts line exactly w wide", "abcd\nabcdef", 4, "abc…"},
		{"multiline drops lines after cut", "long first line\nb", 6, "long …"},
		{"ansi keeps style closed", "\x1b[1mhello world\x1b[0m", 6, "\x1b[1mhello…\x1b[0m"},
		{"ansi prefix kept whole", "\x1b[36m⠋\x1b[0m downloading model", 8, "\x1b[36m⠋\x1b[0m downl…"},
	} {
		if got := Truncate(tc.s, tc.w); got != tc.want {
			t.Errorf("%s: Truncate(%q, %d) = %q, want %q", tc.name, tc.s, tc.w, got, tc.want)
		}
	}
}

// truncateByRunes is the rune-at-a-time helper the TUI screens used before
// Truncate: the oracle for plain text.
func truncateByRunes(s string, w int) string {
	if w <= 0 {
		return ""
	}
	if lipgloss.Width(s) <= w {
		return s
	}
	r := []rune(s)
	for len(r) > 0 && lipgloss.Width(string(r))+1 > w {
		r = r[:len(r)-1]
	}
	return string(r) + "…"
}

func TestTruncateMatchesRuneByRune(t *testing.T) {
	for _, s := range []string{
		"", "a", "hello world", "日本語テキスト", "a日b本c", "ééé",
		"👩‍👩‍👧x👩‍👩‍👧y", "🇺🇸🇬🇧🇫🇷", "tab\there", "line one\nline two longer",
		"abcd\nab\nabcdef", "\nlead", "trail\n", "日本\n語テキスト",
	} {
		for w := -1; w <= 16; w++ {
			if got, want := Truncate(s, w), truncateByRunes(s, w); got != want {
				t.Errorf("Truncate(%q, %d) = %q, rune-by-rune gives %q", s, w, got, want)
			}
		}
	}
}
