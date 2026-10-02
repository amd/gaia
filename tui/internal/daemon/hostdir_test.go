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

// A GAIA_HOME written with ~ or $VAR must expand the same way config and
// Lemonade do, or the daemon resolves relative to cwd instead of $HOME.
func TestHostDirExpandsTildeAndEnvVar(t *testing.T) {
	t.Setenv(EnvHome, "")
	t.Setenv("GAIA_PROFILE_DIR", "profile2")
	t.Setenv("GAIA_HOME", "~/$GAIA_PROFILE_DIR")

	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatalf("UserHomeDir: %v", err)
	}
	want := filepath.Join(home, "profile2", "host")
	if got, err := HostDir(); err != nil || got != want {
		t.Fatalf("HostDir() = %q, %v; want %q", got, err, want)
	}
}
