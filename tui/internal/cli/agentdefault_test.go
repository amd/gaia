package cli

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

// installBothAgents puts gaiacpp and gaia-agent on an otherwise empty PATH,
// with a throwaway home, so only these two copies can be found.
func installBothAgents(t *testing.T) {
	t.Helper()
	bin := t.TempDir()
	home := t.TempDir()
	t.Setenv("PATH", bin)
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("GAIA_HOME", "")
	t.Setenv("GAIA_CONFIG_DIR", "")
	t.Setenv(catalog.NativePathEnv, "")
	t.Chdir(t.TempDir())
	for _, name := range []string{catalog.NativeAgentBinary, catalog.PythonAgentBinary} {
		if runtime.GOOS == "windows" {
			name += ".exe"
		}
		if err := os.WriteFile(filepath.Join(bin, name), []byte("#!/bin/sh\n"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
}

// With gaiacpp installed, a user who never chose an agent runs the Python one,
// as the Agent UI and the daemon do. A saved choice, the variable and the flag
// still run gaiacpp, and the variable still wins over the saved choice.
func TestAnInstalledGaiacppRunsOnlyWhenChosen(t *testing.T) {
	cases := []struct {
		name, saved, env, flag string
		native                 bool
	}{
		{name: "nothing chosen"},
		{name: "saved gaiapy", saved: "gaiapy"},
		{name: "saved gaiacpp", saved: "gaiacpp", native: true},
		{name: "variable gaiacpp", env: catalog.NativeAgentBinary, native: true},
		{name: "variable over saved", saved: "gaiacpp", env: catalog.PythonAgentBinary},
		{name: "flag gaiacpp", flag: catalog.NativeAgentBinary, native: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			installBothAgents(t)
			cleanAgentChoice(t)
			body := ""
			if tc.saved != "" {
				body = `{"flagship_agent": "` + tc.saved + `"}`
			}
			savedAgentConfig(t, body)
			t.Setenv(catalog.AgentEnv, tc.env)
			agentProgram = tc.flag

			cat, _, err := catalog.ForFlagship(mustChoice(t).Program)
			if err != nil {
				t.Fatalf("the launch was refused: %v", err)
			}
			if got := cat.Get(catalog.FlagshipID); got.Native != tc.native {
				t.Errorf("native=%v, want %v: %+v", got.Native, tc.native, got)
			}
		})
	}
}
