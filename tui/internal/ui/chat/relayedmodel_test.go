// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/providers"
)

// relayClient is the daemon relay as the chat sees it: turns go through Send,
// and a model switch is a setting on the connection.
type relayClient struct {
	queryCapturingClient
	checkErr  error
	turnModel string
}

func (c *relayClient) CheckModelSwitch(context.Context) error { return c.checkErr }
func (c *relayClient) SetTurnModel(model string)              { c.turnModel = model }
func (c *relayClient) TurnModel() string                      { return c.turnModel }

// finishTurn completes a turn the way the relay does, with a final.
func finishTurn(t *testing.T, m ChatModel) ChatModel {
	t.Helper()
	return deliver(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "ok"})
}

const (
	localModel  = "Qwen3-4B-Instruct-GGUF"
	gatewayMdl  = "amd.DeepSeek-V4.1-Flash"
	missingMdl  = "Llama-9-Unreleased-GGUF"
	lemonadeAPI = "/api/v1"
)

// fakeLemonade serves the model listing the switch is checked against.
func fakeLemonade(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != lemonadeAPI+"/models" {
			http.NotFound(w, r)
			return
		}
		fmt.Fprintf(w, `{"data":[
			{"id":%q,"recipe":"llamacpp","downloaded":true},
			{"id":%q,"recipe":"cloud","cloud_provider":"amd"},
			{"id":"Gemma-4-E4B-it-GGUF","recipe":"llamacpp","downloaded":false}]}`,
			localModel, gatewayMdl)
	}))
	t.Cleanup(srv.Close)
	return srv.URL + lemonadeAPI
}

func relayedChat(t *testing.T) (ChatModel, *relayClient, *[]savedCall) {
	t.Helper()
	c := &relayClient{}
	var saves []savedCall
	m := NewChatModelForFlagship(c, "gaia", "GAIA", true).
		WithModelMemory(func(p, id string) error {
			saves = append(saves, savedCall{p, id})
			return nil
		})
	m.width, m.height = 120, 30
	m.lemonadeBaseURL = fakeLemonade(t)
	return m, c, &saves
}

// runRelayed delivers what a relayed switch scheduled, the way the program does.
func runRelayed(t *testing.T, m ChatModel, cmd tea.Cmd) ChatModel {
	t.Helper()
	msg := findBatchedMsg(cmd, func(msg tea.Msg) bool {
		_, ok := msg.(relayedSwitchMsg)
		return ok
	})
	if msg == nil {
		t.Fatal("no relayed switch was scheduled")
	}
	updated, _ := m.Update(msg)
	m = updated.(ChatModel)
	if m.streaming && len(m.queued) == 0 && !m.hasUserMessage() {
		t.Fatal("the switch started a turn of its own")
	}
	return m
}

func submitRelayed(t *testing.T, m ChatModel, line string) ChatModel {
	t.Helper()
	updated, cmd := m.submit(line)
	m = updated.(ChatModel)
	return runRelayed(t, m, cmd)
}

func TestRelayedModelSwitchIsNeverSentAsAQuestion(t *testing.T) {
	m, c, saves := relayedChat(t)
	m = submitRelayed(t, m, "/model "+localModel)

	if len(c.sent) != 0 {
		t.Fatalf("the relay was sent %q as a question", c.sent)
	}
	if c.turnModel != localModel {
		t.Fatalf("the next turn runs on %q, want %q", c.turnModel, localModel)
	}
	if h := ansi.Strip(m.renderModelChip()); !strings.Contains(h, localModel) {
		t.Errorf("the header does not name the new model: %q", h)
	}
	if errs := errorsIn(m); len(errs) != 0 {
		t.Errorf("a good switch reported errors: %q", errs)
	}
	// Remembered only once a turn has completed on it, as on the direct route.
	if len(*saves) != 0 {
		t.Fatalf("the switch was saved before the agent ran a turn on it: %+v", *saves)
	}
	m = finishTurn(t, m)
	if len(*saves) != 1 || (*saves)[0] != (savedCall{"local", localModel}) {
		t.Errorf("the switch was not remembered after a turn completed on it: %+v", *saves)
	}
}

// The agent is the authority: a switch it refuses puts the old model back,
// rather than sending every later turn to the same refusal.
func TestASwitchTheAgentRefusesIsUndone(t *testing.T) {
	m, c, saves := relayedChat(t)
	m = submitRelayed(t, m, "/model "+gatewayMdl)
	m = finishTurn(t, m)
	*saves = nil
	m = submitRelayed(t, m, "/model "+localModel)

	m.turnSeq = 7
	updated, _ := m.Update(errMsg{turnSeq: 7, err: &client.ModelSwitchRefusedError{
		Model: localModel, Detail: "could not switch session s to '" + localModel + "': out of memory",
	}})
	m = updated.(ChatModel)

	if c.turnModel != gatewayMdl {
		t.Fatalf("after the refusal the turns run on %q, want the previous %q", c.turnModel, gatewayMdl)
	}
	if h := ansi.Strip(m.renderModelChip()); !strings.Contains(h, "DeepSeek-V4.1-Flash") {
		t.Errorf("the header still names the refused model: %q", h)
	}
	if len(*saves) != 0 {
		t.Errorf("a refused switch was remembered: %+v", *saves)
	}
	if !strings.Contains(m.messages[len(m.messages)-1].Content, "still on "+gatewayMdl) {
		t.Errorf("the transcript does not say what the session is still on: %+v", m.messages[len(m.messages)-1])
	}
}

// The id goes out spelled the way Lemonade lists it.
func TestASwitchUsesLemonadesSpellingOfTheID(t *testing.T) {
	m, c, _ := relayedChat(t)
	submitRelayed(t, m, "/model "+strings.ToLower(localModel))
	if c.turnModel != localModel {
		t.Fatalf("turn model = %q, want Lemonade's %q", c.turnModel, localModel)
	}
}

func TestRelayedSwitchToACloudModelNamesTheProvider(t *testing.T) {
	m, c, saves := relayedChat(t)
	m = submitRelayed(t, m, "/model "+gatewayMdl)
	if c.turnModel != gatewayMdl || len(c.sent) != 0 {
		t.Fatalf("turn model %q, sent %q", c.turnModel, c.sent)
	}
	if h := ansi.Strip(m.renderModelChip()); !strings.Contains(h, "AMD LLM Gateway · DeepSeek-V4.1-Flash (remote)") {
		t.Errorf("header = %q", h)
	}
	m = finishTurn(t, m)
	if len(*saves) != 1 || (*saves)[0] != (savedCall{"amd", gatewayMdl}) {
		t.Errorf("saved %+v", *saves)
	}
}

func TestRelayedSwitchToAModelLemonadeDoesNotServeChangesNothing(t *testing.T) {
	m, c, saves := relayedChat(t)
	m = submitRelayed(t, m, "/model "+missingMdl)
	if c.turnModel != "" || len(c.sent) != 0 || len(*saves) != 0 {
		t.Fatalf("a refused switch changed something: turn %q, sent %q, saves %+v", c.turnModel, c.sent, *saves)
	}
	errs := errorsIn(m)
	if len(errs) != 1 || !strings.Contains(errs[0], "not downloaded") || !strings.Contains(errs[0], "Nothing was switched") {
		t.Fatalf("the refusal = %q", errs)
	}
}

func TestATooOldAgentRefusesTheSwitchWithItsReason(t *testing.T) {
	m, c, _ := relayedChat(t)
	c.checkErr = errors.New("the installed 'gaia' agent speaks contract 2.13 — update it")
	m = submitRelayed(t, m, "/model "+localModel)
	if c.turnModel != "" || len(c.sent) != 0 {
		t.Fatal("a switch the agent cannot take was made anyway")
	}
	if errs := errorsIn(m); len(errs) != 1 || !strings.Contains(errs[0], "2.13") {
		t.Fatalf("the refusal does not carry the agent's reason: %q", errs)
	}
}

func TestBareModelOnTheRelayOpensThePicker(t *testing.T) {
	m, c, _ := relayedChat(t)
	updated, _ := m.submit("/model")
	m = updated.(ChatModel)
	if m.providerPanel == nil || len(c.sent) != 0 {
		t.Fatalf("bare /model: panel %v, sent %q", m.providerPanel != nil, c.sent)
	}
}

// A gateway user's enter used to land on Local and its 165-model download list.
func TestTheModelPickerOpensOnTheProviderChatUses(t *testing.T) {
	for _, query := range []string{"/model", "/provider"} {
		m, _, _ := relayedChat(t)
		m.modelBackend = "amd"
		updated, _ := m.submit(query)
		m = updated.(ChatModel)
		if m.providerPanel == nil {
			t.Fatalf("%s did not open the panel", query)
		}
		if view := m.providerPanel.View(); !strings.Contains(view, "› AMD LLM Gateway") {
			t.Fatalf("%s opened on another provider:\n%s", query, view)
		}
	}
}

// /provider ends in a pick, and the pick used to be sent on as `/model <id>`.
func TestAPickFromTheProviderPanelSwitchesWithoutATurn(t *testing.T) {
	m, c, _ := relayedChat(t)
	updated, _ := m.submit("/provider")
	m = updated.(ChatModel)
	if m.providerPanel == nil {
		t.Fatal("/provider did not open the panel")
	}
	updated, cmd := m.Update(providers.SelectedMsg{ID: localModel})
	m = runRelayed(t, updated.(ChatModel), cmd)
	if len(c.sent) != 0 || c.turnModel != localModel {
		t.Fatalf("the pick: sent %q, turn model %q", c.sent, c.turnModel)
	}
}

// A remembered LOCAL model used to fail its restore on every relayed launch.
func TestARememberedLocalModelIsRestoredOnTheRelay(t *testing.T) {
	m, c, _ := relayedChat(t)
	m = m.WithStartupModel(StartupRestore, "local", localModel)
	updated, cmd := m.Update(startupModelMsg{})
	m = runRelayed(t, updated.(ChatModel), cmd)

	if len(c.sent) != 0 || c.turnModel != localModel {
		t.Fatalf("restore: sent %q, turn model %q", c.sent, c.turnModel)
	}
	if errs := errorsIn(m); len(errs) != 0 {
		t.Fatalf("a good restore reported %q", errs)
	}
	if m.providerPanel != nil {
		t.Error("a good restore opened the picker")
	}
	if h := ansi.Strip(m.renderModelChip()); !strings.Contains(h, localModel) {
		t.Errorf("header = %q", h)
	}
}

// A provider that lists models has a working key, so a mistyped id must not be
// blamed on the key.
func TestARelayedSwitchToAnIDTheProviderLacksBlamesTheName(t *testing.T) {
	m, c, _ := relayedChat(t)
	m = submitRelayed(t, m, "/model amd.not-a-real-model")
	if c.turnModel != "" || len(c.sent) != 0 {
		t.Fatalf("a refused switch changed something: turn %q, sent %q", c.turnModel, c.sent)
	}
	errs := errorsIn(m)
	if len(errs) != 1 || !strings.Contains(errs[0], "does not offer a model with that id") ||
		strings.Contains(errs[0], "key may be missing") {
		t.Fatalf("the refusal = %q", errs)
	}
}

func TestARelayedRestoreThatCannotTakeHandsBackTheChoice(t *testing.T) {
	m, c, _ := relayedChat(t)
	m = m.WithStartupModel(StartupRestore, "local", missingMdl)
	updated, cmd := m.Update(startupModelMsg{})
	m = runRelayed(t, updated.(ChatModel), cmd)

	errs := errorsIn(m)
	want := "Couldn't restore your last model, " + missingMdl + ": it is not downloaded"
	if len(errs) != 1 || !strings.HasPrefix(errs[0], want) {
		t.Fatalf("errors = %q, want one starting %q", errs, want)
	}
	if m.providerPanel == nil {
		t.Error("a failed restore did not open the picker")
	}
	if len(c.sent) != 0 || c.turnModel != "" {
		t.Fatalf("a failed restore changed the session: sent %q, turn model %q", c.sent, c.turnModel)
	}
}

// A question typed while the switch is checked runs on the new model.
func TestAQuestionTypedDuringTheSwitchWaitsForIt(t *testing.T) {
	m, c, _ := relayedChat(t)
	updated, cmd := m.submit("/model " + localModel)
	m = updated.(ChatModel)
	updated, _ = m.Update(sendQueryMsg{query: "hello"})
	m = updated.(ChatModel)
	if len(c.sent) != 0 {
		t.Fatal("a question went out before the switch was applied")
	}
	m = runRelayed(t, m, cmd)
	if c.turnModel != localModel || !m.streaming || len(m.queued) != 0 || !m.hasUserMessage() {
		t.Fatalf("after the switch the question did not go out on %q: turn model %q, streaming %v, queued %q",
			localModel, c.turnModel, m.streaming, m.queued)
	}
}
