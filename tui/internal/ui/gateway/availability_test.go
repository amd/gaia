package gateway

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

const (
	brokenModel  = Provider + ".GLM-5.2-FP8"
	workingModel = Provider + ".DeepSeek-V4.1-Flash"
)

// seedState writes a gateway.json as the Python client leaves it after a 404,
// and points GAIA's config.json at the temp dir too.
func seedState(t *testing.T, active string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "gateway.json")
	t.Setenv("GAIA_GATEWAY_FILE", path)
	t.Setenv("GAIA_CONFIG_FILE", filepath.Join(dir, "config.json"))
	seed := map[string]any{
		"base_url":       "https://x/v1",
		"enabled_models": []string{},
		"unavailable_models": map[string]any{
			brokenModel: map[string]any{"status": 404, "at": "2026-10-14T09:30:00Z"},
		},
	}
	if active != "" {
		seed["active_model"] = active
		seed["enabled_models"] = []string{active}
	}
	raw, _ := json.Marshal(seed)
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func modelsScreen(t *testing.T) GatewayModel {
	t.Helper()
	m := New(&Client{}, nil)
	if m.initErr != nil {
		t.Fatalf("New: %v", m.initErr)
	}
	m.haveInit = true
	m.stage = stageModels
	next, _ := m.Update(modelsMsg{models: []Model{{ID: brokenModel}, {ID: workingModel}}})
	return next.(GatewayModel)
}

func TestAutoSelectSkipsAModelTheGatewayLastAnswered404For(t *testing.T) {
	seedState(t, "")
	m := modelsScreen(t)

	if got := m.state.ActiveModel; got != workingModel {
		t.Fatalf("auto-selected %q, want %q (the broken model ranks first)", got, workingModel)
	}
	if m.availability != "" {
		t.Errorf("no warning expected for a working active model, got %q", m.availability)
	}
}

func TestAnUnavailableActiveModelIsMarkedAndKept(t *testing.T) {
	seedState(t, brokenModel)
	m := modelsScreen(t)

	// Never switched for the user: the choice stays theirs.
	if got := m.state.ActiveModel; got != brokenModel {
		t.Fatalf("active model changed to %q; it must stay %q", got, brokenModel)
	}
	view := ansi.Strip(m.View())
	var brokenRow, workingRow string
	for _, line := range strings.Split(view, "\n") {
		if strings.Contains(line, "[") && strings.Contains(line, brokenModel) {
			brokenRow = line
		}
		if strings.Contains(line, workingModel) {
			workingRow = line
		}
	}
	if !strings.Contains(brokenRow, "(unavailable)") {
		t.Errorf("broken model row not marked unavailable: %q", brokenRow)
	}
	if strings.Contains(workingRow, "(unavailable)") {
		t.Errorf("working model row wrongly marked: %q", workingRow)
	}
	if !strings.Contains(view, "answered HTTP 404 on 2026-10-14 09:30:00 UTC") ||
		!strings.Contains(view, "Press enter on another model to switch") {
		t.Errorf("no plain warning naming the failure and the next step:\n%s", view)
	}
}

func TestUnavailableModelsSurviveATUISave(t *testing.T) {
	path := seedState(t, brokenModel)
	state, err := LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if err := state.SetActive(workingModel).Save(); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	var got map[string]map[string]any
	var all map[string]json.RawMessage
	if err := json.Unmarshal(raw, &all); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(all["unavailable_models"], &got); err != nil {
		t.Fatalf("unavailable_models not carried through: %s", raw)
	}
	if _, ok := got[brokenModel]; !ok {
		t.Errorf("record for %s dropped by the TUI save: %s", brokenModel, raw)
	}
}
