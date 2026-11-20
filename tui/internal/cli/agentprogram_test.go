package cli

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

// Which agent program runs is the user's to override, from the command line or
// the environment, and the flag has to say what leaving it unset does.
func TestAgentProgramFlagOverridesTheEnvironment(t *testing.T) {
	flags := rootCmd.PersistentFlags()
	f := flags.Lookup("agent-program")
	if f == nil {
		t.Fatal("--agent-program is not registered")
	}
	for _, must := range []string{catalog.NativeAgentBinary, catalog.PythonAgentBinary, catalog.AgentEnv} {
		if !strings.Contains(f.Usage, must) {
			t.Errorf("--agent-program help text does not mention %q: %q", must, f.Usage)
		}
	}

	agentProgram = ""
	t.Cleanup(func() { agentProgram = "" })

	t.Setenv(catalog.AgentEnv, "")
	if got := agentChoice(); got != "" {
		t.Errorf("with nothing set the choice is %q, want the catalog's own pick", got)
	}
	t.Setenv(catalog.AgentEnv, catalog.PythonAgentBinary)
	if got := agentChoice(); got != catalog.PythonAgentBinary {
		t.Errorf("%s was ignored: %q", catalog.AgentEnv, got)
	}
	if err := flags.Set("agent-program", catalog.NativeAgentBinary); err != nil {
		t.Fatal(err)
	}
	if got := agentChoice(); got != catalog.NativeAgentBinary {
		t.Errorf("the flag did not override %s: %q", catalog.AgentEnv, got)
	}
}
