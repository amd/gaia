package daemon

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"testing"
)

// gaiaOnPath puts a stand-in `gaia` first on PATH so the launcher resolves.
func gaiaOnPath(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	name := "gaia"
	if runtime.GOOS == "windows" {
		name = "gaia.exe"
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
}

func withLaunchEnv(t *testing.T, fn func(context.Context) []string) {
	t.Helper()
	orig := LaunchEnv
	t.Cleanup(func() { LaunchEnv = orig })
	LaunchEnv = fn
}

// A daemon started without the gate's pin starts GAIA's own Lemonade beside the
// system one the gate is using, and the session moves onto it.
func TestADaemonTheTUIStartsCarriesTheLaunchEnv(t *testing.T) {
	gaiaOnPath(t)
	const pin = "LEMONADE_BASE_URL=http://localhost:13305/api/v1"
	withLaunchEnv(t, func(context.Context) []string { return []string{pin} })

	cmd, err := gaiaDaemonStart(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(cmd.Env, pin) {
		t.Fatalf("the daemon launcher does not carry %s", pin)
	}
	if len(cmd.Env) != len(os.Environ())+1 {
		t.Fatalf("the launcher env is not the inherited env plus the pin: %d vars", len(cmd.Env))
	}
}

// Nothing to add leaves the environment inherited, as it always was.
func TestADaemonWithNothingToPinInheritsItsEnvironment(t *testing.T) {
	gaiaOnPath(t)
	for name, fn := range map[string]func(context.Context) []string{
		"no hook":     nil,
		"nothing pin": func(context.Context) []string { return nil },
	} {
		withLaunchEnv(t, fn)
		cmd, err := gaiaDaemonStart(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if cmd.Env != nil {
			t.Fatalf("%s: the launcher env was replaced: %v", name, cmd.Env)
		}
	}
}
