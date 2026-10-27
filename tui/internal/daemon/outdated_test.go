package daemon

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// An update that replaced GAIA must not leave the TUI on the previous release's
// daemon (#5695): the record names the daemon's own version.py, and a release
// that no longer matches it is restarted through the GAIA CLI.

func versionFile(t *testing.T, version string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "gaia", "version.py")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`__version__ = "`+version+"\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestReplacedInstall(t *testing.T) {
	current := versionFile(t, "0.25.1")
	cases := []struct {
		name string
		inst Instance
		want string
	}{
		{"no record", Instance{}, ""},
		{"still installed", Instance{GaiaVersion: "0.25.1", VersionFile: current}, ""},
		{"replaced", Instance{GaiaVersion: "0.25.0", VersionFile: current}, "it runs GAIA 0.25.0, and 0.25.1 is installed"},
		{"gone", Instance{GaiaVersion: "0.25.0", VersionFile: filepath.Join(t.TempDir(), "version.py")}, "is gone"},
	}
	for _, tc := range cases {
		got := ReplacedInstall(&tc.inst)
		if (tc.want == "") != (got == "") || !strings.Contains(got, tc.want) {
			t.Errorf("%s: ReplacedInstall = %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestStartOrAttachKeepsACurrentDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	path := versionFile(t, "0.25.1")
	f.writeInstance(func(i *Instance) { i.GaiaVersion, i.VersionFile = "0.25.1", path })

	c := testClient(t, func(o *Options) {
		o.StartCommand = func(context.Context) (*exec.Cmd, error) {
			t.Error("a current daemon must not be restarted")
			return nil, &StartError{Reason: "unreachable"}
		}
	})
	if _, err := c.StartOrAttach(context.Background()); err != nil {
		t.Fatalf("StartOrAttach: %v", err)
	}
}

func TestStartOrAttachRestartsAnOutOfDateDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	path := versionFile(t, "0.25.2")
	f.writeInstance(func(i *Instance) { i.GaiaVersion, i.VersionFile = "0.25.1", path })

	fresh := &Instance{
		PID: os.Getpid(), Port: f.port(), Token: "token-A", Host: DefaultHost,
		APIVersion: "1.1", Service: ServiceID, GaiaVersion: "0.25.2", VersionFile: path,
	}
	payload, err := json.Marshal(fresh)
	if err != nil {
		t.Fatal(err)
	}
	c := testClient(t, func(o *Options) { o.StartCommand = launcher(t, f.dir, string(payload), "") })

	got, err := c.StartOrAttach(context.Background())
	if err != nil {
		t.Fatalf("StartOrAttach: %v", err)
	}
	if got.GaiaVersion != "0.25.2" {
		t.Errorf("attached to GAIA %q, want the restarted 0.25.2", got.GaiaVersion)
	}
}

func TestStartOrAttachKeepsABusyOutOfDateDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	path := versionFile(t, "0.25.2")
	f.writeInstance(func(i *Instance) { i.GaiaVersion, i.VersionFile = "0.25.1", path })

	// The CLI found it busy and left the same daemon registered.
	c := testClient(t, func(o *Options) { o.StartCommand = launcher(t, f.dir, "", "") })

	got, err := c.StartOrAttach(context.Background())
	if err != nil {
		t.Fatalf("StartOrAttach: %v", err)
	}
	if got.GaiaVersion != "0.25.1" {
		t.Errorf("attached to GAIA %q, want the busy 0.25.1 daemon kept", got.GaiaVersion)
	}
}

func TestStartOrAttachSaysWhatToRunWhenTheRestartLeavesItOutOfDate(t *testing.T) {
	f := newFakeDaemon(t)
	path := versionFile(t, "0.25.2")
	f.writeInstance(func(i *Instance) { i.GaiaVersion, i.VersionFile = "0.25.1", path })

	// A new daemon came up, but still on the old release.
	restarted := &Instance{
		PID: os.Getpid(), Port: f.port(), Token: "token-A", Host: DefaultHost,
		APIVersion: "1.1", Service: ServiceID, StartedAt: 2.0,
		GaiaVersion: "0.25.1", VersionFile: path,
	}
	payload, err := json.Marshal(restarted)
	if err != nil {
		t.Fatal(err)
	}
	c := testClient(t, func(o *Options) { o.StartCommand = launcher(t, f.dir, string(payload), "") })

	_, err = c.StartOrAttach(context.Background())
	var se *StartError
	if !asError(err, &se) || !strings.Contains(se.Reason, "gaia daemon restart") {
		t.Fatalf("expected a StartError naming `gaia daemon restart`, got %v", err)
	}
}
