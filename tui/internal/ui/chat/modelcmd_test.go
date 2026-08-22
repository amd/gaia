// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// queryCapturingClient records every literal line handed to Send, so a test
// can prove exactly what reached the agent — not just that something did.
type queryCapturingClient struct {
	nullClient
	sent []string
}

func (c *queryCapturingClient) Send(_ context.Context, query string) (<-chan interface{}, error) {
	c.sent = append(c.sent, query)
	ch := make(chan interface{})
	close(ch)
	return ch, nil
}

// The /model forms are recognised as commands, not questions — mirrors
// TestBypassCommandsAreNeverSentAsQueries.
func TestModelCommandsAreRecognised(t *testing.T) {
	for _, cmd := range []string{
		"/model",
		"/model claude-sonnet-5",
		"/model Gemma-4-E4B-it-GGUF",
		"  /model claude-opus-5  ",
	} {
		if !isModelCommand(cmd) {
			t.Errorf("%q must be recognised as a /model command", cmd)
		}
	}
	for _, notCmd := range []string{
		"what model do you use?",
		"/modeling clay",
		"tell me about /model",
		"",
	} {
		if isModelCommand(notCmd) {
			t.Errorf("%q must not be treated as a /model command", notCmd)
		}
	}
}

// Unlike /bypass, /model genuinely needs the agent — only it can discover
// Lemonade's downloaded models and validate a Claude credential — so it must
// still reach Send with the exact literal line, dispatched as a recognised
// command rather than silently dropped or mangled.
func TestModelCommandDispatchesTheLiteralLineToTheAgent(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30

	_, cmd := m.submit("/model claude-sonnet-5")
	msg := findBatchedMsg(cmd, func(msg tea.Msg) bool {
		_, ok := msg.(channelReadyMsg)
		return ok
	})
	if msg == nil {
		t.Fatal("/model did not dispatch a turn to the agent")
	}
	if len(c.sent) != 1 || c.sent[0] != "/model claude-sonnet-5" {
		t.Errorf("the exact command line must reach the agent, got %v", c.sent)
	}
}

// /model is a control request, not a question — the raw command line must
// never appear as if the user asked it as a chat question.
func TestModelCommandDoesNotShowAsAChatBubble(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30

	updated, _ := m.submit("/model")
	m = updated.(ChatModel)

	for _, msg := range m.messages {
		if msg.Role == RoleUser {
			t.Errorf("/model must not be posted as a user chat bubble: %+v", msg)
		}
	}
}

// The header must name the SPECIFIC model the agent resolved — never a bare
// backend word — and mark a remote one distinctly from a local one.
func TestHeaderNamesTheSpecificModelFromTheAgent(t *testing.T) {
	m, _ := newTestModel(t)

	m = feed(t, m, event.CanonicalStatusEvent{
		Type:         "status",
		ModelID:      "claude-sonnet-5",
		ModelDisplay: "Sonnet 5",
		ModelBackend: "claude",
		ModelRemote:  true,
	})

	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, "Sonnet 5") {
		t.Errorf("header must name the specific model, got %q", header)
	}
	if strings.HasSuffix(strings.TrimSpace(header), "claude") {
		t.Errorf("header must never show a bare backend word: %q", header)
	}
}

func TestHeaderNamesTheLocalModelFromTheAgent(t *testing.T) {
	m, _ := newTestModel(t)

	m = feed(t, m, event.CanonicalStatusEvent{
		Type:         "status",
		ModelID:      "Gemma-4-E4B-it-GGUF",
		ModelDisplay: "Gemma-4-E4B-it-GGUF",
		ModelBackend: "lemonade",
		ModelRemote:  false,
	})

	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, "Gemma-4-E4B-it-GGUF") {
		t.Errorf("header must name the local model, got %q", header)
	}
}

// A /model switch mid-session updates the header the same way the startup
// ping does — the whole point of a LIVE switch.
func TestHeaderUpdatesAfterALiveModelSwitch(t *testing.T) {
	m, _ := newTestModel(t)

	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "Gemma-4-E4B-it-GGUF", ModelDisplay: "Gemma-4-E4B-it-GGUF",
		ModelBackend: "lemonade", ModelRemote: false,
	})
	if !strings.Contains(ansi.Strip(m.renderHeader()), "Gemma-4-E4B-it-GGUF") {
		t.Fatal("precondition: header does not yet show the local model")
	}

	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "claude-opus-5", ModelDisplay: "Opus 5",
		ModelBackend: "claude", ModelRemote: true,
	})

	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, "Opus 5") {
		t.Errorf("header did not pick up the live switch, got %q", header)
	}
	if strings.Contains(header, "Gemma") {
		t.Errorf("header still shows the pre-switch model: %q", header)
	}
}

// An ordinary progress status (no ModelID) must never be mistaken for a
// model-state ping.
func TestOrdinaryStatusEventsDoNotTouchTheModelChip(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Reading issue #42"})

	if m.modelDisplay != "" {
		t.Errorf("an ordinary status must not set modelDisplay: %q", m.modelDisplay)
	}
}

// Only the gaia flagship agent's stdio.py understands /model — every other
// agent (a daemon-relay agent like email, or another subprocess agent) would
// otherwise receive the literal text as an uncomprehended chat question.
func TestModelCommandRefusedOnAnUnsupportedAgent(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30

	updated, _ := m.submit("/model claude-sonnet-5")
	m = updated.(ChatModel)

	if len(c.sent) != 0 {
		t.Errorf("an unsupported agent must never receive /model as a query: %v", c.sent)
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError {
		t.Errorf("the refusal must be visible, got role %v: %+v", last.Role, last)
	}
	if !strings.Contains(last.Content, "does not support") {
		t.Errorf("the refusal must say why, got: %q", last.Content)
	}
}

// A cancelled turn respawns the child from its ORIGINAL launch flags
// (subprocess.go), silently reverting any live /model switch. The header
// must self-correct AND say so — a silent revert is the failure mode this
// guards against.
func TestAnUnrequestedModelChangeWarnsOfARevert(t *testing.T) {
	m, _ := newTestModel(t)

	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "claude-opus-5", ModelDisplay: "Opus 5",
		ModelBackend: "claude", ModelRemote: true,
	})
	before := len(m.messages)

	// A ping for a DIFFERENT model arrives with no /model switch requested —
	// exactly what a silent respawn looks like.
	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "Gemma-4-E4B-it-GGUF", ModelDisplay: "Gemma-4-E4B-it-GGUF",
		ModelBackend: "lemonade", ModelRemote: false,
	})

	if len(m.messages) != before+1 {
		t.Fatalf("expected exactly one new message warning of the revert, got %d new", len(m.messages)-before)
	}
	warning := m.messages[len(m.messages)-1]
	if warning.Role != RoleStatus || !strings.Contains(warning.Content, "reverted") {
		t.Errorf("the revert must be surfaced, got: %+v", warning)
	}
	if !strings.Contains(ansi.Strip(m.renderHeader()), "Gemma-4-E4B-it-GGUF") {
		t.Error("the header must still self-correct to the model actually running")
	}
}

// The SAME ping shape, but as the expected confirmation of a switch THIS
// session itself requested, must never be flagged as a revert.
func TestAConfirmedModelSwitchDoesNotWarnOfARevert(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30

	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "Gemma-4-E4B-it-GGUF", ModelDisplay: "Gemma-4-E4B-it-GGUF",
		ModelBackend: "lemonade", ModelRemote: false,
	})

	updated, _ := m.submit("/model claude-opus-5")
	m = updated.(ChatModel)
	before := len(m.messages)

	m = feed(t, m, event.CanonicalStatusEvent{
		Type: "status", ModelID: "claude-opus-5", ModelDisplay: "Opus 5",
		ModelBackend: "claude", ModelRemote: true,
	})

	for _, msg := range m.messages[before:] {
		if strings.Contains(msg.Content, "reverted") {
			t.Errorf("a switch this session requested must not be flagged as a revert: %+v", msg)
		}
	}
}
