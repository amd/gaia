package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// savedAgentConfig points the saved settings at a throwaway file holding body,
// or at no file when body is empty.
func savedAgentConfig(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.json")
	if body != "" {
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("GAIA_CONFIG_FILE", path)
	return path
}

func cleanAgentChoice(t *testing.T) {
	t.Helper()
	agentProgram, mockAgent = "", ""
	t.Cleanup(func() { agentProgram, mockAgent = "", "" })
	t.Setenv(catalog.AgentEnv, "")
}

func mustChoice(t *testing.T) ui.AgentChoice {
	t.Helper()
	got, err := agentChoice()
	if err != nil {
		t.Fatalf("agentChoice: %v", err)
	}
	return got
}

// Which agent program runs is the user's to override, from the command line or
// the environment, and the flag has to say what leaving it unset does.
func TestAgentProgramFlagOverridesTheEnvironment(t *testing.T) {
	flags := rootCmd.PersistentFlags()
	f := flags.Lookup("agent-program")
	if f == nil {
		t.Fatal("--agent-program is not registered")
	}
	for _, must := range []string{catalog.NativeAgentBinary, catalog.PythonAgentBinary, catalog.AgentEnv,
		"/agent", preflight.FlagshipAgentKey} {
		if !strings.Contains(f.Usage, must) {
			t.Errorf("--agent-program help text does not mention %q: %q", must, f.Usage)
		}
	}

	cleanAgentChoice(t)
	savedAgentConfig(t, "")

	if got := mustChoice(t); got != (ui.AgentChoice{}) {
		t.Errorf("with nothing set the choice is %+v, want the catalog's own pick", got)
	}
	t.Setenv(catalog.AgentEnv, catalog.PythonAgentBinary)
	if got := mustChoice(t); got.Program != catalog.PythonAgentBinary || got.Pin != catalog.AgentEnv {
		t.Errorf("%s was ignored: %+v", catalog.AgentEnv, got)
	}
	if err := flags.Set("agent-program", catalog.NativeAgentBinary); err != nil {
		t.Fatal(err)
	}
	if got := mustChoice(t); got.Program != catalog.NativeAgentBinary || got.Pin != "--agent-program" {
		t.Errorf("the flag did not override %s: %+v", catalog.AgentEnv, got)
	}
}

// The saved choice sits below the flag and the variable and above the
// catalog's own pick, and it never pins the session.
func TestSavedAgentProgramSitsBelowTheFlagAndTheVariable(t *testing.T) {
	cleanAgentChoice(t)

	for saved, program := range map[string]string{
		preflight.FlagshipAgentPython: catalog.PythonAgentBinary,
		preflight.FlagshipAgentNative: catalog.NativeAgentBinary,
	} {
		savedAgentConfig(t, `{"flagship_agent": "`+saved+`"}`)
		if got := mustChoice(t); got.Program != program || got.Pin != "" {
			t.Errorf("saved %s gave %+v, want %s and no pin", saved, got, program)
		}
	}

	savedAgentConfig(t, `{"flagship_agent": "gaiacpp"}`)
	t.Setenv(catalog.AgentEnv, catalog.PythonAgentBinary)
	if got := mustChoice(t); got.Program != catalog.PythonAgentBinary || got.Pin != catalog.AgentEnv {
		t.Errorf("%s did not override the saved choice: %+v", catalog.AgentEnv, got)
	}
	agentProgram = "/opt/gaia/gaiacpp"
	if got := mustChoice(t); got.Program != "/opt/gaia/gaiacpp" || got.Pin != "--agent-program" {
		t.Errorf("the flag did not override the variable and the saved choice: %+v", got)
	}
}

// A saved value GAIA does not know stops the launch, naming the file and key,
// unless the flag or the variable already decided.
func TestUnknownSavedAgentProgramIsAnErrorNotAGuess(t *testing.T) {
	cleanAgentChoice(t)
	path := savedAgentConfig(t, `{"flagship_agent": "rust"}`)

	got, err := agentChoice()
	if err == nil {
		t.Fatalf("an unknown saved value was read as %+v", got)
	}
	for _, want := range []string{path, preflight.FlagshipAgentKey, "gaiapy", "gaiacpp", "--agent-program"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the error does not name %q:\n%v", want, err)
		}
	}

	if _, err := agentProgramFor("email"); err != nil {
		t.Errorf("launching another agent tripped over the flagship's saved program: %v", err)
	}
	if _, err := agentProgramFor(catalog.FlagshipID); err == nil {
		t.Error("launching the flagship by id ignored an unknown saved program")
	}

	t.Setenv(catalog.AgentEnv, catalog.PythonAgentBinary)
	if got := mustChoice(t); got.Program != catalog.PythonAgentBinary {
		t.Errorf("the variable did not decide over an unreadable saved choice: %+v", got)
	}
}

// --mock replaces the agent, so the saved choice is neither read nor switchable.
func TestMockPinsTheSessionAndSkipsTheSavedChoice(t *testing.T) {
	cleanAgentChoice(t)
	savedAgentConfig(t, `{"flagship_agent": "rust"}`)
	mockAgent = "/tmp/mock-agent"
	if got := mustChoice(t); got.Program != "" || got.Pin != "--mock" {
		t.Errorf("--mock gave %+v", got)
	}
}
