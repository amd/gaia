// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
)

func flagshipModel(t *testing.T) ChatModel {
	t.Helper()
	m := NewChatModel(&nullClient{}, modelSwitchAgentID, "GAIA", false)
	m.width, m.height = 100, 30
	return m
}

func lastUserLine(m ChatModel) string {
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleUser {
			return m.messages[i].Content
		}
	}
	return ""
}

func TestResearchCommandIsRecognisedOnlyAtTheStart(t *testing.T) {
	for line, want := range map[string]bool{
		"/research":             true,
		"/research RISC-V":      true,
		"  /RESEARCH jwst":      true,
		"/research\tthe EU act": true,
		"/researcher notes":     false,
		"please /research this": false,
		"research RISC-V":       false,
	} {
		if got := isResearchCommand(line); got != want {
			t.Errorf("isResearchCommand(%q) = %v, want %v", line, got, want)
		}
	}
	if got := researchTopic("  /research   the EU AI Act "); got != "the EU AI Act" {
		t.Fatalf("topic = %q", got)
	}
}

func TestResearchWithATopicSendsOneResearchTurn(t *testing.T) {
	m := flagshipModel(t)
	next, _ := m.submit("/research the history of RISC-V")
	m = next.(ChatModel)
	if got := lastUserLine(m); got != "/research the history of RISC-V" {
		t.Fatalf("sent %q", got)
	}
	if !m.streaming || m.researchMode {
		t.Fatal("a topic must start a turn and leave research mode as it was")
	}
}

func TestBareResearchTogglesResearchModeForEveryMessage(t *testing.T) {
	m := flagshipModel(t)
	next, _ := m.submit("/research")
	m = next.(ChatModel)
	if !m.researchMode || m.streaming {
		t.Fatal("bare /research must switch research mode on without a turn")
	}
	if !strings.Contains(m.messages[len(m.messages)-1].Content, "Research mode on") {
		t.Fatalf("no confirmation: %+v", m.messages)
	}
	if !strings.Contains(m.renderHeader(), "research") {
		t.Fatal("header does not show research mode")
	}

	next, _ = m.submit("the EU AI Act timeline")
	m = next.(ChatModel)
	if got := lastUserLine(m); got != "/research the EU AI Act timeline" {
		t.Fatalf("sent %q", got)
	}

	m.streaming = false
	next, _ = m.submit("/research")
	m = next.(ChatModel)
	if m.researchMode || strings.Contains(m.renderHeader(), "research") {
		t.Fatal("second /research did not switch it off")
	}
	next, _ = m.submit("plain question")
	m = next.(ChatModel)
	if got := lastUserLine(m); got != "plain question" {
		t.Fatalf("sent %q after research mode was switched off", got)
	}
}

func TestResearchModeLeavesOtherCommandsAlone(t *testing.T) {
	m := flagshipModel(t)
	m.researchMode = true
	if got := m.withResearch("/research already"); got != "/research already" {
		t.Fatalf("doubled the command: %q", got)
	}
	if got := m.withResearch("   "); got != "   " {
		t.Fatalf("prefixed an empty line: %q", got)
	}
}

func TestResearchIsRefusedOnAnotherAgent(t *testing.T) {
	m, _ := newTestModel(t) // the email agent
	next, _ := m.submit("/research anything")
	m = next.(ChatModel)
	if m.streaming || m.researchMode {
		t.Fatal("another agent must not get a /research turn")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "flagship") {
		t.Fatalf("no refusal: %+v", last)
	}
}

func TestResearchIsInThePalette(t *testing.T) {
	for _, c := range paletteCommands {
		if c.Name == researchCommand {
			return
		}
	}
	t.Fatal("/research is missing from the command palette")
}

func TestResearchIsOfferedOnlyToTheFlagship(t *testing.T) {
	if !flagshipModel(t).availableCommandSet()[researchCommand] {
		t.Fatal("the flagship should offer /research")
	}
	other, _ := newTestModel(t)
	if other.availableCommandSet()[researchCommand] {
		t.Fatal("another agent should not offer /research")
	}
}

func TestClearTurnsResearchModeOff(t *testing.T) {
	m := flagshipModel(t)
	m.researchMode = true
	next, _ := m.submit("/clear")
	m = next.(ChatModel)
	if m.researchMode {
		t.Fatal("/clear left research mode on for the new conversation")
	}
}
