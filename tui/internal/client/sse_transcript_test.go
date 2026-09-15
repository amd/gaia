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
	s.appendTurn("triage my inbox", "Here's your inbox pre-scan.", []string{line}, nil)

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

// No card, no noise: an ordinary turn must not gain a ui-context section it
// has nothing to put in.
func TestAnOrdinaryTurnIsRecordedUnchanged(t *testing.T) {
	s := &SSEClient{}
	s.appendTurn("thanks", "You're welcome!", nil, nil)
	if got := s.Transcript()[1].Content; got != "You're welcome!" {
		t.Errorf("a card-less turn was rewritten: %q", got)
	}
}

// The stored marker must read as metadata, never as a heading a model could
// mistake for its own prior words and echo back verbatim. Specifically: the
// old bare "[shown to the user]" string must be gone, and whatever replaces
// it still needs to carry the row content a follow-up resolves against.
func TestCardMarkerCannotBeMistakenForContent(t *testing.T) {
	s := &SSEClient{}
	s.appendTurn("triage my inbox", "Here's your inbox pre-scan.",
		[]string{"- [suggested_archives] DMW Martial Arts — SUMMER HOLIDAY SALE (id abc123)"}, nil)

	got := s.Transcript()[1].Content
	if strings.Contains(got, "[shown to the user]") {
		t.Errorf("the old bare marker must not appear verbatim:\n%s", got)
	}
	if !strings.Contains(got, uiContextMarker) {
		t.Errorf("expected the renamed ui-context marker in:\n%s", got)
	}
	if !strings.Contains(got, "abc123") {
		t.Errorf("the marker rename must not drop the row content:\n%s", got)
	}
}

// A follow-up delivered mid-turn is part of that turn's conversation. It has to
// be recorded between the question and the answer: /query is stateless, so this
// transcript is the only copy that survives into the next turn's pushed
// context, and an answer filed before the words it responds to reads as a
// non-sequitur to the model on every turn after.
func TestAFollowUpIsRecordedBetweenTheQuestionAndTheAnswer(t *testing.T) {
	s := &SSEClient{}
	s.appendTurn("triage my inbox", "Done — 3 archived.", nil,
		[]string{"only the unread ones", "and skip newsletters"})

	turns := s.Transcript()
	var got []string
	for _, turn := range turns {
		got = append(got, turn.Role+":"+turn.Content)
	}
	want := []string{
		"user:triage my inbox",
		"user:only the unread ones",
		"user:and skip newsletters",
		"assistant:Done — 3 archived.",
	}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Errorf("transcript = %v, want %v", got, want)
	}
}
