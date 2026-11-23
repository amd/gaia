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

// With no answer to lead into, the last status before a failure stays on record.
func TestCommitWorkKeepsTheLastStatusWhenTheTurnFails(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "write_file", Args: json.RawMessage(`{"path":"notes.md"}`)},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "write_file", Data: json.RawMessage(`{"status":"success"}`)},
		event.CanonicalStatusEvent{Type: "status", Message: "Verifying the write"},
		event.CanonicalErrorEvent{Type: "error", Detail: "model unloaded"},
	)
	for _, msg := range m.messages {
		if msg.Role != RoleWork {
			continue
		}
		for _, item := range msg.Work {
			if item.Content == "Verifying the write" {
				return
			}
		}
		t.Fatalf("last status before the failure was dropped: %+v", msg.Work)
	}
	t.Fatal("expected a work record for the tool step")
}
