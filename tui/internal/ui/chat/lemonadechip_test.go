// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

func chipFor(m ChatModel) string { return ansi.Strip(m.renderLemonadeChip()) }

func TestTheVersionIsNamedInDevMode(t *testing.T) {
	m := ChatModel{dev: true, lemonadeKnown: true, lemonadeUp: true, lemonadeVersion: "10.7.2"}
	if got := chipFor(m); !strings.Contains(got, "10.7.2") {
		t.Errorf("the header does not name the Lemonade version: %q", got)
	}
}

// A confusing session is very often just "Lemonade is not running", so the
// header has to say so rather than leaving the user to infer it from failures.
func TestADownServerSaysSo(t *testing.T) {
	m := ChatModel{dev: true, lemonadeKnown: true, lemonadeUp: false}
	got := strings.ToLower(chipFor(m))
	if !strings.Contains(got, "down") {
		t.Errorf("a down server is not reported: %q", got)
	}
}

// "The agent never told us" and "the agent told us it is down" are different
// facts. Rendering the first as the second sends someone chasing a healthy
// server; this is the same omit-rather-than-fake rule the token count follows.
func TestNothingIsClaimedBeforeTheAgentReports(t *testing.T) {
	if got := chipFor(ChatModel{dev: true}); got != "" {
		t.Errorf("claimed something before the agent reported: %q", got)
	}
}

func TestReachableButUnversionedSaysReachable(t *testing.T) {
	m := ChatModel{dev: true, lemonadeKnown: true, lemonadeUp: true}
	got := strings.ToLower(chipFor(m))
	if !strings.Contains(got, "up") || strings.Contains(got, "down") {
		t.Errorf("a reachable server with no version reported wrongly: %q", got)
	}
}

// Outside --dev this is harness telemetry, same as the token counts.
func TestTheChipIsDevOnly(t *testing.T) {
	m := ChatModel{lemonadeKnown: true, lemonadeUp: true, lemonadeVersion: "10.7.2"}
	if got := chipFor(m); got != "" {
		t.Errorf("Lemonade telemetry leaked outside --dev: %q", got)
	}
}

// The state has to actually arrive from the agent's ping, not just be
// renderable — this is the wiring a unit test on the renderer alone would miss.
func TestThePingCarriesLemonadeStateIntoTheModel(t *testing.T) {
	up := true
	m := ChatModel{dev: true}
	next, _, _ := m.handleCanonicalEvent(event.CanonicalStatusEvent{
		Type:              "status",
		ModelID:           "Gemma-4-E4B-it-GGUF",
		ModelDisplay:      "Gemma-4-E4B-it-GGUF",
		ModelBackend:      "lemonade",
		LemonadeReachable: &up,
		LemonadeVersion:   "10.7.2",
		LemonadeBaseURL:   "http://localhost:13305",
	})

	if !next.lemonadeKnown || !next.lemonadeUp || next.lemonadeVersion != "10.7.2" {
		t.Fatalf("state did not reach the model: known=%v up=%v version=%q",
			next.lemonadeKnown, next.lemonadeUp, next.lemonadeVersion)
	}
	if got := chipFor(next); !strings.Contains(got, "10.7.2") {
		t.Errorf("header does not show the reported version: %q", got)
	}
}

func TestADownReportFromThePingRendersDown(t *testing.T) {
	down := false
	m := ChatModel{dev: true}
	next, _, _ := m.handleCanonicalEvent(event.CanonicalStatusEvent{
		Type:              "status",
		ModelID:           "claude-sonnet-5",
		ModelDisplay:      "Sonnet 5",
		ModelBackend:      "claude",
		ModelRemote:       true,
		LemonadeReachable: &down,
	})

	if !next.lemonadeKnown || next.lemonadeUp {
		t.Fatal("a down report did not reach the model")
	}
	// Remote chat does not make a dead Lemonade harmless — embeddings need it.
	if got := strings.ToLower(chipFor(next)); !strings.Contains(got, "down") {
		t.Errorf("a Claude session hides that Lemonade is down: %q", got)
	}
}
