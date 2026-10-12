// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// A closing "Done." status just before the answer is not kept beside it.
func TestCommitWorkDropsNarrationThatLeadsIntoAnswer(t *testing.T) {
	m, _ := newTestModel(t)
	m.dev = true
	m.streaming = true
	m = feed(t, m,
		event.CanonicalStatusEvent{Type: "status", Message: "Reading the note"},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "write_file", Args: json.RawMessage(`{"path":"notes.md"}`)},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "write_file", Data: json.RawMessage(`{"status":"success"}`)},
		event.CanonicalStatusEvent{Type: "status", Message: "Done."},
		event.CanonicalFinalEvent{Type: "final", Answer: "Done — file now reads: hi"},
	)
	for _, msg := range m.messages {
		if msg.Role != RoleWork {
			continue
		}
		var sawLead bool
		for _, item := range msg.Work {
			if strings.Contains(item.Content, "Done") {
				t.Fatalf("trailing narration kept in the work record: %+v", msg.Work)
			}
			sawLead = sawLead || item.Content == "Reading the note"
		}
		if !sawLead {
			t.Errorf("narration before a step must be kept: %+v", msg.Work)
		}
		return
	}
	t.Fatal("expected a work record for the tool step")
}
