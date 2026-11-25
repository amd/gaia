package gateway

import (
	"path/filepath"
	"testing"
)

// GAIA_HOME moves the whole config folder for Python, so the TUI must read and
// write the same gateway.json and config.json there.
func TestGaiaHomeMovesGatewayAndConfigFiles(t *testing.T) {
	home := t.TempDir()
	for _, name := range []string{"GAIA_GATEWAY_FILE", "GAIA_CONFIG_FILE", "GAIA_CONFIG_DIR"} {
		t.Setenv(name, "")
	}
	t.Setenv("GAIA_HOME", home)

	state, err := StatePath()
	if err != nil {
		t.Fatalf("StatePath: %v", err)
	}
	if want := filepath.Join(home, "gateway.json"); state != want {
		t.Errorf("StatePath() = %q, want %q", state, want)
	}
	config, err := gaiaConfigPath()
	if err != nil {
		t.Fatalf("gaiaConfigPath: %v", err)
	}
	if want := filepath.Join(home, "config.json"); config != want {
		t.Errorf("gaiaConfigPath() = %q, want %q", config, want)
	}

	// GAIA_CONFIG_DIR still wins over GAIA_HOME, as in gaia.config.
	dir := t.TempDir()
	t.Setenv("GAIA_CONFIG_DIR", dir)
	if state, _ := StatePath(); state != filepath.Join(dir, "gateway.json") {
		t.Errorf("with GAIA_CONFIG_DIR, StatePath() = %q", state)
	}
}
