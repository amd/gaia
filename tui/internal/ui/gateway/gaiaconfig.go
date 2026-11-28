package gateway

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/amd/gaia/tui/internal/filelock"
	"github.com/amd/gaia/tui/internal/lemonade"
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
	if lemonade.LocalOnly() {
		return fmt.Errorf("Local only is on, so GAIA did not make %s the default model. %s",
			modelID, lemonade.LocalOnlyHint)
	}
	path, err := gaiaConfigPath()
	if err != nil {
		return err
	}

	// The Python side and the preflight writers use this same lock. Hold it for
	// the complete read-modify-write so a concurrent save cannot lose a key, and
	// replace the file atomically so readers never observe partial JSON.
	lock, err := filelock.Acquire(path, filelock.Timeout)
	if err != nil {
		return fmt.Errorf("could not save GAIA config at %s: %w", path, err)
	}
	defer lock.Release()

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
	encoded, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return fmt.Errorf("could not encode GAIA config: %w", err)
	}
	if err := filelock.WriteAtomic(path, append(encoded, '\n'), 0o600); err != nil {
		return fmt.Errorf("could not write GAIA config to %s: %w", path, err)
	}
	return nil
}

// gaiaConfigPath is the config.json Python reads (lemonade.ConfigPathFrom).
func gaiaConfigPath() (string, error) {
	path := lemonade.ConfigPathFrom(os.Getenv, os.UserHomeDir)
	if path == "" {
		return "", fmt.Errorf("could not find GAIA's config folder: no home directory, " +
			"and none of GAIA_CONFIG_FILE, GAIA_CONFIG_DIR or GAIA_HOME is set. Set GAIA_HOME and try again")
	}
	return path, nil
}
