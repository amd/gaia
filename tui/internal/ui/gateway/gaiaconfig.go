package gateway

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// setDefaultModel points GAIA's persistent `default_model` at a gateway model,
// so picking one here actually changes what `gaia chat` / `gaia llm` run.
//
// Without this, the selection would live only in gateway.json and the screen
// would appear to do nothing. It routes through the existing default_model
// mechanism rather than adding a second precedence rule — the same thing
// `gaia gateway use` does on the CLI side.
//
// Unknown keys are preserved: this file is shared with the Python side
// (gaia.config.GaiaConfig), which owns fields this package does not model.
func setDefaultModel(modelID string) error {
	path := gaiaConfigPath()

	config := map[string]any{}
	raw, err := os.ReadFile(path)
	switch {
	case err == nil:
		if err := json.Unmarshal(raw, &config); err != nil {
			return fmt.Errorf(
				"GAIA config at %s is not valid JSON: %w\n"+
					"Fix or delete it, then set the model again", path, err)
		}
	case !os.IsNotExist(err):
		return fmt.Errorf("could not read GAIA config at %s: %w", path, err)
	}

	config["default_model"] = modelID

	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return fmt.Errorf("could not create %s: %w", dir, err)
		}
	}
	encoded, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return fmt.Errorf("could not encode GAIA config: %w", err)
	}
	if err := os.WriteFile(path, append(encoded, '\n'), 0o600); err != nil {
		return fmt.Errorf("could not write GAIA config to %s: %w", path, err)
	}
	return nil
}

// gaiaConfigPath mirrors gaia.config's resolution order: GAIA_CONFIG_FILE wins
// outright, then GAIA_CONFIG_DIR, then ~/.gaia.
func gaiaConfigPath() string {
	if override := strings.TrimSpace(os.Getenv("GAIA_CONFIG_FILE")); override != "" {
		return override
	}
	dir := strings.TrimSpace(os.Getenv("GAIA_CONFIG_DIR"))
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "config.json"
		}
		dir = filepath.Join(home, ".gaia")
	}
	return filepath.Join(dir, "config.json")
}
