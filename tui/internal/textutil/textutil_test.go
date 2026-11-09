// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package textutil

import (
	"strings"
	"testing"
	"unicode/utf8"
)

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

func TestClipCutsOnRunesAndKeepsTheLimit(t *testing.T) {
	long := strings.Repeat("Settings → Connections — reconnect. ", 40)
	got := Clip(long, 7)
	if !utf8.ValidString(got) || strings.Contains(got, "�") {
		t.Errorf("Clip split a multibyte character: %q", got)
	}
	if n := len([]rune(got)); n > 7 || !strings.HasSuffix(got, "…") {
		t.Errorf("Clip(…, 7) = %q (%d runes), want at most 7 ending in …", got, n)
	}
	if got := Clip("short", 7); got != "short" {
		t.Errorf("Clip changed a string under the limit: %q", got)
	}
	if got := Clip("abc def", 5); got != "abc…" {
		t.Errorf("Clip left a space before the ellipsis: %q", got)
	}
	if got := Clip("abc", 0); got != "" {
		t.Errorf("Clip(…, 0) = %q, want empty", got)
	}
}
