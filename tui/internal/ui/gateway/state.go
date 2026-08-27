package gateway

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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
}

// StatePath is where the selection is stored. GAIA_GATEWAY_FILE overrides it
// for tests; GAIA_CONFIG_DIR moves the whole GAIA config directory.
func StatePath() string {
	if override := strings.TrimSpace(os.Getenv("GAIA_GATEWAY_FILE")); override != "" {
		return override
	}
	dir := strings.TrimSpace(os.Getenv("GAIA_CONFIG_DIR"))
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			// Fall back to the working directory rather than guessing a path
			// that does not exist; a save error then says so plainly.
			return "gateway.json"
		}
		dir = filepath.Join(home, ".gaia")
	}
	return filepath.Join(dir, "gateway.json")
}

// LoadState reads the saved selection. A missing file is not an error — it
// means "nothing configured yet". A corrupt one is, so it surfaces instead of
// being silently replaced by defaults.
func LoadState() (State, error) {
	path := StatePath()
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
	if state.BaseURL == "" {
		state.BaseURL = DefaultBaseURL
	}
	return state, nil
}

// Save writes the selection back, creating ~/.gaia if needed.
func (s State) Save() error {
	path := StatePath()
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return fmt.Errorf("could not create %s: %w", dir, err)
		}
	}
	if s.EnabledModels == nil {
		s.EnabledModels = []string{}
	}
	encoded, err := json.MarshalIndent(s, "", "  ")
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
