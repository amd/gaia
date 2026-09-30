package daemon

import (
	"os"
	"path/filepath"
	"testing"
)

// GAIA_HOME isolates the daemon too; mirrors tests/unit/test_daemon_home_follows_gaia_home.py.
func TestHostDirFollowsGaiaHome(t *testing.T) {
	gaiaHome := t.TempDir()
	t.Setenv(EnvHome, "")
	t.Setenv("GAIA_HOME", gaiaHome)
	got, err := HostDir()
	if err != nil || got != filepath.Join(gaiaHome, "host") {
		t.Fatalf("HostDir() = %q, %v; want %q", got, err, filepath.Join(gaiaHome, "host"))
	}

	override := t.TempDir()
	t.Setenv(EnvHome, override)
	if got, _ := HostDir(); got != override {
		t.Fatalf("GAIA_DAEMON_HOME must win: got %q", got)
	}

	t.Setenv(EnvHome, "")
	t.Setenv("GAIA_HOME", "")
	home, _ := os.UserHomeDir()
	if got, _ := HostDir(); got != filepath.Join(home, ".gaia", "host") {
		t.Fatalf("default = %q", got)
	}
}
