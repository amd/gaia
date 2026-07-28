// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// A card is drawn by this client, so its rows never reach the sidecar's history
// on their own. If the transcript records only the model's framing sentence, a
// follow-up referring to something on screen ("when is that one?") resolves
// against nothing — which is exactly what happened with a visible email.
func TestTheTranscriptCarriesWhatTheCardShowed(t *testing.T) {
	payload := map[string]any{
		"kind": "email_pre_scan",
		"suggested_archives": []map[string]string{{
			"message_id": "abc123",
			"sender":     "DMW Martial Arts",
			"subject":    "SUMMER HOLIDAY SALE IS LIVE NOW!",
		}},
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("fixture: %v", err)
	}
	line := displayedCard(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "pre_scan_inbox",
		Render: "email_pre_scan", Data: raw,
	})

	s := &SSEClient{}
	s.appendTurn("triage my inbox", "Here's your inbox pre-scan.", []string{line})

	turns := s.Transcript()
	if len(turns) != 2 {
		t.Fatalf("expected a user and an assistant turn, got %d", len(turns))
	}
	got := turns[1].Content
	for _, want := range []string{
		"Here's your inbox pre-scan.", // the model's own words survive
		"SUMMER HOLIDAY SALE",         // and so does the row the user can see
		"DMW Martial Arts",
		"abc123", // the id, so "that one" can become an action
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the assistant turn does not carry %q:\n%s", want, got)
		}
	}
}

// No card, no noise: an ordinary turn must not gain a "[shown to the user]"
// section it has nothing to put in.
func TestAnOrdinaryTurnIsRecordedUnchanged(t *testing.T) {
	s := &SSEClient{}
	s.appendTurn("thanks", "You're welcome!", nil)
	if got := s.Transcript()[1].Content; got != "You're welcome!" {
		t.Errorf("a card-less turn was rewritten: %q", got)
	}
}
