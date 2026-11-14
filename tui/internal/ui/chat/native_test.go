package chat

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/client"
)

// nativeTransport is the client of a gaiacpp child, as SubprocessClient
// reports it.
type nativeTransport struct{ nullClient }

func (*nativeTransport) Native() bool { return true }

func nativeChat(t *testing.T) ChatModel {
	t.Helper()
	m := NewChatModelForCatalogAgent(&nativeTransport{}, setupAgentID, "GAIA", false)
	m.width, m.height = 120, 30
	return m
}

// gaiacpp has no Claude provider. Asked for a Claude model, the chat says so
// itself, in words the user can act on, and sends the agent nothing.
func TestAClaudeModelIsRefusedInWordsOnTheNativeAgent(t *testing.T) {
	m := nativeChat(t)
	m.setupChecking = false
	m.modelDisplay = "fireworks.deepseek-v4p1-flash"

	reason := m.refuseModelSwitch(client.ExampleClaudeModelID)
	for _, want := range []string{client.NativeClaudeUnavailable, "still on fireworks.deepseek-v4p1-flash"} {
		if !strings.Contains(reason, want) {
			t.Errorf("the refusal does not say %q:\n%s", want, reason)
		}
	}
	if strings.Contains(reason, "ANTHROPIC_API_KEY") || strings.Contains(reason, "`gaia ") {
		t.Errorf("the refusal points at something that cannot help here:\n%s", reason)
	}

	updated, _ := m.submit("/model " + client.ExampleClaudeModelID)
	next := updated.(ChatModel)
	if next.streaming {
		t.Fatal("the switch was sent to the agent, which can only refuse it")
	}
	last := next.messages[len(next.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, client.NativeClaudeUnavailable) {
		t.Errorf("the transcript does not carry the refusal: %+v", last)
	}

	if got := m.refuseModelSwitch("fireworks.gemma-4-31b-it"); got != "" {
		t.Errorf("a Lemonade model was refused on the native agent: %s", got)
	}
}

// The Python agent still takes a Claude model.
func TestAClaudeModelIsStillSentToThePythonAgent(t *testing.T) {
	m, _ := newTestModel(t)
	if got := m.refuseModelSwitch(client.ExampleClaudeModelID); got != "" {
		t.Errorf("a Claude model was refused on the Python agent: %s", got)
	}
}

// With the local server down, the native agent's other way forward is a cloud
// provider, never Claude.
func TestTheNativeAgentIsNeverPointedAtClaude(t *testing.T) {
	m := nativeChat(t)
	m.lemonadeKnown, m.lemonadeUp = true, false
	reason := m.refuseModelSwitch("Gemma-4-E4B-it-GGUF")
	if reason == "" || strings.Contains(reason, "Claude") || !strings.Contains(reason, "/provider") {
		t.Errorf("the alternative offered is wrong for gaiacpp:\n%s", reason)
	}
}

// Two programs can answer as "agent gaia"; the header says when it is gaiacpp.
func TestTheHeaderNamesTheNativeAgent(t *testing.T) {
	m := nativeChat(t)
	m.agentVersion = "0.25.4"
	if got := m.agentIdentity(); got != "agent gaia v0.25.4 (gaiacpp)" {
		t.Errorf("identity = %q", got)
	}
	if !strings.Contains(m.renderHeader(), "(gaiacpp)") {
		t.Errorf("the header does not name the native agent: %q", m.renderHeader())
	}

	python, _ := newTestModel(t)
	python.agentID, python.agentVersion = setupAgentID, "0.25.4"
	if got := python.agentIdentity(); got != "agent gaia v0.25.4" {
		t.Errorf("the Python agent's identity changed: %q", got)
	}
}
