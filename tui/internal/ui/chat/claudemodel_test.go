// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
)

// claudeModelTransport is a transport spawned with both --use-claude and
// --claude-model, matching what SubprocessClient reports back off a real
// child's argv.
type claudeModelTransport struct {
	nullClient
	model string
}

func (c *claudeModelTransport) ClaudeAtLaunch() bool        { return true }
func (c *claudeModelTransport) ClaudeModelAtLaunch() string { return c.model }

func claudeLaunchModel(t *testing.T, id string) ChatModel {
	t.Helper()
	m := NewChatModelForCatalogAgent(&claudeModelTransport{model: id}, setupAgentID, "GAIA", false)
	m.width, m.height = 100, 30
	return m
}

// canonicalModelPing is the agent's model-state ping — the header metadata it
// emits at startup and after every switch.
func canonicalModelPing(id, display string, remote bool) event.CanonicalStatusEvent {
	return canonicalModelPingWithLemonade(id, display, remote, true, "http://localhost:13305")
}

func canonicalModelPingWithLemonade(id, display string, remote, lemonadeUp bool, baseURL string) event.CanonicalStatusEvent {
	backend := "lemonade"
	if remote {
		backend = "claude"
	}
	up := lemonadeUp
	return event.CanonicalStatusEvent{
		Type:              "status",
		ModelID:           id,
		ModelDisplay:      display,
		ModelBackend:      backend,
		ModelRemote:       remote,
		LemonadeReachable: &up,
		LemonadeBaseURL:   baseURL,
	}
}

// --- the header names WHICH Claude model ------------------------------------

// The gap this closes: a bare "claude" chip cannot tell Haiku from Sonnet
// from Opus, which differ by an order of magnitude in cost and capability.
func TestHeaderNamesTheLaunchedClaudeModelBeforeAnyTurn(t *testing.T) {
	for _, tc := range []struct{ id, want string }{
		{"claude-haiku-4-5", "claude · haiku-4.5"},
		{"claude-sonnet-5", "claude · sonnet-5"},
		{"claude-opus-5", "claude · opus-5"},
	} {
		header := ansi.Strip(claudeLaunchModel(t, tc.id).renderHeader())
		if !strings.Contains(header, tc.want) {
			t.Errorf("launched on %s: header = %q, want it to contain %q",
				tc.id, header, tc.want)
		}
	}
}

// --use-claude with no model named still says the session is remote — it just
// has nothing more specific to say yet.
func TestHeaderFallsBackToABareClaudeChipWithNoModelFlag(t *testing.T) {
	header := ansi.Strip(claudeLaunchModel(t, "").renderHeader())
	if !strings.Contains(header, "│ claude") {
		t.Errorf("a --use-claude launch must still be marked remote: %q", header)
	}
	if strings.Contains(header, "·") {
		t.Errorf("no model was named, so none may be shown: %q", header)
	}
}

// The launch flag is a REQUEST; the agent's ping is what resolved. When they
// disagree the ping wins, because it is the only one that reflects reality.
func TestTheAgentsPingOverridesTheLaunchFlagInTheHeader(t *testing.T) {
	m := claudeLaunchModel(t, "claude-haiku-4-5")
	m = feed(t, m, canonicalModelPing("claude-opus-5", "Opus 5", true))

	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, "claude · opus-5") {
		t.Errorf("the agent's resolved model must win, got %q", header)
	}
	if strings.Contains(header, "haiku") {
		t.Errorf("the header still shows the launch flag's model: %q", header)
	}
}

// A launch-flagged Claude session that switches to a local model must stop
// looking remote — the chip's text carries that meaning as well as its colour.
func TestSwitchingToALocalModelClearsTheRemoteChip(t *testing.T) {
	m := claudeLaunchModel(t, "claude-haiku-4-5")
	m = feed(t, m, canonicalModelPing("Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF", false))

	header := ansi.Strip(m.renderHeader())
	if strings.Contains(header, "claude ·") {
		t.Errorf("a local session must not carry a claude model chip: %q", header)
	}
	if !strings.Contains(header, "Gemma-4-E4B-it-GGUF") {
		t.Errorf("header must name the local model it switched to: %q", header)
	}
}

// --- /model id validation ---------------------------------------------------

// A typo'd Claude id is refused locally, with the accepted ids, instead of
// being spent on a turn that ends in an opaque 404 from Anthropic.
func TestBadClaudeIDIsRefusedWithoutSendingATurn(t *testing.T) {
	m := gaiaTestModel(t)

	updated, cmd := m.submit("/model claude-haiku-45")
	m = updated.(ChatModel)

	if cmd != nil {
		t.Error("a refused switch must not start a turn")
	}
	if m.awaitingModelSwitch {
		t.Error("a refused switch must not arm the switch state machine")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError {
		t.Fatalf("expected an error message, got %+v", last)
	}
	for _, id := range client.ClaudeModelIDs() {
		if !strings.Contains(last.Content, id) {
			t.Errorf("refusal does not offer %q: %q", id, last.Content)
		}
	}
}

// Every accepted id goes through — the point of the check is to refuse typos,
// not to become a second gate on real models.
func TestEveryKnownClaudeIDIsAcceptedForSwitching(t *testing.T) {
	m := gaiaTestModel(t)
	for _, id := range client.ClaudeModelIDs() {
		if reason := m.refuseModelSwitch(id); reason != "" {
			t.Errorf("%s must be switchable, refused with: %s", id, reason)
		}
	}
}

// A bare `/model` is a LIST request, not a switch — never validated away.
func TestBareModelCommandIsNeverRefused(t *testing.T) {
	m := gaiaTestModel(t)
	if reason := m.refuseModelSwitch(modelCommandArg("/model")); reason != "" {
		t.Errorf("`/model` must always be able to list: %s", reason)
	}
}

func TestModelCommandArg(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"/model", ""},
		{"  /model  ", ""},
		{"/model claude-haiku-4-5", "claude-haiku-4-5"},
		{"/model   claude-opus-5  ", "claude-opus-5"},
	} {
		if got := modelCommandArg(tc.in); got != tc.want {
			t.Errorf("modelCommandArg(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// --- Lemonade down: refuse with both ways forward, never fall back ----------

// Switching to a local model with the local server known-down is a dead end
// the TUI can see coming. It must say what is wrong, both ways out, and that
// nothing changed — and must NOT quietly leave the session somewhere else
// while reporting success.
func TestLocalSwitchWithLemonadeDownIsRefusedActionably(t *testing.T) {
	m := claudeLaunchModel(t, "claude-haiku-4-5")
	m = feed(t, m, canonicalModelPingWithLemonade(
		"claude-haiku-4-5", "Haiku 4.5", true, false, "http://localhost:13305"))

	updated, cmd := m.submit("/model Gemma-4-E4B-it-GGUF")
	m = updated.(ChatModel)

	if cmd != nil {
		t.Error("a switch that cannot succeed must not start a turn")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError {
		t.Fatalf("expected an error message, got %+v", last)
	}
	for _, must := range []string{
		"Lemonade",               // what is wrong
		"http://localhost:13305", // where
		// How to fix it — a command that exists on every machine, NOT a launch
		// command. `lemonade-server serve` was pinned here, and Lemonade 10.7
		// removed it, so this assertion kept a dead remedy looking verified
		// (CLAUDE.md, "Never hardcode how Lemonade is started").
		"gaia daemon status",
		"stay on Claude",       // the other way forward
		"Nothing was switched", // what did NOT happen
	} {
		if !strings.Contains(last.Content, must) {
			t.Errorf("refusal must mention %q: %q", must, last.Content)
		}
	}
	// No silent fallback: the session is still exactly where it was.
	if !m.modelRemote || m.modelID != "claude-haiku-4-5" {
		t.Errorf("a refused switch must leave the session untouched, got %q remote=%v",
			m.modelID, m.modelRemote)
	}
}

// The same refusal on a LOCAL session must not tell the user to "stay remote"
// — it is not remote — and must name what moving to Claude actually costs.
func TestTheRemoteAlternativeIsPhrasedForALocalSession(t *testing.T) {
	m := gaiaTestModel(t)
	m = feed(t, m, canonicalModelPingWithLemonade(
		"Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF", false, false, "http://localhost:13305"))

	updated, _ := m.submit("/model some-other-local-model")
	m = updated.(ChatModel)

	last := m.messages[len(m.messages)-1].Content
	if strings.Contains(last, "stay on Claude") {
		t.Errorf("a local session is not on Claude: %q", last)
	}
	for _, must := range []string{client.ExampleClaudeModelID, "ANTHROPIC_API_KEY"} {
		if !strings.Contains(last, must) {
			t.Errorf("the alternative must mention %q: %q", must, last)
		}
	}
}

// The refusal reads a CACHED snapshot — nothing refreshes it between agent
// pings — so it must fire once and then get out of the way. A user who starts
// Lemonade (in another terminal, or with /setup) and sends the same line again
// has to reach the agent, which probes live. A sticky refusal would be a dead
// end no retry could ever clear.
func TestTheLemonadeDownRefusalDoesNotStickForever(t *testing.T) {
	m := gaiaTestModel(t)
	m = feed(t, m, canonicalModelPingWithLemonade(
		"Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF", false, false, "http://localhost:13305"))

	updated, cmd := m.submit("/model some-other-local-model")
	m = updated.(ChatModel)
	if cmd != nil {
		t.Fatal("precondition: the first attempt should have been refused locally")
	}

	updated, cmd = m.submit("/model some-other-local-model")
	m = updated.(ChatModel)
	if cmd == nil {
		t.Error("the retry must reach the agent — it is the only live probe")
	}
	if !m.awaitingModelSwitch {
		t.Error("the retry must arm the switch state machine")
	}
}

// A fresh ping is fresh evidence, so the one-shot refusal is armed again.
func TestANewPingRearmsTheLemonadeDownRefusal(t *testing.T) {
	m := gaiaTestModel(t)
	down := canonicalModelPingWithLemonade(
		"Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF", false, false, "http://localhost:13305")

	m = feed(t, m, down)
	updated, _ := m.submit("/model some-other-local-model")
	m = updated.(ChatModel)
	if !m.lemonadeDownRefused {
		t.Fatal("precondition: the first refusal should have spent the latch")
	}

	m = feed(t, m, down)
	if m.lemonadeDownRefused {
		t.Error("a new report is new evidence — the refusal must be armed again")
	}
}

// Lemonade's state UNKNOWN is not the same as Lemonade being down: the switch
// goes through to the agent, which is the only thing that can actually answer.
func TestLocalSwitchIsAllowedWhenLemonadeStateIsUnknown(t *testing.T) {
	m := gaiaTestModel(t)
	if m.lemonadeKnown {
		t.Fatal("precondition: a fresh model has not heard about Lemonade yet")
	}
	if reason := m.refuseModelSwitch("Gemma-4-E4B-it-GGUF"); reason != "" {
		t.Errorf("an unknown local state must not pre-refuse: %s", reason)
	}
}

// --- the palette surfaces the models ---------------------------------------

// `/model` takes a free-form id, so the models cannot be flat palette rows.
// The space after the command name opens a second level instead.
func TestTypingTheSpaceAfterModelOpensTheModelPicker(t *testing.T) {
	m := gaiaTestModel(t)
	m = typeInto(t, m, "/model ")

	if !m.palette.open {
		t.Fatal("`/model ` must open the model picker")
	}
	names := paletteNames(m.paletteFiltered())
	for _, id := range client.ClaudeModelIDs() {
		if !containsAll(names, "/model "+id) {
			t.Errorf("the picker does not offer %q, got %v", id, names)
		}
	}
	// Bare /model stays reachable: it is the only way to see LOCAL models,
	// which only the agent can enumerate.
	if !containsAll(names, "/model") {
		t.Errorf("the picker must keep a row that lists every model, got %v", names)
	}
}

// Nobody types "claude-" to find Haiku.
func TestModelPickerMatchesOnTheFamilyName(t *testing.T) {
	m := gaiaTestModel(t)
	m = typeInto(t, m, "/model haiku")

	names := paletteNames(m.paletteFiltered())
	if len(names) != 1 || names[0] != "/model claude-haiku-4-5" {
		t.Errorf(`"/model haiku" should narrow to Haiku alone, got %v`, names)
	}
}

// The top-level palette is unchanged: the models live one level down, so the
// real commands are not buried under a model list nobody asked for.
func TestTheModelPickerDoesNotLeakIntoTheTopLevelPalette(t *testing.T) {
	m := gaiaTestModel(t)
	m = typeInto(t, m, "/mo")

	names := paletteNames(m.paletteFiltered())
	if len(names) != 1 || names[0] != "/model" {
		t.Errorf(`"/mo" must still narrow to just /model, got %v`, names)
	}
}

// Picking a model row runs it as the command it is — not as a chat message.
func TestPickingAModelRowSubmitsTheSwitch(t *testing.T) {
	m := claudeLaunchModel(t, "claude-sonnet-5")
	m = typeInto(t, m, "/model haiku")

	updated, _, _ := m.handlePaletteKey(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated

	if !m.awaitingModelSwitch {
		t.Error("picking a model row must dispatch the switch")
	}
	for _, msg := range m.messages {
		if msg.Role == RoleUser {
			t.Errorf("a picked model must never post as a chat message: %+v", msg)
		}
	}
}
