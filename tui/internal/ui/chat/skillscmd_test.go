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

// Where /skills cannot add, the palette does not offer adding, and each form
// that does not work says what does, without naming a terminal command.
func TestSkillsOverTheRelaySaysWhatWorks(t *testing.T) {
	c := &relaySkillsClient{skills: []event.SkillCommand{{Name: "log-triage"}}}
	m := NewChatModelForFlagship(c, "gaia", "gaia", true)
	m.width, m.height = 100, 30

	m.input.SetValue("/skills")
	var row *paletteCommand
	for _, r := range m.paletteFiltered() {
		if r.Name == skillsCommandPrefix {
			row = &r
		}
	}
	if row == nil || row.Desc != skillsListOnlyDesc || strings.Contains(row.Desc, "add") {
		t.Fatalf("the palette must offer /skills as a list only, got %+v", row)
	}

	for line, want := range map[string]string{
		"/skills add acme/skills":         "Settings › Skills",
		"/skills add acme/skills confirm": "Settings › Skills",
		"/skills remove log-triage":       "Type /<skill-name>",
		"/skills list":                    "Type /<skill-name>",
	} {
		updated, cmd := m.submit(line)
		got := updated.(ChatModel)
		last := got.messages[len(got.messages)-1]
		if cmd != nil || len(c.sent) != 0 || last.Role != RoleError || !strings.Contains(last.Content, want) {
			t.Errorf("%q: sent %v, answered %+v; want %q and no turn", line, c.sent, last, want)
		}
		for _, banned := range []string{"gaia skill", "in a terminal", "not available in this session"} {
			if strings.Contains(last.Content, banned) {
				t.Errorf("%q answered with %q: %s", line, banned, last.Content)
			}
		}
	}

	// An agent the TUI runs itself still offers adding from a link.
	own := NewChatModelForFlagship(&nullClient{}, "gaia", "gaia", true)
	own.input.SetValue("/skills")
	for _, r := range own.paletteFiltered() {
		if r.Name == skillsCommandPrefix && !strings.Contains(r.Desc, "/skills add") {
			t.Errorf("the subprocess palette lost /skills add: %q", r.Desc)
		}
	}
}

// Research mode over the relay: the skill list the agent was asked for decides
// what is a skill command, and until it answers the line is sent as typed.
func TestResearchModeOverTheRelayRunsSkillCommands(t *testing.T) {
	c := &relaySkillsClient{skills: []event.SkillCommand{{Name: "summarize"}, {Name: "office-docs"}}}
	m := NewChatModelForFlagship(c, "gaia", "gaia", true)
	m.width, m.height = 100, 30
	m.researchMode = true

	// Before the list arrives nothing says /summarize is not a skill.
	if got := m.withResearch("/summarize this page"); got != "/summarize this page" {
		t.Errorf("before the skill list arrived, sent %q", got)
	}
	if got := m.withResearch("what is new in go"); got != "/research what is new in go" {
		t.Errorf("a plain question was sent as %q", got)
	}

	next, _ := m.Update(m.listSkillsCmd()())
	m = next.(ChatModel)
	for _, line := range []string{"/summarize this page", "/office-docs make a memo"} {
		if got := m.withResearch(line); got != line {
			t.Errorf("%q was sent as %q", line, got)
		}
	}
	// Known list: a name that is not a skill is researched.
	if got := m.withResearch("/sumarize this page"); got != "/research /sumarize this page" {
		t.Errorf("a mistyped skill name was sent as %q", got)
	}
}
