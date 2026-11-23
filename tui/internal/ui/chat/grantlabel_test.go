// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// The "always" choice of a multi-line command: gaiacpp sends the grant with
// each line break as [U+000A], and the label repeated all 49 lines of it.
func TestAMultiLineCommandsAlwaysChoiceShowsItsFirstLine(t *testing.T) {
	lines := []string{"python3 -c 'import os", "total = 0"}
	for i := 0; i < 40; i++ {
		lines = append(lines, "x = 1 # step")
	}
	lines = append(lines, "print(total)'")
	command := strings.Join(lines, "\n")

	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, event.CanonicalNeedsConfirmationEvent{
		Type: "needs_confirmation", RunID: "run-1", Action: "run_shell_command",
		Summary: command, ConfirmID: "cid-1", Risk: "execute",
		AlwaysScope: "Run `" + strings.Join(lines, "[U+000A]") + "`",
	})

	frame := plainFrame(m)
	t.Logf("\n%s", frame)
	if strings.Contains(frame, "U+000A") {
		t.Error("the always choice shows line breaks as [U+000A]")
	}
	if !strings.Contains(frame, "a always: Run `python3 -c 'import os … (this session)") {
		t.Error("the always choice does not name the command by its first line")
	}
	if n := strings.Count(frame, "total = 0"); n != 1 {
		t.Errorf("the command's second line is painted %d times, want once (on the card)", n)
	}
}

// Over the daemon, gaiacpp's always_scope is its own key and always_label its
// wording; the key is not something to show anyone.
func TestTheAlwaysChoiceUsesTheAgentsWordingOverItsKey(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, event.CanonicalNeedsConfirmationEvent{
		Type: "needs_confirmation", RunID: "run-1", Action: "run_shell_command",
		Summary: "ps -o pid -p 1", ConfirmID: "cid-1", Risk: "execute",
		AlwaysScope: "bash_execute:exact:ps -o pid -p 1",
		AlwaysLabel: "Run `ps -o pid -p 1`",
	})

	frame := plainFrame(m)
	if strings.Contains(frame, "bash_execute:exact") {
		t.Errorf("the always choice shows the agent's internal key:\n%s", frame)
	}
	if !strings.Contains(frame, "a always: Run `ps -o pid -p 1` (this session)") {
		t.Errorf("the always choice does not use the agent's wording:\n%s", frame)
	}
}

func TestGrantShown(t *testing.T) {
	for _, c := range []struct{ label, scope, want string }{
		{"", "pytest", "pytest"},
		{"Run `gh issue list`", "bash_execute:exact:gh issue list", "Run `gh issue list`"},
		{"", "Run `a  [U+000A]b`", "Run `a …"},
		{"Run `a\nb\nc`", "", "Run `a …"},
	} {
		if got := grantShown(c.label, c.scope); got != c.want {
			t.Errorf("grantShown(%q, %q) = %q, want %q", c.label, c.scope, got, c.want)
		}
	}
}
