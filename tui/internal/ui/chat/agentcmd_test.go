// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// agentProgramAsked runs query through submit and returns what the host was
// asked, failing when nothing was sent to it.
func agentProgramAsked(t *testing.T, m ChatModel, query string) AgentProgramMsg {
	t.Helper()
	updated, cmd := m.submit(query)
	for _, msg := range updated.(ChatModel).messages {
		if msg.Role == RoleUser {
			t.Fatalf("%q was posted as a chat message: %q", query, msg.Content)
		}
	}
	if cmd == nil {
		t.Fatalf("%q sent nothing to the host; transcript: %+v", query, updated.(ChatModel).messages)
	}
	asked, ok := cmd().(AgentProgramMsg)
	if !ok {
		t.Fatalf("%q produced %T, want AgentProgramMsg", query, cmd())
	}
	return asked
}

// hostedGaiaModel is an idle flagship chat whose host answers /agent.
func hostedGaiaModel(t *testing.T) ChatModel {
	t.Helper()
	m := gaiaTestModel(t).WithAgentPrograms()
	// The first-boot setup check would queue a command instead of running it.
	m.setupChecking = false
	return m
}

func TestAgentCommandAsksTheHostWithItsArgument(t *testing.T) {
	m := hostedGaiaModel(t)

	for query, want := range map[string]string{
		"/agent":           "",
		"/agent gaiacpp":   "gaiacpp",
		"/agent gaiapy":    "gaiapy",
		" /agent  GaiaPy ": "GaiaPy",
	} {
		if got := agentProgramAsked(t, m, query); got.Arg != want {
			t.Errorf("%q asked for %q, want %q", query, got.Arg, want)
		}
	}
}

// /agents is a different command and must not be read as /agent with an "s".
func TestAgentsIsNotTheAgentCommand(t *testing.T) {
	if isAgentCommand("/agents") || isAgentCommand("/agentx gaiacpp") {
		t.Fatal("a longer command name was read as /agent")
	}
	if !isAgentCommand("/agent") || !isAgentCommand("/agent gaiacpp") {
		t.Fatal("/agent was not recognised")
	}
}

// With nothing to answer it, /agent says so instead of doing nothing or being
// sent to the agent as a question.
func TestAgentCommandRefusesWhereNothingCanSwitch(t *testing.T) {
	unhosted := gaiaTestModel(t)
	updated, cmd := unhosted.submit("/agent gaiacpp")
	msgs := updated.(ChatModel).messages
	if cmd != nil || len(msgs) != 1 || msgs[0].Role != RoleError || !strings.Contains(msgs[0].Content, "--agent-program") {
		t.Fatalf("an unhosted chat did not refuse with the way forward: cmd=%v msgs=%+v", cmd != nil, msgs)
	}

	other, _ := newTestModel(t)
	other = other.WithAgentPrograms()
	updated, cmd = other.submit("/agent")
	msgs = updated.(ChatModel).messages
	if cmd != nil || len(msgs) != 1 || msgs[0].Role != RoleError || !strings.Contains(msgs[0].Content, "one program") {
		t.Fatalf("another agent did not refuse /agent: cmd=%v msgs=%+v", cmd != nil, msgs)
	}
}

// Typing /agent and pressing Enter runs /agent, not /agents below it.
func TestPaletteOffersAgentAheadOfAgents(t *testing.T) {
	m := typeInto(t, hostedGaiaModel(t), "/agent")
	names := paletteNames(m.paletteFiltered())
	if len(names) != 2 || names[0] != "/agent" || names[1] != "/agents" {
		t.Fatalf("\"/agent\" should offer /agent then /agents, got %v", names)
	}

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd == nil {
		t.Fatal("Enter on /agent ran nothing")
	}
	if _, ok := cmd().(AgentProgramMsg); !ok {
		t.Fatalf("Enter on \"/agent\" ran %T, want the agent-program listing", cmd())
	}
	if updated.(ChatModel).agentsPanel != nil {
		t.Fatal("Enter on \"/agent\" opened the /agents panel")
	}

	m = typeInto(t, gaiaTestModel(t), "/agents")
	if names := paletteNames(m.paletteFiltered()); len(names) != 1 || names[0] != "/agents" {
		t.Fatalf("\"/agents\" should offer only /agents, got %v", names)
	}
}

// The space after /agent turns the palette into the two-program picker.
func TestPaletteAgentPickerListsBothProgramsAndMarksBeta(t *testing.T) {
	m := typeInto(t, hostedGaiaModel(t), "/agent ")
	rows := m.paletteFiltered()
	names := paletteNames(rows)
	if len(names) != 3 || !containsAll(names, "/agent", "/agent gaiapy", "/agent gaiacpp") {
		t.Fatalf("\"/agent \" should offer the listing and both programs, got %v", names)
	}
	for _, row := range rows {
		beta := strings.Contains(row.Desc, "Beta")
		if (row.Name == "/agent gaiacpp") != beta {
			t.Errorf("%s: Beta marking is wrong in %q", row.Name, row.Desc)
		}
	}

	m = typeInto(t, m, "cpp")
	if names := paletteNames(m.paletteFiltered()); len(names) != 1 || names[0] != "/agent gaiacpp" {
		t.Fatalf("\"/agent cpp\" should narrow to gaiacpp, got %v", names)
	}
	_, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd == nil {
		t.Fatal("Enter on the gaiacpp row ran nothing")
	}
	if asked, ok := cmd().(AgentProgramMsg); !ok || asked.Arg != "gaiacpp" {
		t.Fatalf("Enter on the gaiacpp row asked for %#v", cmd())
	}
}

// An agent with one program is offered neither the command nor its picker.
func TestPaletteHidesAgentForOtherAgents(t *testing.T) {
	m, _ := newTestModel(t)
	for _, typed := range []string{"/agent", "/agent "} {
		for _, name := range paletteNames(typeInto(t, m, typed).paletteFiltered()) {
			if strings.HasPrefix(name, "/agent") && name != "/agents" {
				t.Errorf("typing %q offered %q to an agent with one program", typed, name)
			}
		}
	}
}
