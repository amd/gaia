// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "testing"

// A 200-second turn showed: two timers disagreeing, "Thinking" three times,
// "Step 1/50", "Step 2/50", and the model name — while the one line that said
// what the agent was doing (the tool call and its query) scrolled away.
func TestAgentLoopMechanicsAreNotShownToTheUser(t *testing.T) {
	for _, noise := range []string{
		"Processing with Gemma-4-E4B-it-GGUF...", // identical every message
		"Step 1/50",                              // a loop bound, not progress
		"Step 12/50",
		"Thinking", // the spinner already says this
		"Completed in 1 steps",
	} {
		if got := userFacingStatus(noise); got != "" {
			t.Errorf("%q was shown to the user as %q", noise, got)
		}
	}
}

// Anything that names real work must survive — that is the whole point of the
// line. Losing these is how a user concludes the agent is stuck.
func TestRealWorkStillReachesTheUser(t *testing.T) {
	for _, keep := range []string{
		"Triaged 7/25 — Your Shop Job Claim Check",
		"Opening your browser to sign in to Gmail.",
		"Gmail needs an OAuth client before I can sign you in.",
	} {
		if got := userFacingStatus(keep); got != keep {
			t.Errorf("real progress %q was suppressed (got %q)", keep, got)
		}
	}
}

// The status line is replaced, not accumulated: a user watching needs the
// current stage, and a completed tool call stays as evidence of work done.
func TestStatusReplacesRatherThanPilesUp(t *testing.T) {
	m := &ChatModel{}
	m.setLiveStatus("Searching your inbox")
	m.setLiveStatus("Reading 4 messages")
	if n := len(m.activity); n != 1 {
		t.Fatalf("expected one live status line, got %d: %+v", n, m.activity)
	}
	if got := m.activity[0].Content; got != "Reading 4 messages" {
		t.Errorf("status line did not update in place: %q", got)
	}
	m.activity = append(m.activity, ActivityItem{Kind: "tool", Content: "search_messages"})
	m.setLiveStatus("Writing your answer")
	if n := len(m.activity); n != 3 {
		t.Fatalf("a new stage after a tool call must start its own line, got %d", n)
	}
}
