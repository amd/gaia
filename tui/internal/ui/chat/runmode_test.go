// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

const (
	modeLocalModel = "Qwen3.5-2B-GGUF"
	modeCloudModel = "fireworks.glm-5p3-flash"
)

// modeChat is a flagship chat that has run turns on model, with run modes saved as state.
func modeChat(t *testing.T, model string, remote bool, state preflight.RunModeState) (ChatModel, *queryCapturingClient) {
	t.Helper()
	c := &queryCapturingClient{}
	m := NewChatModel(c, "gaia", "").WithRunModes(func() (preflight.RunModeState, error) { return state, nil })
	m.width, m.height = 120, 30
	backend := "lemonade"
	if remote {
		backend = "fireworks"
	}
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", ModelID: model, ModelDisplay: model, ModelBackend: backend, ModelRemote: remote})
	return m, c
}

func savedModes() preflight.RunModeState {
	return preflight.RunModeState{Saved: map[string]string{preflight.LocalMode: modeLocalModel, preflight.CloudMode: modeCloudModel}}
}

// dispatch runs the turn a submit started, so what it sends reaches the client.
func dispatch(t *testing.T, cmd tea.Cmd) {
	t.Helper()
	if findBatchedMsg(cmd, func(msg tea.Msg) bool { _, ok := msg.(channelReadyMsg); return ok }) == nil {
		t.Fatal("no turn was dispatched to the agent")
	}
}

func lastMessage(m ChatModel) Message { return m.messages[len(m.messages)-1] }

func TestTheHeaderLeadsWithTheRunMode(t *testing.T) {
	local, _ := modeChat(t, modeLocalModel, false, savedModes())
	if h := ansi.Strip(local.renderModelChip()); h != " │ Local · "+modeLocalModel {
		t.Errorf("local header = %q", h)
	}
	cloud, _ := modeChat(t, modeCloudModel, true, savedModes())
	if h := ansi.Strip(cloud.renderModelChip()); h != " │ Cloud · Fireworks AI · glm-5p3-flash" {
		t.Errorf("cloud header = %q", h)
	}
}

func TestModeAloneListsBothModesAndWhichIsInUse(t *testing.T) {
	m, c := modeChat(t, modeLocalModel, false, savedModes())
	updated, _ := m.submit("/mode")
	m = updated.(ChatModel)
	got := lastMessage(m)
	if got.Role != RoleStatus {
		t.Fatalf("role = %q, want a status line", got.Role)
	}
	for _, want := range []string{
		"Local · " + modeLocalModel + "  (in use)",
		"Cloud · Fireworks AI · glm-5p3-flash",
		"Cloud moves chat only",
		"/mode local or /mode cloud",
	} {
		if !strings.Contains(got.Content, want) {
			t.Errorf("listing lacks %q:\n%s", want, got.Content)
		}
	}
	if len(c.sent) != 0 {
		t.Errorf("listing the modes sent %q to the agent", c.sent)
	}
}

func TestSwitchingModeRunsThatModesModelThroughModel(t *testing.T) {
	m, c := modeChat(t, modeLocalModel, false, savedModes())
	updated, cmd := m.submit("/mode cloud")
	m = updated.(ChatModel)
	dispatch(t, cmd)
	if len(c.sent) != 1 || c.sent[0] != "/model "+modeCloudModel {
		t.Fatalf("sent %q, want one /model %s", c.sent, modeCloudModel)
	}
	if !m.awaitingModelSwitch || m.switchTarget != modeCloudModel {
		t.Errorf("the switch is not awaited like /model's: awaiting=%v target=%q", m.awaitingModelSwitch, m.switchTarget)
	}
}

func TestSwitchingToAModeWithNothingSavedUsesItsDefault(t *testing.T) {
	m, c := modeChat(t, modeCloudModel, true, preflight.RunModeState{Saved: map[string]string{}})
	_, cmd := m.submit("/mode Local")
	dispatch(t, cmd)
	if len(c.sent) != 1 || c.sent[0] != "/model "+preflight.DefaultLocalModel {
		t.Fatalf("sent %q, want /model %s", c.sent, preflight.DefaultLocalModel)
	}
}

func TestTheModeInUseIsNotSwitchedAgain(t *testing.T) {
	m, c := modeChat(t, modeCloudModel, true, savedModes())
	updated, _ := m.submit("/mode cloud")
	m = updated.(ChatModel)
	if len(c.sent) != 0 {
		t.Fatalf("sent %q for the mode already in use", c.sent)
	}
	if got := lastMessage(m).Content; !strings.Contains(got, "Already in Cloud on Fireworks AI · glm-5p3-flash") {
		t.Errorf("message = %q", got)
	}
}

func TestAnUnknownModeNamesTheValidOnes(t *testing.T) {
	m, c := modeChat(t, modeLocalModel, false, savedModes())
	updated, _ := m.submit("/mode hybrid")
	m = updated.(ChatModel)
	got := lastMessage(m)
	if got.Role != RoleError || !strings.Contains(got.Content, "/mode local or /mode cloud") || len(c.sent) != 0 {
		t.Fatalf("got %q (%s), sent %q", got.Content, got.Role, c.sent)
	}
}

func TestUnreadableModesAreSaidNotGuessed(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModel(c, "gaia", "").WithRunModes(func() (preflight.RunModeState, error) {
		return preflight.RunModeState{}, errors.New("config.json: 'run_profiles' must map local or cloud")
	})
	updated, _ := m.submit("/mode cloud")
	m = updated.(ChatModel)
	got := lastMessage(m)
	if got.Role != RoleError || !strings.Contains(got.Content, "run_profiles") || len(c.sent) != 0 {
		t.Fatalf("got %q (%s), sent %q", got.Content, got.Role, c.sent)
	}
}

func TestOnlyTheFlagshipHasRunModes(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModel(c, "notes", "")
	updated, _ := m.submit("/mode cloud")
	m = updated.(ChatModel)
	got := lastMessage(m)
	if got.Role != RoleError || !strings.Contains(got.Content, "only the gaia flagship agent") || len(c.sent) != 0 {
		t.Fatalf("got %q (%s), sent %q", got.Content, got.Role, c.sent)
	}
	if m.availableCommandSet()["/mode"] {
		t.Error("/mode is offered in the palette of an agent that can't run it")
	}
}

func TestTypingACommandInFullRunsThatCommandNotALongerOne(t *testing.T) {
	for typed, want := range map[string]string{"/mode": "/mode", "/model": "/model", "/models": "/models"} {
		m := typeInto(t, gaiaTestModel(t), typed)
		if names := paletteNames(m.paletteFiltered()); len(names) == 0 || names[m.palette.selected] != want {
			t.Errorf("typing %q highlights %v[%d], want %q first", typed, names, m.palette.selected, want)
		}
	}
}

func TestBeforeTheAgentRepliesTheModeNamesTheModelTheGateLoaded(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModelForFlagship(c, "gaia", "GAIA", true).
		WithExpectedModel(modeLocalModel).
		WithRunModes(func() (preflight.RunModeState, error) { return savedModes(), nil })
	m.width, m.height = 120, 30
	updated, _ := m.submit("/mode")
	m = updated.(ChatModel)
	if got := lastMessage(m).Content; !strings.Contains(got, "Local · "+modeLocalModel+"  (in use)") {
		t.Errorf("listing does not name the loaded model:\n%s", got)
	}
	updated, _ = m.submit("/mode local")
	m = updated.(ChatModel)
	if got := lastMessage(m).Content; got != "Already in Local on "+modeLocalModel+"." || len(c.sent) != 0 {
		t.Errorf("got %q, sent %q", got, c.sent)
	}
}

func TestCloudIsRefusedUnderLocalOnly(t *testing.T) {
	policy := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(policy, []byte(`{"network_policy":"local_ai_only"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_POLICY_FILE", policy)
	m, c := modeChat(t, modeLocalModel, false, savedModes())
	updated, _ := m.submit("/mode cloud")
	m = updated.(ChatModel)
	got := lastMessage(m)
	if got.Role != RoleError || !strings.Contains(got.Content, "Local only is on") || len(c.sent) != 0 {
		t.Fatalf("got %q (%s), sent %q", got.Content, got.Role, c.sent)
	}
	updated, _ = m.submit("/mode")
	m = updated.(ChatModel)
	if listing := lastMessage(m).Content; strings.Contains(listing, "/mode cloud") {
		t.Errorf("the listing still offers /mode cloud under Local only:\n%s", listing)
	}
}
