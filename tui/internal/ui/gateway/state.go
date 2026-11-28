package gateway

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// State is the non-secret gateway selection shared with the Python side
// (gaia.llm.gateway.GatewayState). It lives at ~/.gaia/gateway.json.
//
// There is deliberately no token field. Adding one would put a secret on disk,
// which the whole design exists to avoid — tokens go to Lemonade's in-memory
// store, or to LEMONADE_AMD_API_KEY in Lemonade's environment.
type State struct {
	BaseURL       string   `json:"base_url"`
	EnabledModels []string `json:"enabled_models"`
	ActiveModel   string   `json:"active_model,omitempty"`

	// extra holds keys this file does not model so a save does not delete
	// them. The Python side records learned model capabilities here; a
	// round-trip through the TUI must not cost that.
	extra map[string]json.RawMessage

	// unavailable is read from extra["unavailable_models"], which the Python
	// client writes when the gateway answers 404 for a model. Read-only here.
	unavailable map[string]Unavailability
}

// Unavailability is a model's last recorded "not found" from the gateway.
type Unavailability struct {
	Status int    `json:"status"`
	At     string `json:"at"`
}

// Describe says when the model failed, in one short clause.
func (u Unavailability) Describe() string {
	status := u.Status
	if status == 0 {
		status = 404
	}
	when := strings.TrimSuffix(strings.Replace(u.At, "T", " ", 1), "Z")
	if when == "" {
		return fmt.Sprintf("answered HTTP %d", status)
	}
	return fmt.Sprintf("answered HTTP %d on %s UTC", status, when)
}

// Unavailable reports the recorded failure for a model, if any.
func (s State) Unavailable(id string) (Unavailability, bool) {
	u, ok := s.unavailable[id]
	return u, ok
}

// ownedKeys are the fields State models itself; everything else in the file
// belongs to another writer and is carried through untouched.
var ownedKeys = []string{"base_url", "enabled_models", "active_model"}

// StatePath is where the selection is stored, the same file gaia.llm.gateway
// uses: GAIA_GATEWAY_FILE, else gateway.json in GAIA's config folder
// (lemonade.ConfigDirFrom, so GAIA_HOME moves it too).
func StatePath() (string, error) {
	if override := strings.TrimSpace(os.Getenv("GAIA_GATEWAY_FILE")); override != "" {
		return override, nil
	}
	dir := lemonade.ConfigDirFrom(os.Getenv, os.UserHomeDir)
	if dir == "" {
		return "", fmt.Errorf("could not find GAIA's config folder for the gateway settings: " +
			"no home directory, and neither GAIA_CONFIG_DIR nor GAIA_HOME is set. Set GAIA_HOME and try again")
	}
	return filepath.Join(dir, "gateway.json"), nil
}

// LoadState reads the saved selection. A missing file is not an error — it
// means "nothing configured yet". A corrupt one is, so it surfaces instead of
// being silently replaced by defaults.
func LoadState() (State, error) {
	path, err := StatePath()
	if err != nil {
		return State{}, err
	}
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return State{BaseURL: DefaultBaseURL}, nil
	}
	if err != nil {
		return State{}, fmt.Errorf("could not read gateway settings at %s: %w", path, err)
	}
	var state State
	if err := json.Unmarshal(raw, &state); err != nil {
		return State{}, fmt.Errorf(
			"gateway settings at %s are not valid JSON: %w\n"+
				"Delete the file to start fresh", path, err)
	}
	if err := json.Unmarshal(raw, &state.extra); err != nil {
		return State{}, fmt.Errorf(
			"gateway settings at %s are not a JSON object: %w\n"+
				"Delete the file to start fresh", path, err)
	}
	for _, own := range ownedKeys {
		delete(state.extra, own)
	}
	if raw, ok := state.extra["unavailable_models"]; ok {
		if err := json.Unmarshal(raw, &state.unavailable); err != nil {
			return State{}, fmt.Errorf(
				"gateway settings at %s have an unreadable unavailable_models entry: %w\n"+
					"Delete that key to start fresh", path, err)
		}
	}
	if state.BaseURL == "" {
		state.BaseURL = DefaultBaseURL
	}
	return state, nil
}

// Save writes the selection back, creating ~/.gaia if needed.
func (s State) Save() error {
	path, err := StatePath()
	if err != nil {
		return err
	}
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return fmt.Errorf("could not create %s: %w", dir, err)
		}
	}
	if s.EnabledModels == nil {
		s.EnabledModels = []string{}
	}
	merged := map[string]any{
		"base_url":       s.BaseURL,
		"enabled_models": s.EnabledModels,
	}
	if s.ActiveModel != "" {
		merged["active_model"] = s.ActiveModel
	}
	for k, v := range s.extra {
		merged[k] = v
	}
	encoded, err := json.MarshalIndent(merged, "", "  ")
	if err != nil {
		return fmt.Errorf("could not encode gateway settings: %w", err)
	}
	// Preferences, not secrets — but this sits beside config.json in a
	// user-private directory, so match its posture.
	if err := os.WriteFile(path, append(encoded, '\n'), 0o600); err != nil {
		return fmt.Errorf("could not write gateway settings to %s: %w", path, err)
	}
	return nil
}

// IsEnabled reports whether the user has ticked this model.
func (s State) IsEnabled(id string) bool {
	for _, enabled := range s.EnabledModels {
		if enabled == id {
			return true
		}
	}
	return false
}

// Toggle flips a model's enabled state, keeping ActiveModel consistent: the
// first model enabled becomes active, and disabling the active one hands off
// to whatever remains.
func (s State) Toggle(id string) State {
	if !s.IsEnabled(id) {
		s.EnabledModels = append(s.EnabledModels, id)
		if s.ActiveModel == "" {
			s.ActiveModel = id
		}
		return s
	}
	remaining := make([]string, 0, len(s.EnabledModels))
	for _, enabled := range s.EnabledModels {
		if enabled != id {
			remaining = append(remaining, enabled)
		}
	}
	s.EnabledModels = remaining
	if s.ActiveModel == id {
		s.ActiveModel = ""
		if len(remaining) > 0 {
			s.ActiveModel = remaining[0]
		}
	}
	return s
}

// SetActive makes a model active, enabling it if it was not already.
func (s State) SetActive(id string) State {
	if !s.IsEnabled(id) {
		s.EnabledModels = append(s.EnabledModels, id)
	}
	s.ActiveModel = id
	return s
}
