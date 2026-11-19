// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
)

// On AMD's network the agent reports direct Claude ids it refuses; the model
// picker must show them, name the gateway id instead, and never run them.

var amdBlocked = map[string]event.ClaudeBlock{
	"claude-opus-5": {
		Reason: "On the AMD network Claude runs through the AMD LLM Gateway — use amd.Claude-Opus-5",
		Use:    "amd.Claude-Opus-5",
	},
}

func TestBlockedClaudeRowNamesTheGatewayModel(t *testing.T) {
	rows := filterPaletteCommandsBlocked(modelPalettePrefix+"opus", nil, amdBlocked)
	if len(rows) != 1 || rows[0].Name != modelPalettePrefix+"claude-opus-5" {
		t.Fatalf("expected the opus row, got %+v", rows)
	}
	if !strings.Contains(rows[0].Desc, "unavailable") || !strings.Contains(rows[0].Desc, "amd.Claude-Opus-5") {
		t.Errorf("the row must say it is unavailable and name the gateway id, got %q", rows[0].Desc)
	}
	other := filterPaletteCommandsBlocked(modelPalettePrefix+"sonnet", nil, amdBlocked)
	if len(other) != 1 || strings.Contains(other[0].Desc, "unavailable") {
		t.Errorf("an id the agent did not refuse stays a normal row, got %+v", other)
	}
}

func TestNoBlockedIDsLeavesThePickerUnchanged(t *testing.T) {
	plain := filterPaletteCommands(modelPalettePrefix, nil)
	withNil := filterPaletteCommandsBlocked(modelPalettePrefix, nil, nil)
	if len(plain) != len(withNil) {
		t.Fatalf("row count changed: %d vs %d", len(plain), len(withNil))
	}
	for i := range plain {
		if plain[i] != withNil[i] {
			t.Errorf("row %d changed: %+v vs %+v", i, plain[i], withNil[i])
		}
	}
}

func TestEnterOnABlockedClaudeRowRunsNothing(t *testing.T) {
	m := gaiaTestModel(t)
	m.claudeUnavailable = amdBlocked
	m = typeInto(t, m, modelPalettePrefix+"opus")
	if !m.palette.open {
		t.Fatal("test setup: the model picker should be open")
	}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("Enter on a refused id must not start anything")
	}
	if !m.palette.open {
		t.Error("the picker stays open so another row can be chosen")
	}
	for _, msg := range m.messages {
		if msg.Role == RoleUser {
			t.Errorf("nothing may be sent, found %q", msg.Content)
		}
	}
}

func TestTheStatusPingCarriesTheBlockedIDs(t *testing.T) {
	m, _ := newTestModel(t)
	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "Gemma-4-E4B-it-GGUF", ModelBackend: "lemonade",
		ClaudeUnavailable: amdBlocked,
	})
	if _, ok := m.claudeUnavailable["claude-opus-5"]; !ok {
		t.Fatalf("the ping's refused ids must reach the picker, got %+v", m.claudeUnavailable)
	}
	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "Gemma-4-E4B-it-GGUF", ModelBackend: "lemonade",
	})
	if len(m.claudeUnavailable) != 0 {
		t.Error("a later ping without the field (off the network) clears them")
	}
}
