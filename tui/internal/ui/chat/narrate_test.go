// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

func TestToolNarration(t *testing.T) {
	for _, tc := range []struct {
		name      string
		tool      string
		args      string
		narration string
		want      string
	}{
		{"sidecar narration wins", "load_skill", `{"name":"x"}`, "Reading issue #2924", "Reading issue #2924"},
		{"curated phrase", "list_skills", `{}`, "", "Checking your installed skills"},
		{"curated with argument", "load_skill", `{"name":"github-triage"}`, "", "Loading the github-triage skill"},
		{"shell shows the command", "run_shell_command", `{"command":"gh issue view 2924"}`, "", "gh issue view 2924"},
		{"derived verb + object", "unload_skill_set", `{}`, "", "Unloading skill set"},
		{"derived with argument", "fetch_report", `{"name":"q3"}`, "", "Fetching report: q3"},
		{"unknown verb stays honest", "pre_scan_inbox2", `{}`, "", "Running pre_scan_inbox2"},
		{"no args at all", "read_file", ``, "", "Reading a file"},
		{"numeric id is not scientific", "get_message", `{"message_id":2924}`, "", "Opening message 2924"},
		{"nested args are not pasted as JSON", "index_document", `{"opts":{"deep":true}}`, "", "Indexing a document"},
		// The tool name reaches a Sprintf FORMAT string, so an unescaped "%" used
		// to come out as "Getting 100%!d(string=arg)one".
		{"a percent is not a format verb", "get_100%_done", `{}`, "", "Getting 100% done"},
		{"...on the fallback path too", "zzz_100%_x", `{}`, "", "Running zzz_100%_x"},
		{"...and with an argument", "get_100%_done", `{"name":"q3"}`, "", "Getting 100% done: q3"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := toolNarration(tc.tool, json.RawMessage(tc.args), tc.narration)
			if got != tc.want {
				t.Errorf("toolNarration(%q, %s) = %q, want %q", tc.tool, tc.args, got, tc.want)
			}
		})
	}
}

func TestToolResultDetail(t *testing.T) {
	for _, tc := range []struct {
		name string
		e    event.CanonicalToolResultEvent
		want string
	}{
		{"sidecar preview wins",
			event.CanonicalToolResultEvent{Preview: "3.7 KB returned", Data: []byte(`{"summary":"ignored"}`)},
			"3.7 KB returned"},
		{"summary and latency",
			event.CanonicalToolResultEvent{Data: []byte(`{"summary":"18 skills","latency_ms":20.7}`)},
			"18 skills · 21ms"},
		{"a bare status word is not an outcome",
			event.CanonicalToolResultEvent{Data: []byte(`{"summary":"success","latency_ms":1500}`)},
			"1.5s"},
		{"counts come from the payload's own noun",
			event.CanonicalToolResultEvent{Data: []byte(`{"files":[1,2,3]}`)},
			"3 files"},
		{"one of something is singular",
			event.CanonicalToolResultEvent{Data: []byte(`{"entries":[1]}`)},
			"1 entry"},
		{"one match is not one matche",
			event.CanonicalToolResultEvent{Data: []byte(`{"matches":[1]}`)},
			"1 match"},
		{"failure says so in words",
			event.CanonicalToolResultEvent{Data: []byte(`{"ok":false,"summary":"no such file"}`)},
			"failed — no such file"},
		// The bug this table exists for: a preview short-circuited ahead of the
		// failure check, leaving red as the only signal.
		{"failure survives a preview",
			event.CanonicalToolResultEvent{Preview: "deleted 3 files", Data: []byte(`{"ok":false,"preview":"deleted 3 files"}`)},
			"failed — deleted 3 files"},
		{"an empty result still proves it arrived",
			event.CanonicalToolResultEvent{Data: []byte(`{}`)},
			"done"},
		{"a failed empty result too",
			event.CanonicalToolResultEvent{Data: []byte(`{"ok":false}`)},
			"failed"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := toolResultDetail(tc.e); got != tc.want {
				t.Errorf("toolResultDetail = %q, want %q", got, tc.want)
			}
		})
	}
}

// Every string here is agent-supplied and lands in a bare lipgloss render, so it
// gets the same scrub the card path applies: an escape sequence left live moves
// the cursor and shears the layout.
func TestAgentTextCannotCarryEscapesIntoTheLog(t *testing.T) {
	got := toolNarration("run_shell_command",
		json.RawMessage(`{"command":"echo [31mred[0m\thi\nthere"}`), "")
	if strings.ContainsRune(got, 0x1b) {
		t.Errorf("an ANSI escape reached the work log: %q", got)
	}
	for _, r := range got {
		if r < 0x20 || r == 0x7f {
			t.Errorf("a control byte reached the work log: %q", got)
		}
	}
	if strings.Contains(got, "\n") {
		t.Errorf("a newline reached a one-row log line: %q", got)
	}
}

// Width is a COLUMN budget, not a rune count: 74 double-width runes occupy 148
// columns and silently double the live region's height.
func TestTruncationCountsColumnsNotRunes(t *testing.T) {
	wide := strings.Repeat("世", 60) // 120 columns
	got := truncateRunes(wide, 20)
	if w := displayWidth(got); w > 20 {
		t.Errorf("truncated to %d columns, want <= 20 (%q)", w, got)
	}
	if !strings.HasSuffix(got, "…") {
		t.Errorf("a truncated string must say it was cut: %q", got)
	}
	if short := truncateRunes("fits", 20); short != "fits" {
		t.Errorf("a string within budget was altered: %q", short)
	}
}
