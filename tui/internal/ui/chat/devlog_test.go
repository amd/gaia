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

// The dev payload renders RAW tool output — whatever a web page, a file or an
// email put in front of the tool. clean() drops C0 and ESC-introduced ANSI but
// not these two classes, and both reach the terminal.
func TestDevPayloadDropsControlsThatSurviveClean(t *testing.T) {
	for _, tc := range []struct {
		name string
		bad  string
	}{
		{"C1 CSI", ""},
		{"C1 NEL", ""},
		{"bidi override", "‮"}, //nolint:bidichk // test data: the exact Trojan-Source char clean() must scrub
		{"bidi isolate", "⁦"},  //nolint:bidichk // test data: the exact Trojan-Source char clean() must scrub
	} {
		t.Run(tc.name, func(t *testing.T) {
			in, err := json.Marshal(map[string]string{"x": "a" + tc.bad + "b"})
			if err != nil {
				t.Fatalf("marshal: %v", err)
			}
			got := devPayload(json.RawMessage(in), devPayloadWidth)
			if strings.Contains(got, tc.bad) {
				t.Errorf("devPayload passed %q through to the frame: %q", tc.bad, got)
			}
			// The surrounding data must survive — this is a scrub, not a drop.
			if !strings.Contains(got, "ab") {
				t.Errorf("devPayload lost the payload around the control: %q", got)
			}
		})
	}
}

// An empty payload arrives in several shapes. "out {}" under every result is
// the noise that makes a developer stop reading the log.
func TestDevPayloadTreatsEveryEmptyShapeAsNothing(t *testing.T) {
	for _, in := range []string{"null", "{}", "[]", `""`, "  {}  "} {
		if got := devPayload(json.RawMessage(in), devPayloadWidth); got != "" {
			t.Errorf("devPayload(%q) = %q, want empty", in, got)
		}
	}
}

// Two calls open at once: each payload must land under the call it came from.
// Searching back for "the last tool line without output" gets this wrong the
// moment the two predicates disagree.
func TestOverlappingToolCallsKeepTheirOwnPayloads(t *testing.T) {
	m := NewChatModel(&nullClient{}, "GAIA", "", true)

	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "search", Narration: "Searching",
		Args: json.RawMessage(`{"q":"first"}`),
	})
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "fetch", Narration: "Fetching",
		Args: json.RawMessage(`{"url":"second"}`),
	})

	// Results arrive in the order the calls close: setOpenToolOutcome closes
	// the newest still-open line, so "fetch" resolves first here.
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "fetch", Data: json.RawMessage(`{"got":"SECOND"}`),
	})
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "search", Data: json.RawMessage(`{"got":"FIRST"}`),
	})

	if len(m.activity) != 2 {
		t.Fatalf("got %d activity items, want 2", len(m.activity))
	}
	for _, tc := range []struct {
		idx          int
		args, output string
	}{
		{0, "first", "FIRST"},
		{1, "second", "SECOND"},
	} {
		item := m.activity[tc.idx]
		if !strings.Contains(item.Args, tc.args) {
			t.Errorf("item %d args = %q, want it to contain %q", tc.idx, item.Args, tc.args)
		}
		if !strings.Contains(item.Output, tc.output) {
			t.Errorf("item %d output = %q, want it to contain %q — payloads crossed over",
				tc.idx, item.Output, tc.output)
		}
	}
}

// The case where "last line without output" and "the line just closed" come
// apart: an earlier call whose payload compacts to nothing leaves a tool line
// with an empty Output forever, so a search-backwards attach hands the NEXT
// call's payload to it.
func TestAnEmptyPayloadDoesNotStealTheNextCallsOutput(t *testing.T) {
	m := NewChatModel(&nullClient{}, "GAIA", "", true)

	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "search", Narration: "Searching",
	})
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolCallEvent{
		Type: "tool_call", Tool: "ping", Narration: "Pinging",
	})
	// "ping" closes first and returns an empty object, which renders as nothing.
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "ping", Data: json.RawMessage(`{}`),
	})
	m, _, _ = m.handleCanonicalEvent(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "search", Data: json.RawMessage(`{"hits":7}`),
	})

	if got := m.activity[1].Output; got != "" {
		t.Errorf("the empty-payload call now shows %q — it stole the other call's output", got)
	}
	if got := m.activity[0].Output; !strings.Contains(got, "7") {
		t.Errorf("the call that returned data shows %q; want its own payload", got)
	}
}
