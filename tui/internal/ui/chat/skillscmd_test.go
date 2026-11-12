// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
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
	m := NewChatModel(&queryCapturingClient{}, "gaia", "", false)
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
	m := NewChatModel(c, "email", "", false)
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
