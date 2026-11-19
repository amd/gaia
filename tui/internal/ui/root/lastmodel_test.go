// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/providers"
)

const flashID = "fireworks.deepseek-v4p1-flash"

var savedFlash = preflight.LastModel{Provider: "fireworks", Model: flashID}

// launchedModel is the --model the agent child was built with.
func launchedModel(t *testing.T, m FlagshipModel) string {
	t.Helper()
	c, ok := m.chatClient.c.(interface{ ModelAtLaunch() string })
	if !ok {
		t.Fatalf("client %T cannot report its launch model", m.chatClient.c)
	}
	return c.ModelAtLaunch()
}

func headerOf(m FlagshipModel) string {
	sized, _ := m.chat.Update(tea.WindowSizeMsg{Width: 120, Height: 30})
	return strings.SplitN(ansi.Strip(sized.View()), "\n", 2)[0]
}

func TestSavedLemonadeModelIsWhatTheFlagshipStartsOn(t *testing.T) {
	agent := fixtureAgent(t, catalog.FlagshipID)
	m := NewFlagshipModel(agent, false).WithSavedModel(savedFlash)
	// The gate checks the restored model: a cloud one skips the local load.
	if opts := m.localOptions(agent); opts.Model != flashID || opts.ClaudeMode {
		t.Fatalf("readiness would check %+v, not the restored model", opts)
	}
	updated, _ := m.launchAgent(agent, true)
	m = updated.(FlagshipModel)
	if got := launchedModel(t, m); got != flashID {
		t.Fatalf("agent started on %q, not the restored model", got)
	}
	if h := headerOf(m); !strings.Contains(h, "Fireworks AI · deepseek-v4p1-flash") {
		t.Fatalf("header does not name the restored model: %q", h)
	}
}

func TestExplicitFlagsWinOverTheSavedModel(t *testing.T) {
	agent := fixtureAgent(t, catalog.FlagshipID)
	for name, m := range map[string]FlagshipModel{
		"--model":      NewFlagshipModel(agent, false).WithModel("Qwen3-4B-GGUF"),
		"--use-claude": NewFlagshipModel(agent, false).WithClaude(true, "claude-sonnet-5"),
	} {
		t.Run(name, func(t *testing.T) {
			m = m.WithSavedModel(savedFlash)
			if _, _, ok := m.restoring(agent); ok || m.launchModel(agent) == flashID {
				t.Fatal("a launch flag lost to the saved choice")
			}
		})
	}
}

// The restored model belongs to the flagship. Another agent started on it
// would run the wrong model (daemon) or refuse to start (subprocess).
func TestSavedModelNeverReachesAnotherAgent(t *testing.T) {
	other := fixtureAgent(t, "other")
	m := NewFlagshipModel(fixtureAgent(t, catalog.FlagshipID), false).WithSavedModel(savedFlash)
	if got := m.launchModel(other); got != "" {
		t.Fatalf("another agent would launch on %q", got)
	}
	if opts := m.localOptions(other); opts.Model != "" {
		t.Fatalf("another agent's gate would check %q", opts.Model)
	}
	updated, _ := m.launchAgent(other, true)
	m = updated.(FlagshipModel)
	if got := launchedModel(t, m); got != "" {
		t.Fatalf("another agent started on %q", got)
	}
}

// Switching back to the flagship restores the latest confirmed choice, not the
// one read at launch.
func TestSwitchingBackRestoresTheLatestConfirmedChoice(t *testing.T) {
	agent := fixtureAgent(t, catalog.FlagshipID)
	var saved []string
	m := NewFlagshipModel(agent, false).
		WithModelMemory(func(_, model string) error { saved = append(saved, model); return nil }).
		WithSavedModel(savedFlash)
	m.saveModel("amd", "amd.gpt-4.1") // what the chat calls after a confirmed /model
	if len(saved) != 1 {
		t.Fatal("a confirmed switch was not written through")
	}
	updated, _ := m.launchAgent(agent, true)
	m = updated.(FlagshipModel)
	if got := launchedModel(t, m); got != "amd.gpt-4.1" {
		t.Fatalf("relaunched on %q, want the latest confirmed choice", got)
	}
}

// A saved local or Claude model is switched to by the opening /model turn, so
// a deleted model or a missing key reaches the chat's one-line refusal instead
// of halting the launch at the gate.
func TestSavedLocalAndClaudeModelsAreAppliedByTheAgentNotTheGate(t *testing.T) {
	agent := fixtureAgent(t, catalog.FlagshipID)
	for _, saved := range []preflight.LastModel{
		{Provider: "claude", Model: "claude-sonnet-5"},
		{Provider: "local", Model: "Qwen3-4B-GGUF"},
	} {
		t.Run(saved.Model, func(t *testing.T) {
			m := NewFlagshipModel(agent, false).WithSavedModel(saved)
			if m.launchModel(agent) != "" || m.useClaude {
				t.Fatal("the agent must start on its default and switch after")
			}
			if opts := m.localOptions(agent); opts.Model != "" || opts.ClaudeMode {
				t.Fatalf("the gate must check what the agent starts on, got %+v", opts)
			}
			if _, model, ok := m.restoring(agent); !ok || model != saved.Model {
				t.Fatal("the choice must still be restored by the opening /model turn")
			}
		})
	}
}

func TestTheModelIDWinsOverAContradictingProvider(t *testing.T) {
	for _, tc := range []struct{ provider, model, want string }{
		{"fireworks", "claude-sonnet-5", "claude"},
		{"claude", "Gemma-4-E4B-it-GGUF", "local"},
		{"local", "amd.gpt-4.1", "amd"},
		{"acme", "acme-model", "acme"},
	} {
		if got := providerOf(tc.provider, tc.model); got != tc.want {
			t.Errorf("providerOf(%q, %q) = %q, want %q", tc.provider, tc.model, got, tc.want)
		}
	}
}

// A gate pick is for the launch it was made on. After that the remembered
// choice decides — including one changed in chat — and no other agent gets it.
func TestAGatePickDoesNotOutliveItsLaunch(t *testing.T) {
	agent := fixtureAgent(t, catalog.FlagshipID)
	m := NewFlagshipModel(agent, false).WithModelMemory(nil)
	m.model = "amd.gpt-4.1"
	m = m.pickedAtGate("amd.gpt-4.1")
	updated, _ := m.launchAgent(agent, true)
	m = updated.(FlagshipModel)
	if got := launchedModel(t, m); got != "amd.gpt-4.1" {
		t.Fatalf("the pick was not launched: %q", got)
	}
	m.saveModel("fireworks", flashID) // a later /model switch in chat
	if got := m.launchModel(fixtureAgent(t, "other")); got != "" {
		t.Fatalf("another agent would launch on %q", got)
	}
	if got := m.launchModel(agent); got != flashID {
		t.Fatalf("switching back relaunches on %q, want the latest confirmed choice", got)
	}
}

func TestAPickAtTheGateIsHandedToTheChat(t *testing.T) {
	m := providerBlockedRoot(t)
	panel := providers.New("", 80, 24)
	m.providerPanel = &panel
	updated, _ := m.Update(providers.SelectedMsg{ID: "amd.gpt-4.1"})
	m = updated.(FlagshipModel)
	defer m.preflight.Cancel()
	if m.startupProvider != "amd" || m.startupModel != "amd.gpt-4.1" {
		t.Fatalf("gate pick not handed on: provider=%q model=%q", m.startupProvider, m.startupModel)
	}
}

func TestHeaderNamesTheModelTheGateLoaded(t *testing.T) {
	agent := fixtureAgent(t, catalog.FlagshipID)
	m := NewFlagshipModel(agent, false)
	m.gateChatModel = "Gemma-4-E4B-it-GGUF"
	updated, _ := m.launchAgent(agent, true)
	m = updated.(FlagshipModel)
	if h := headerOf(m); !strings.Contains(h, "│ Gemma-4-E4B-it-GGUF") {
		t.Fatalf("a local session's header must name its model before the first message: %q", h)
	}
}
