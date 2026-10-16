// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

// A command shown under Ctrl+O is RAW model output — whatever a web page, a
// file or an email put in front of the model. clean() drops C0 and ANSI but not
// these two classes, and both reach the terminal.
func TestCommandsShownOnScreenDropControlsThatSurviveClean(t *testing.T) {
	for _, tc := range []struct {
		name string
		bad  string
	}{
		{"C1 CSI", "\u009b"},
		{"C1 NEL", "\u0085"},
		// Escapes, not literals — bidichk (rightly) refuses the raw chars.
		{"bidi override", "\u202e"},
		{"bidi isolate", "\u2066"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			args, err := json.Marshal(map[string]string{
				"command":     "echo a" + tc.bad + "b",
				"description": "Say a" + tc.bad + "b",
			})
			if err != nil {
				t.Fatalf("marshal: %v", err)
			}
			info := describeCall("run_shell_command", args, "")
			for field, got := range map[string]string{"command": info.Command, "description": info.Description} {
				if strings.Contains(got, tc.bad) {
					t.Errorf("%s passed %q through to the frame: %q", field, tc.bad, got)
				}
				// A scrub, not a drop: the text around it survives.
				if !strings.Contains(got, "ab") {
					t.Errorf("%s lost the text around the control: %q", field, got)
				}
			}
		})
	}
}

// There is one screen. No mode, folded or expanded, live or finished, prints a
// tool's raw arguments or its raw result.
func TestNoInlineJSONInAnyView(t *testing.T) {
	m := NewChatModel(&nullClient{}, "GAIA", "")
	m.width, m.height = 120, 40
	m.streaming = true
	m.queryStart = time.Now()
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "run_python",
		Args: json.RawMessage(`{"code":"import json\nprint(json.dumps({\"k\": 1}))"}`),
	})
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "run_python",
		Data: json.RawMessage(`{"summary":"success","success":true,"latency_ms":1047.0}`),
	})

	for _, expanded := range []bool{false, true} {
		m.expandWork = expanded
		live := m.renderLiveRegion()
		finished := m.renderWork(&Message{Role: RoleWork, Work: m.activity})
		for name, out := range map[string]string{"live": live, "finished": finished} {
			for _, bad := range []string{`{"summary"`, `"success":true`, "latency_ms", "args {", "out {"} {
				if strings.Contains(out, bad) {
					t.Errorf("expanded=%v %s view printed %q:\n%s", expanded, name, bad, out)
				}
			}
		}
	}
}

// Two calls open at once: each result must land on the call it came from.
func TestOverlappingToolCallsKeepTheirOwnOutcomes(t *testing.T) {
	m := NewChatModel(&nullClient{}, "GAIA", "")

	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "search", Narration: "Searching",
		Args: json.RawMessage(`{"q":"first"}`),
	})
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "run_shell_command",
		Args: json.RawMessage(`{"command":"ls"}`),
	})
	// setOpenToolOutcome closes the newest open line, so the shell call
	// resolves first here.
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "run_shell_command",
		Data: json.RawMessage(`{"stdout":"a\nb\nc"}`),
	})
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "search", Data: json.RawMessage(`{"summary":"7 hits"}`),
	})

	if len(m.activity) != 2 {
		t.Fatalf("got %d activity items, want 2", len(m.activity))
	}
	if got := m.activity[1].OutLines; got != 3 {
		t.Errorf("the shell call reports %d output lines, want its own 3", got)
	}
	if got := m.activity[0].OutLines; got != 0 {
		t.Errorf("the search call reports %d output lines — it took the shell call's", got)
	}
	if got := m.activity[0].Detail; !strings.Contains(got, "7 hits") {
		t.Errorf("the search call's outcome = %q, want its own", got)
	}
}

// A model that streams its reasoning inline must not have it shown as the
// answer or as narration: the status line says it is reasoning.
func TestRawReasoningNeverReachesTheScreen(t *testing.T) {
	for in, want := range map[string]string{
		"<think>weigh the clauses</think>The register is ready.": "The register is ready.",
		"weigh the clauses</think>The register is ready.":        "The register is ready.",
		"Intro. <think>still going":                              "Intro.",
		"<think>a</think>One. <think>b</think>Two.":              "One. Two.",
		"No reasoning here.":                                     "No reasoning here.",
		"<think>unfinished":                                      "",
	} {
		if got := withoutThinking(in); got != want {
			t.Errorf("withoutThinking(%q) = %q, want %q", in, got, want)
		}
	}
}
