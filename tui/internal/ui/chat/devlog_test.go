// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

func TestDevPayload(t *testing.T) {
	for _, tc := range []struct {
		name, in, want string
	}{
		{
			name: "indentation is dropped, the data is kept",
			in:   "{\n  \"path\": \"/tmp/a.txt\",\n  \"limit\": 5\n}",
			want: `{"path":"/tmp/a.txt","limit":5}`,
		},
		{
			// A payload that spans lines would reflow the live region and push
			// the narration it is explaining off the screen.
			name: "an embedded newline never reaches the frame",
			in:   `{"body":"line one\nline two"}`,
			want: `{"body":"line one\nline two"}`,
		},
		{
			name: "an absent payload says nothing",
			in:   "null",
			want: "",
		},
		{
			name: "an empty payload says nothing",
			in:   "",
			want: "",
		},
		{
			// This is a raw-payload view: bytes that do not parse are exactly
			// what the reader is looking for.
			name: "malformed json is shown, not swallowed",
			in:   `{"truncated": `,
			want: `{"truncated":`,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := devPayload(json.RawMessage(tc.in), devPayloadWidth)
			if got != tc.want {
				t.Errorf("devPayload() = %q, want %q", got, tc.want)
			}
			if strings.ContainsAny(got, "\n\r") {
				t.Errorf("devPayload() returned a multi-line string: %q", got)
			}
		})
	}
}

// A long payload must not be able to wrap: the work log budgets its height in
// lines, so one call returning a big blob would shove everything else away.
func TestDevPayloadCannotWrap(t *testing.T) {
	long := `{"data":"` + strings.Repeat("x", 4000) + `"}`
	got := devPayload(json.RawMessage(long), devPayloadWidth)
	if len([]rune(got)) > devPayloadWidth {
		t.Errorf("devPayload() is %d runes, over the %d cap", len([]rune(got)), devPayloadWidth)
	}
}

// The whole point of the split: the same turn shows the arguments and the
// result payload to a developer, and neither to a user.
func TestToolArgsAndOutputAreDevModeOnly(t *testing.T) {
	call := event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "read_file",
		Args:      json.RawMessage(`{"path":"/etc/hosts"}`),
		Narration: "Reading /etc/hosts",
	}
	result := event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "read_file",
		Data: json.RawMessage(`{"bytes":417,"ok":true}`),
	}

	for _, tc := range []struct {
		name    string
		dev     bool
		wantAny bool
	}{
		{"user mode keeps the log to the narration", false, false},
		{"dev mode carries the payloads", true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := NewChatModel(&nullClient{}, "GAIA", "", tc.dev)
			m, _, _ = m.handleCanonicalEvent(call)
			m, _, _ = m.handleCanonicalEvent(result)

			if len(m.activity) != 1 {
				t.Fatalf("got %d activity items, want 1", len(m.activity))
			}
			item := m.activity[0]

			// The narration is what a user reads, and it is present either way.
			if item.Content != "Reading /etc/hosts" {
				t.Errorf("narration = %q; the user-facing line must not change with the mode", item.Content)
			}

			gotAny := item.Args != "" || item.Output != ""
			if gotAny != tc.wantAny {
				t.Errorf("args=%q output=%q; wantAny=%v", item.Args, item.Output, tc.wantAny)
			}
			if tc.wantAny {
				if !strings.Contains(item.Args, "/etc/hosts") {
					t.Errorf("args lost the argument that was passed: %q", item.Args)
				}
				if !strings.Contains(item.Output, "417") {
					t.Errorf("output lost the result payload: %q", item.Output)
				}
			}
		})
	}
}

// Rendering must honour the same split, so a mode that populates nothing also
// draws nothing extra.
func TestDevPayloadsRenderOnlyUnderDev(t *testing.T) {
	item := ActivityItem{
		Kind: "tool", Tool: "read_file",
		Content: "Reading /etc/hosts", Detail: "417 bytes",
		Args: `{"path":"/etc/hosts"}`, Output: `{"bytes":417}`,
	}

	dev := NewChatModel(&nullClient{}, "GAIA", "", true)
	dev.width = 120
	devLines := strings.Join(dev.renderActivityItem(item, false, 0), "\n")
	for _, want := range []string{"Reading /etc/hosts", "417 bytes", "/etc/hosts", "args", "out"} {
		if !strings.Contains(devLines, want) {
			t.Errorf("--dev render lost %q:\n%s", want, devLines)
		}
	}

	// In user mode the fields are never populated, so the same item as a user
	// would ever see it carries no payload at all.
	user := NewChatModel(&nullClient{}, "GAIA", "", false)
	user.width = 120
	bare := item
	bare.Args, bare.Output = "", ""
	userLines := strings.Join(user.renderActivityItem(bare, false, 0), "\n")
	if strings.Contains(userLines, "args") || strings.Contains(userLines, `{"`) {
		t.Errorf("user mode drew a developer payload:\n%s", userLines)
	}
	if !strings.Contains(userLines, "Reading /etc/hosts") {
		t.Errorf("user mode lost the narration it exists to show:\n%s", userLines)
	}
}

func TestStepNumberOf(t *testing.T) {
	for _, tc := range []struct {
		in     string
		want   int
		wantOK bool
	}{
		{"Step 3/50", 3, true},
		{"Step 12/50", 12, true},
		{"Step 7", 7, true},
		{"  Step 4/50  ", 4, true},
		{"Processing with Gemma-4-E4B-it-GGUF", 0, false},
		{"Thinking", 0, false},
		{"Stepping through the results", 0, false},
		{"", 0, false},
	} {
		t.Run(tc.in, func(t *testing.T) {
			got, ok := stepNumberOf(tc.in)
			if got != tc.want || ok != tc.wantOK {
				t.Errorf("stepNumberOf(%q) = (%d, %v), want (%d, %v)", tc.in, got, ok, tc.want, tc.wantOK)
			}
		})
	}
}

// The canonical transport reports steps only in the final event's usage block,
// so a turn that ends without one used to report the PREVIOUS turn's count as
// its own — a number that looks authoritative and describes different work.
func TestTheStepCountDoesNotSurviveIntoTheNextTurn(t *testing.T) {
	m := NewChatModel(&nullClient{}, "GAIA", "", true)

	m, _, _ = m.handleCanonicalEvent(event.CanonicalStatusEvent{Type: "status", Message: "Step 9/50"})
	if m.totalSteps != 9 {
		t.Fatalf("totalSteps = %d after Step 9/50; want 9", m.totalSteps)
	}

	next, _ := m.sendQuery("a new question")
	if got := next.(ChatModel).totalSteps; got != 0 {
		t.Errorf("the new turn opened at step %d; the previous turn's count leaked into it", got)
	}
}
