// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import (
	"strings"
	"testing"
	"unicode/utf8"
)

func TestSequenceBytes(t *testing.T) {
	cases := []struct {
		style Style
		want  []byte
	}{
		{StyleBell, []byte{0x07}},
		{StyleOSC9, []byte("\x1b]9;Title: Body\x07")},
		{StyleOSC777, []byte("\x1b]777;notify;Title;Body\x07")},
		{StyleOSC777Bell, []byte("\x1b]777;notify;Title;Body\x07\x07")},
	}
	for _, tc := range cases {
		got := []byte(Sequence(tc.style, "Title", "Body"))
		if string(got) != string(tc.want) {
			t.Errorf("%s: got % x, want % x", tc.style, got, tc.want)
		}
	}
}

func TestSequenceCannotEscapeTheOSC(t *testing.T) {
	// A tool summary is the agent's text: it must not end the sequence early,
	// start a new one, or add an OSC 777 field.
	hostile := "a\x1b]0;pwned\x07b;c\u009b31m\nd\x00e"
	got := Sequence(StyleOSC777, "T;x", hostile)
	want := "\x1b]777;notify;T,x;a]0,pwnedb,c31m de\x07"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if strings.Count(got, "\x1b") != 1 || strings.Count(got, "\x07") != 1 {
		t.Fatalf("control bytes leaked: %q", got)
	}
}

func TestOSC9NeverLooksLikeAConEmuCommand(t *testing.T) {
	// `ESC ] 9 ; 4 ; ...` is ConEmu's progress bar, not a message.
	got := Sequence(StyleOSC9, "4", "1;2")
	if strings.HasPrefix(got, "\x1b]9;4;") {
		t.Fatalf("got %q", got)
	}
}

func TestSequenceTruncatesLongBodies(t *testing.T) {
	got := Sequence(StyleOSC777, "T", strings.Repeat("x", 500))
	body := strings.TrimSuffix(strings.TrimPrefix(got, "\x1b]777;notify;T;"), "\x07")
	if n := utf8.RuneCountInString(body); n != maxBody || !strings.HasSuffix(body, "…") {
		t.Fatalf("body is %d runes: %q", n, body)
	}
}

func TestDetectStyle(t *testing.T) {
	cases := []struct {
		env  map[string]string
		want Style
	}{
		{map[string]string{}, StyleBell},
		{map[string]string{"WT_SESSION": "x"}, StyleOSC777Bell},
		{map[string]string{"WT_SESSION": "x", "TMUX": "/tmp/t"}, StyleBell},
		{map[string]string{"TERM_PROGRAM": "iTerm.app"}, StyleOSC9},
		{map[string]string{"TERM_PROGRAM": "ghostty"}, StyleOSC777},
		{map[string]string{"TERM_PROGRAM": "WezTerm"}, StyleOSC777},
		{map[string]string{"TERM": "foot"}, StyleOSC777},
		{map[string]string{"TERM": "rxvt-unicode-256color"}, StyleOSC777},
		{map[string]string{"TERM_PROGRAM": "Apple_Terminal"}, StyleBell},
		{map[string]string{"TERM_PROGRAM": "ghostty", "STY": "1.pts"}, StyleBell},
	}
	for _, tc := range cases {
		got := DetectStyle(func(k string) string { return tc.env[k] })
		if got != tc.want {
			t.Errorf("%v: got %s, want %s", tc.env, got, tc.want)
		}
	}
}
