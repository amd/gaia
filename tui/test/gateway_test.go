package test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/gateway"
)

// fakeLemonade stands in for a Lemonade Server with cloud offload. It records
// the requests the screen makes so the tests can assert on their SHAPE — a stub
// that merely returns success would prove a call happened, not that Lemonade
// would accept it.
type fakeLemonade struct {
	server *httptest.Server

	installed     bool
	authenticated bool

	installBody map[string]any
	authBody    map[string]any
}

func newFakeLemonade(t *testing.T) *fakeLemonade {
	t.Helper()
	f := &fakeLemonade{}
	mux := http.NewServeMux()

	mux.HandleFunc("/api/v1/system-info", func(w http.ResponseWriter, r *http.Request) {
		providers := []any{}
		if f.installed {
			providers = append(providers, map[string]any{
				"name":              "amd",
				"base_url":          "https://gw.example.com/api/v1",
				"env_var_set":       false,
				"runtime_key_set":   f.authenticated,
				"models_discovered": f.discovered(),
			})
		}
		writeJSON(w, map[string]any{"cloud": map[string]any{"providers": providers}})
	})

	mux.HandleFunc("/api/v1/install", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&f.installBody)
		f.installed = true
		writeJSON(w, map[string]any{
			"models_discovered": f.discovered(),
			"auth_state": map[string]any{
				"env_var_set":     false,
				"runtime_key_set": f.authenticated,
			},
		})
	})

	mux.HandleFunc("/api/v1/cloud/auth", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&f.authBody)
		f.authenticated = true
		writeJSON(w, map[string]any{"models_discovered": f.discovered()})
	})

	mux.HandleFunc("/api/v1/models", func(w http.ResponseWriter, r *http.Request) {
		data := []any{
			map[string]any{
				"id": "Gemma-4-E4B-it-GGUF", "recipe": "llamacpp",
				"labels": []string{"hot"},
			},
		}
		if f.authenticated {
			data = append(data,
				map[string]any{
					"id": "amd.zephyr-small", "recipe": "cloud",
					"labels": []string{}, "context_length": 8192,
				},
				// The on-prem model the real gateway serves, and the one the
				// preference ranking must put first. Deliberately listed after
				// Claude-Opus-5 so alphabetical order would get it wrong.
				map[string]any{
					"id": "amd.Gemma-4-31B", "recipe": "cloud",
					"labels": []string{"tool-calling"}, "context_length": 131072,
				},
				map[string]any{
					"id": "amd.Claude-Opus-5", "recipe": "cloud",
					"labels": []string{"tool-calling", "vision"}, "context_length": 1000000,
				},
				// Another provider's cloud model must not leak into the list.
				map[string]any{
					"id": "fireworks.kimi", "recipe": "cloud", "labels": []string{},
				},
			)
		}
		writeJSON(w, map[string]any{"data": data})
	})

	f.server = httptest.NewServer(mux)
	t.Cleanup(f.server.Close)
	return f
}

func (f *fakeLemonade) discovered() int {
	if f.authenticated {
		return 3
	}
	return 0
}

func (f *fakeLemonade) baseURL() string { return f.server.URL + "/api/v1" }

// gatewayDriver pumps the gateway screen directly, without going through root.
type gatewayDriver struct {
	t *testing.T
	m gateway.GatewayModel
}

func newGatewayDriver(t *testing.T, fake *fakeLemonade) *gatewayDriver {
	t.Helper()
	// Isolate both files so tests never touch a real ~/.gaia.
	dir := t.TempDir()
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(dir, "gateway.json"))
	t.Setenv("GAIA_CONFIG_FILE", filepath.Join(dir, "config.json"))

	d := &gatewayDriver{t: t, m: gateway.New(gateway.NewClientAt(fake.baseURL()), nil)}
	d.pump(d.m.Init())
	d.send(tea.WindowSizeMsg{Width: 100, Height: 30})
	return d
}

func (d *gatewayDriver) send(msg tea.Msg) {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(gateway.GatewayModel)
	d.pump(cmd)
}

// pump runs commands to completion. isCursorBlink (driver_test.go) filters the
// text input's self-re-arming blink, which would otherwise never settle.
func (d *gatewayDriver) pump(cmd tea.Cmd) {
	d.t.Helper()
	queue := []tea.Cmd{cmd}
	for steps := 0; len(queue) > 0; steps++ {
		if steps > maxPumpSteps {
			d.t.Fatalf("command loop did not settle after %d steps", maxPumpSteps)
		}
		next := queue[0]
		queue = queue[1:]
		if next == nil {
			continue
		}
		msg := next()
		if msg == nil {
			continue
		}
		if batch, ok := msg.(tea.BatchMsg); ok {
			queue = append(queue, batch...)
			continue
		}
		if isCursorBlink(msg) {
			continue
		}
		updated, follow := d.m.Update(msg)
		d.m = updated.(gateway.GatewayModel)
		queue = append(queue, follow)
	}
}

// typeText enters a string one rune at a time, the way a user would.
func (d *gatewayDriver) typeText(text string) {
	d.t.Helper()
	for _, r := range text {
		d.send(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
}

// clearInput empties the focused text field.
func (d *gatewayDriver) clearInput() {
	d.t.Helper()
	for i := 0; i < 120; i++ {
		d.send(keyBackspace())
	}
}

func keySpace() tea.KeyMsg { return tea.KeyMsg{Type: tea.KeySpace} }

// readStateFile returns the raw gateway.json so a test can prove no secret
// reached it.
func readStateFile(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(gateway.StatePath())
	if err != nil {
		t.Fatalf("reading %s: %v", gateway.StatePath(), err)
	}
	return string(raw)
}

// --------------------------------------------------------------------------

func TestGatewayFlowRegistersAuthenticatesAndSelects(t *testing.T) {
	fake := newFakeLemonade(t)
	d := newGatewayDriver(t, fake)

	// Stage 1: base URL. The fake plays the gateway too, so the pre-flight
	// probe stays local — a real hostname here would hit live DNS.
	if !strings.Contains(d.m.View(), "Gateway base URL") {
		t.Fatalf("expected the URL stage first, got:\n%s", d.m.View())
	}
	d.clearInput()
	d.typeText(fake.baseURL())
	d.send(keyEnter())

	if !fake.installed {
		t.Fatalf("enter on the URL stage did not register the provider; screen:\n%s",
			d.m.View())
	}
	// The call must be the shape Lemonade routes on.
	if got := fake.installBody["backend"]; got != "cloud" {
		t.Errorf("install backend = %v, want cloud", got)
	}
	if got := fake.installBody["provider"]; got != "amd" {
		t.Errorf("install provider = %v, want amd", got)
	}
	if got := fake.installBody["base_url"]; got != fake.baseURL() {
		t.Errorf("install base_url = %v, want %v", got, fake.baseURL())
	}
	if got := fake.installBody["wire_format"]; got != "openai" {
		t.Errorf("install wire_format = %v, want openai (GAIA speaks chat completions)", got)
	}

	// Stage 2: token. Nothing is discovered until Lemonade has one.
	view := d.m.View()
	if !strings.Contains(view, "Gateway API token") {
		t.Fatalf("expected the token stage after registering, got:\n%s", view)
	}
	d.typeText("sk-super-secret")

	// The token must not be readable off the rendered frame — the loopback
	// control API returns exactly this string in its snapshot.
	if strings.Contains(d.m.View(), "sk-super-secret") {
		t.Fatal("the token is rendered in plain text; it must be masked")
	}
	d.send(keyEnter())

	if got := fake.authBody["api_key"]; got != "sk-super-secret" {
		t.Errorf("auth api_key = %v, want the typed token", got)
	}
	if got := fake.authBody["provider"]; got != "amd" {
		t.Errorf("auth provider = %v, want amd", got)
	}

	// Stage 3: models.
	view = d.m.View()
	if !strings.Contains(view, "amd.Claude-Opus-5") {
		t.Fatalf("expected discovered models, got:\n%s", view)
	}
	if strings.Contains(view, "fireworks.kimi") {
		t.Error("another provider's cloud model leaked into the gateway list")
	}
	if strings.Contains(view, "Gemma-4-E4B-it-GGUF") {
		t.Error("a local model leaked into the gateway list")
	}
	// Preference order, not alphabetical: Gemma-4-31B leads because it is the
	// only gateway model that streams, then Claude-Opus-5, then the rest.
	if strings.Index(view, "amd.Gemma-4-31B") > strings.Index(view, "amd.Claude-Opus-5") {
		t.Error("Gemma-4-31B should rank above Claude-Opus-5")
	}
	if strings.Index(view, "amd.Claude-Opus-5") > strings.Index(view, "amd.zephyr-small") {
		t.Error("preferred models should sort above the rest")
	}

	// Enable the first model and make it active.
	d.send(keySpace())
	d.send(keyEnter())
	if got := d.m.ActiveModel(); got != "amd.Gemma-4-31B" {
		t.Errorf("active model = %q, want amd.Gemma-4-31B", got)
	}

	// The selection must survive a reload — and carry no secret.
	state, err := gateway.LoadState()
	if err != nil {
		t.Fatalf("reloading state: %v", err)
	}
	if state.ActiveModel != "amd.Gemma-4-31B" {
		t.Errorf("persisted active model = %q", state.ActiveModel)
	}
	if strings.Contains(readStateFile(t), "sk-super-secret") {
		t.Fatal("the token was written to disk; it must only live in Lemonade's memory")
	}

	// Picking a model must actually change what GAIA runs. Without this the
	// selection would live only in gateway.json and the screen would look like
	// it worked while changing nothing.
	var config map[string]any
	raw, err := os.ReadFile(os.Getenv("GAIA_CONFIG_FILE"))
	if err != nil {
		t.Fatalf("selecting a model did not write GAIA's config: %v", err)
	}
	if err := json.Unmarshal(raw, &config); err != nil {
		t.Fatalf("GAIA config is not valid JSON: %v", err)
	}
	if got := config["default_model"]; got != "amd.Gemma-4-31B" {
		t.Errorf("default_model = %v, want amd.Gemma-4-31B", got)
	}
	if strings.Contains(string(raw), "sk-super-secret") {
		t.Fatal("the token leaked into GAIA's config file")
	}
}

func TestGatewayPreservesUnknownConfigKeys(t *testing.T) {
	// gaia.config owns fields this package does not model; rewriting the file
	// must not drop them.
	dir := t.TempDir()
	configPath := filepath.Join(dir, "config.json")
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(dir, "gateway.json"))
	t.Setenv("GAIA_CONFIG_FILE", configPath)
	if err := os.WriteFile(configPath,
		[]byte(`{"profile":"npu","default_device":"npu","default_model":"old"}`),
		0o600); err != nil {
		t.Fatal(err)
	}

	fake := newFakeLemonade(t)
	fake.installed = true
	fake.authenticated = true
	d := &gatewayDriver{t: t, m: gateway.New(gateway.NewClientAt(fake.baseURL()), nil)}
	d.pump(d.m.Init())
	d.send(tea.WindowSizeMsg{Width: 100, Height: 30})
	d.send(keyEnter())

	raw, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	var config map[string]any
	if err := json.Unmarshal(raw, &config); err != nil {
		t.Fatal(err)
	}
	if config["profile"] != "npu" || config["default_device"] != "npu" {
		t.Errorf("unrelated config keys were dropped: %v", config)
	}
	if config["default_model"] != "amd.Gemma-4-31B" {
		t.Errorf("default_model = %v, want the selected model", config["default_model"])
	}
}

func TestGatewaySkipsSetupWhenAlreadyConfigured(t *testing.T) {
	fake := newFakeLemonade(t)
	fake.installed = true
	fake.authenticated = true

	d := newGatewayDriver(t, fake)

	view := d.m.View()
	if strings.Contains(view, "Gateway base URL") {
		t.Error("an already-configured gateway should not re-ask for the URL")
	}
	if !strings.Contains(view, "amd.Claude-Opus-5") {
		t.Fatalf("expected the model list straight away, got:\n%s", view)
	}
}

func TestGatewayEscEmitsClose(t *testing.T) {
	fake := newFakeLemonade(t)
	d := newGatewayDriver(t, fake)

	_, cmd := d.m.Update(keyEsc())
	if cmd == nil {
		t.Fatal("esc produced no command")
	}
	if _, ok := cmd().(gateway.CloseMsg); !ok {
		t.Error("esc should ask the root model to close the screen")
	}
}

func TestGatewayReportsUnreachableLemonade(t *testing.T) {
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(t.TempDir(), "gateway.json"))
	m := gateway.New(nil, errUnreachable{})

	if view := m.View(); !strings.Contains(view, "not reachable") {
		t.Errorf("an unreachable Lemonade should be reported on screen, got:\n%s", view)
	}
}

type errUnreachable struct{}

func (errUnreachable) Error() string {
	return "Lemonade Server is not reachable on port 13305 or 8000."
}

func TestGatewayStateTogglePreservesActive(t *testing.T) {
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(t.TempDir(), "gateway.json"))

	s := gateway.State{}
	s = s.Toggle("amd.a")
	if s.ActiveModel != "amd.a" {
		t.Errorf("first enabled model should become active, got %q", s.ActiveModel)
	}
	s = s.Toggle("amd.b")
	if s.ActiveModel != "amd.a" {
		t.Errorf("enabling a second model should not steal active, got %q", s.ActiveModel)
	}
	s = s.Toggle("amd.a")
	if s.ActiveModel != "amd.b" {
		t.Errorf("disabling the active model should hand off, got %q", s.ActiveModel)
	}
	s = s.Toggle("amd.b")
	if s.ActiveModel != "" {
		t.Errorf("disabling the last model should leave no active, got %q", s.ActiveModel)
	}
}

func TestGatewayRecommendationSurfacesFlagshipAndOnPrem(t *testing.T) {
	// Ids below are the gateway's real ones, taken from its live catalog. It
	// lists seven Opus variants; floating all of them would bury the two models
	// this feature exists to reach, so the hints match only the current
	// flagship and the on-prem model. Casing varies across the catalog
	// (`Claude-Opus-5` vs `claude-opus-4.8`), so matching ignores it.
	for _, id := range []string{
		"amd.Claude-Opus-5", "amd.Claude-Sonnet-5", "amd.Gemma-4-31B",
		"amd.CLAUDE-OPUS-5", "amd.gemma-4-31b",
	} {
		if !(gateway.Model{ID: id}).Recommended() {
			t.Errorf("%q should be recommended", id)
		}
	}
	for _, id := range []string{
		"amd.claude-opus-4.8", // superseded — selectable, just not surfaced
		"amd.claude-haiku-4.5",
		"amd.gpt-oss-20b",
	} {
		if (gateway.Model{ID: id}).Recommended() {
			t.Errorf("%q should not be recommended", id)
		}
	}
}

// TestGatewayRemembersTheModelAcrossRestarts is the "I picked a model
// yesterday" case. Auto-selecting a default on first connect must not turn
// into silently resetting the user's choice on every launch.
func TestGatewayRemembersTheModelAcrossRestarts(t *testing.T) {
	dir := t.TempDir()
	statePath := filepath.Join(dir, "gateway.json")
	t.Setenv("GAIA_GATEWAY_FILE", statePath)
	t.Setenv("GAIA_CONFIG_FILE", filepath.Join(dir, "config.json"))

	// Last session: the user deliberately chose Claude-Opus-5, which is NOT
	// the model the preference ranking would pick on its own.
	prior := gateway.State{
		BaseURL:       "https://gw.example.com/v1",
		EnabledModels: []string{"amd.Claude-Opus-5"},
		ActiveModel:   "amd.Claude-Opus-5",
	}
	if err := prior.Save(); err != nil {
		t.Fatal(err)
	}

	// This session: gateway already registered and authenticated, so the
	// screen goes straight to the model list and discovery runs.
	fake := newFakeLemonade(t)
	fake.installed = true
	fake.authenticated = true

	d := &gatewayDriver{t: t, m: gateway.New(gateway.NewClientAt(fake.baseURL()), nil)}
	d.pump(d.m.Init())
	d.send(tea.WindowSizeMsg{Width: 100, Height: 30})

	if got := d.m.ActiveModel(); got != "amd.Claude-Opus-5" {
		t.Fatalf("relaunch changed the active model to %q; the user's choice must win", got)
	}
	reloaded, err := gateway.LoadState()
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.ActiveModel != "amd.Claude-Opus-5" {
		t.Errorf("persisted active model = %q, want the prior choice", reloaded.ActiveModel)
	}

	// And the screen shows it as active, not merely stored.
	if view := d.m.View(); !strings.Contains(view, "amd.Claude-Opus-5 (active)") {
		t.Errorf("prior choice not marked active on screen:\n%s", view)
	}
}

// TestGatewayAutoSelectsOnlyWhenNothingChosen is the other half: a first-ever
// connect should land on a working model rather than an empty selection.
func TestGatewayAutoSelectsOnlyWhenNothingChosen(t *testing.T) {
	fake := newFakeLemonade(t)
	fake.installed = true
	fake.authenticated = true
	d := newGatewayDriver(t, fake) // fresh temp dirs, no prior state

	if got := d.m.ActiveModel(); got != "amd.Gemma-4-31B" {
		t.Errorf("first connect active model = %q, want amd.Gemma-4-31B "+
			"(the only gateway model that streams)", got)
	}
}
