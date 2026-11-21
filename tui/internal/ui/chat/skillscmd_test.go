// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

func TestSkillsLinesAreCommands(t *testing.T) {
	for _, q := range []string{"/skills", " /skills ", "/skills add https://github.com/acme/skills"} {
		if !isSkillsCommand(q) {
			t.Errorf("%q must route as /skills", q)
		}
	}
	for _, q := range []string{"/skillset", "what skills do you have?"} {
		if isSkillsCommand(q) {
			t.Errorf("%q is a question, not /skills", q)
		}
	}
}

// The flagship answers /skills itself, so the line goes out as a turn.
func TestSkillsAddGoesToTheFlagship(t *testing.T) {
	m := NewChatModel(&queryCapturingClient{}, "gaia", "")
	m.width, m.height = 100, 30

	updated, cmd := m.submit("/skills add https://github.com/acme/skills")
	m = updated.(ChatModel)

	if cmd == nil || !m.streaming {
		t.Fatal("/skills add must start a turn on the gaia agent")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleUser || last.Content != "/skills add https://github.com/acme/skills" {
		t.Errorf("the command must show in the transcript, got %+v", last)
	}
}

func TestSkillsRefusedOnAnUnsupportedAgent(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModel(c, "email", "")
	m.width, m.height = 100, 30

	updated, _ := m.submit("/skills add acme/skills")
	m = updated.(ChatModel)

	if len(c.sent) != 0 {
		t.Errorf("an unsupported agent must never receive /skills: %v", c.sent)
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "does not manage skills") {
		t.Errorf("the refusal must be visible and say why, got %+v", last)
	}
}

// relaySkillsClient is the daemon relay with an agent that lists its skills.
type relaySkillsClient struct {
	relayClient
	skills []event.SkillCommand
	err    error
}

func (c *relaySkillsClient) ListSkills(context.Context) ([]event.SkillCommand, error) {
	return c.skills, c.err
}

// Over the relay the agent loop would treat /skills as a question, so the
// list comes from the agent's skills route and no turn is sent.
func TestSkillsListsOverTheRelay(t *testing.T) {
	c := &relaySkillsClient{skills: []event.SkillCommand{{Name: "log-triage"}, {Name: "daily-brief"}}}
	m := NewChatModelForFlagship(c, "gaia", "gaia", true)
	m.width, m.height = 100, 30

	updated, cmd := m.submit("/skills")
	m = updated.(ChatModel)
	if cmd == nil || m.streaming || len(c.sent) != 0 {
		t.Fatalf("/skills over the relay must list, not send a turn (sent %v)", c.sent)
	}
	next, _ := m.Update(cmd())
	m = next.(ChatModel)

	last := m.messages[len(m.messages)-1]
	if last.Role != RoleAssistant || !strings.Contains(last.Content, "Installed skills (2)") ||
		!strings.Contains(last.Content, "`log-triage`") {
		t.Errorf("the listing must show in the transcript, got %+v", last)
	}
	if len(m.skillCommands) != 2 {
		t.Errorf("the / list must take the answer, got %+v", m.skillCommands)
	}

	c.err = errors.New("relay down")
	updated, cmd = m.submit("/skills")
	next, _ = updated.(ChatModel).Update(cmd())
	m = next.(ChatModel)
	if last := m.messages[len(m.messages)-1]; last.Role != RoleError || !strings.Contains(last.Content, "relay down") {
		t.Errorf("a failed listing must say why, got %+v", last)
	}
}

func TestSkillsAddOverTheRelayNamesNoTerminalCommand(t *testing.T) {
	c := &relaySkillsClient{}
	m := NewChatModelForFlagship(c, "gaia", "gaia", true)
	m.width, m.height = 100, 30

	updated, _ := m.submit("/skills add acme/skills")
	m = updated.(ChatModel)

	if len(c.sent) != 0 {
		t.Errorf("/skills add must not reach the agent loop: %v", c.sent)
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || strings.Contains(last.Content, "gaia skill") ||
		!strings.Contains(last.Content, "Agent UI") {
		t.Errorf("the refusal must point at the Agent UI, not a terminal, got %+v", last)
	}
}
