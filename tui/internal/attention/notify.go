// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import (
	"strings"
	"unicode/utf8"
)

// Style is how a notification reaches the desktop through the terminal.
type Style int

const (
	// StyleBell rings the terminal bell. Every terminal understands it; what
	// it does (a sound, a flashing taskbar) is the terminal's own setting.
	StyleBell Style = iota
	// StyleOSC9 is `ESC ] 9 ; text BEL`: message only.
	StyleOSC9
	// StyleOSC777 is `ESC ] 777 ; notify ; title ; body BEL`.
	StyleOSC777
	// StyleOSC777Bell sends OSC 777 and then the bell. Windows Terminal shows
	// OSC 777 only from 1.26 and only once the user opts in
	// (compatibility.allowOSC777), and ignores it otherwise; the bell is what
	// reaches everyone else.
	StyleOSC777Bell
)

func (s Style) String() string {
	switch s {
	case StyleOSC9:
		return "osc9"
	case StyleOSC777:
		return "osc777"
	case StyleOSC777Bell:
		return "osc777+bell"
	}
	return "bell"
}

// DetectStyle picks the richest notification the terminal is known to show.
// An unknown terminal gets the bell: an OSC it does not understand can be
// printed as text by an old one.
func DetectStyle(getenv func(string) string) Style {
	// tmux and screen swallow OSC notifications unless passthrough is
	// configured, but forward the bell to the outer terminal.
	if getenv("TMUX") != "" || getenv("STY") != "" {
		return StyleBell
	}
	if getenv("WT_SESSION") != "" {
		return StyleOSC777Bell
	}
	switch getenv("TERM_PROGRAM") {
	case "iTerm.app":
		return StyleOSC9
	case "ghostty", "WezTerm":
		return StyleOSC777
	}
	term := getenv("TERM")
	if term == "foot" || strings.HasPrefix(term, "foot-") || strings.HasPrefix(term, "rxvt-unicode") {
		return StyleOSC777
	}
	return StyleBell
}

const (
	bel = "\a"
	// maxBody keeps a toast to a glance; a long tool summary is cut.
	maxBody = 160
)

// Sequence is the exact bytes that raise one notification.
func Sequence(style Style, title, body string) string {
	title, body = clean(title), clean(body)
	if utf8.RuneCountInString(body) > maxBody {
		body = string([]rune(body)[:maxBody-1]) + "…"
	}
	switch style {
	case StyleOSC9:
		text := title
		if body != "" {
			text += ": " + body
		}
		return "\x1b]9;" + text + bel
	case StyleOSC777:
		return osc777(title, body)
	case StyleOSC777Bell:
		return osc777(title, body) + bel
	}
	return bel
}

func osc777(title, body string) string {
	return "\x1b]777;notify;" + title + ";" + body + bel
}

// clean makes text safe inside an OSC string: no control characters (one
// would end the sequence early or start another), and no ';', which separates
// fields in OSC 777 and turns `9;<digit>;` into a different ConEmu command.
func clean(s string) string {
	var b strings.Builder
	for _, r := range s {
		switch {
		case r == '\n' || r == '\r' || r == '\t':
			b.WriteByte(' ')
		case r < 0x20 || r == 0x7f || (r >= 0x80 && r <= 0x9f):
		case r == ';':
			b.WriteByte(',')
		default:
			b.WriteRune(r)
		}
	}
	return strings.Join(strings.Fields(b.String()), " ")
}
