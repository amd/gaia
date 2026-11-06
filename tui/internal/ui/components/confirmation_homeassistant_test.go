// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// The agent names Home Assistant's tools mcp_home_assistant_<HA name>, sends
// no always_scope for them, and summarises them as "Run '<name>' with …?".
func TestHomeAssistantPromptReadsAsTheAction(t *testing.T) {
	const action = "mcp_home_assistant_HassTurnOn"
	m := NewConfirmationModel("run-1", action,
		`Run 'mcp_home_assistant_HassTurnOn' with name="Kitchen Lights"?`, "").
		WithLiveChannel("c-1", "" /* no always_scope */)
	m.SetWidth(80)
	view := ansi.Strip(m.View())

	for _, want := range []string{
		"Allow Home Assistant: turn on?",
		`Turn on with name="Kitchen Lights"?`,
		// Turning on can open a garage door, so it keeps the cautious badge.
		"DESTRUCTIVE",
	} {
		if !strings.Contains(view, want) {
			t.Errorf("prompt is missing %q:\n%s", want, view)
		}
	}
	if strings.Contains(view, action) {
		t.Errorf("the raw tool id is still shown:\n%s", view)
	}
	if strings.Contains(view, "always") {
		t.Errorf("no always_scope was sent, so always must not be offered:\n%s", view)
	}
	// "a" must not grant anything either.
	m, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("a")})
	if m.State() != ConfirmationPending {
		t.Errorf("'a' resolved the prompt to %v", m.State())
	}
}

func TestHomeAssistantVerb(t *testing.T) {
	for action, want := range map[string]string{
		"mcp_home_assistant_HassTurnOn":                "Turn on",
		"mcp_home_assistant_HassLightSet":              "Light set",
		"mcp_home_assistant_GetLiveContext":            "Get live context",
		"mcp_home_assistant_HassClimateSetTemperature": "Climate set temperature",
	} {
		if got, ok := homeAssistantVerb(action); !ok || got != want {
			t.Errorf("homeAssistantVerb(%q) = %q, %v; want %q", action, got, ok, want)
		}
	}
	for _, action := range []string{"run_shell_command", "mcp_home_assistant_", "mcp_github_create_issue"} {
		if _, ok := homeAssistantVerb(action); ok {
			t.Errorf("%q is not a Home Assistant tool", action)
		}
	}
	// Anything else keeps the cautious default.
	if ClassifyActionRisk("mcp_github_create_issue") != RiskDestructive {
		t.Error("an unknown MCP tool must stay destructive")
	}
}

func TestHomeAssistantServerTitleNamesTheQuestion(t *testing.T) {
	m := NewConfirmationModel("r", "mcp_home_assistant_intent__HassTurnOff",
		`Home Assistant: Turn off with name="Kitchen Lights"?`, "").
		WithToolTitle("Home Assistant", "Turn off")
	m.SetWidth(80)
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "Allow Home Assistant: turn off?") {
		t.Fatalf("title should use the server's own name for the tool:\n%s", view)
	}
	if strings.Contains(view, "intent") {
		t.Fatalf("the namespaced tool id leaked into the prompt:\n%s", view)
	}
}

func TestNamespacedHomeAssistantToolsReadAsVerbs(t *testing.T) {
	for action, want := range map[string]string{
		"mcp_home_assistant_intent__HassTurnOff":                "Allow Home Assistant: turn off?",
		"mcp_home_assistant_climate__HassClimateSetTemperature": "Allow Home Assistant: climate set temperature?",
		"mcp_home_assistant_HassTurnOn":                         "Allow Home Assistant: turn on?",
	} {
		if got := ActionTitle(action); got != want {
			t.Errorf("ActionTitle(%q) = %q, want %q", action, got, want)
		}
	}
}

func TestHomeAssistantRiskKeepsOnOffCautious(t *testing.T) {
	for action, want := range map[string]RiskTier{
		"mcp_home_assistant_light__HassLightSet":     RiskWrite,
		"mcp_home_assistant_HassSetVolume":           RiskWrite,
		"mcp_home_assistant_intent__HassTurnOff":     RiskDestructive, // unlocks a lock
		"mcp_home_assistant_intent__HassTurnOn":      RiskDestructive,
		"mcp_home_assistant_intent__HassSetPosition": RiskDestructive, // garage doors
		"mcp_home_assistant_script__open_gate":       RiskDestructive,
	} {
		if got := ClassifyActionRisk(action); got != want {
			t.Errorf("ClassifyActionRisk(%q) = %v, want %v", action, got, want)
		}
	}
}
