// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// The whole point of a terminal answer is to use it somewhere else — paste the
// command, keep the summary, mail the list. In an alt-screen app the terminal's
// own selection is fighting the mouse-motion reporting the transcript needs to
// scroll, so "just select it" is exactly the thing that does not work here.
//
// OSC 52 asks the TERMINAL to set the clipboard, which is what makes this work
// unchanged over SSH — no cgo, no X11 socket, no pbcopy/xclip/wl-copy to shell
// out to and no platform matrix to keep alive. The cost is that support is not
// universal and, by design, nothing reports back: the escape is write-only, so
// a terminal that ignores it does so silently. Hence copyHint below, which
// promises only what was attempted.
//
// Known non-support at time of writing: Windows Console Host (conhost) and the
// older mintty builds. Windows Terminal, iTerm2, kitty, WezTerm, Alacritty,
// foot and tmux (with set-clipboard on) all handle it.
type clipboardResultMsg struct {
	label string
	err   error
}

// copyToClipboard emits the OSC 52 sequence on stdout.
//
// Writing straight to os.Stdout under Bubble Tea is safe ONLY because this
// sequence draws nothing and moves nothing: it sets a terminal property and
// emits no cells, so it cannot corrupt the frame Bubble Tea is composing. Do
// not copy this pattern for anything that renders — that has to go through the
// model's View.
func copyToClipboard(text, label string) tea.Cmd {
	return func() tea.Msg {
		if strings.TrimSpace(text) == "" {
			return clipboardResultMsg{label: label, err: fmt.Errorf("nothing to copy")}
		}
		if _, err := os.Stdout.WriteString(ansi.SetClipboard(ansi.SystemClipboard, text)); err != nil {
			return clipboardResultMsg{label: label, err: err}
		}
		return clipboardResultMsg{label: label}
	}
}

// lastAnswer is the most recent thing the agent said, in its source form —
// the markdown, not the ANSI-rendered copy. Pasting escape codes into an
// editor is never what anyone meant by "copy the answer".
func (m ChatModel) lastAnswer() string {
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleAssistant {
			return m.messages[i].Content
		}
	}
	return ""
}

// lastCodeBlock is the last fenced block in the most recent answer — the thing
// a person copies far more often than the prose around it.
//
// Deliberately a scan for ``` rather than a markdown parse: the answer is
// already known-good markdown, and the failure mode of a parser here (drop the
// block, copy nothing) is worse than the failure mode of the scan (copy one
// block too many). An unterminated fence — a streamed answer cut off
// mid-block — yields everything after it, which is the useful reading.
func lastCodeBlock(markdown string) string {
	lines := strings.Split(markdown, "\n")

	end := -1
	for i := len(lines) - 1; i >= 0; i-- {
		if !strings.HasPrefix(strings.TrimSpace(lines[i]), "```") {
			continue
		}
		if end < 0 {
			end = i
			continue
		}
		return strings.Join(lines[i+1:end], "\n")
	}
	if end >= 0 {
		// A lone fence is an OPENING one whose answer was cut off mid-block —
		// the common case while a reply is still streaming. What follows it is
		// the code; what precedes it is the prose that introduced it.
		return strings.Join(lines[end+1:], "\n")
	}
	return ""
}

// copyHint is what the status line says afterwards. It claims the copy was
// SENT, never that it landed: OSC 52 has no reply, so a terminal that drops it
// leaves us no way to know, and "copied!" over an empty clipboard is worse than
// a hedged sentence.
func copyHint(label string, err error) string {
	if err != nil {
		return fmt.Sprintf("could not copy the %s: %v", label, err)
	}
	return fmt.Sprintf("%s sent to the clipboard — if nothing pasted, this terminal does not support OSC 52", label)
}
