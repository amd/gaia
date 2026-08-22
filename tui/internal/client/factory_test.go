package client

import (
	"os"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

func TestForAgentBuildsTheDeclaredTransport(t *testing.T) {
	daemonAgent := catalog.Agent{ID: "email", Transport: catalog.TransportDaemon}
	c, err := ForAgent(daemonAgent, ForAgentOptions{})
	if err != nil {
		t.Fatalf("ForAgent(daemon): %v", err)
	}
	defer c.Close()
	if _, ok := c.(*SSEClient); !ok {
		t.Errorf("daemon transport built %T, want *SSEClient", c)
	}

	// ResolveExecutable only checks that the path exists and is executable —
	// NewSubprocessClient does not spawn it — so the test binary itself is the
	// one path guaranteed to satisfy that on every OS. /usr/bin/true does not
	// exist on Windows, which only surfaced once this suite ran there.
	self, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}
	subAgent := catalog.Agent{ID: "bash", BinaryPath: self, BinaryArgs: []string{"--json-events"}}
	c2, err := ForAgent(subAgent, ForAgentOptions{})
	if err != nil {
		t.Fatalf("ForAgent(subprocess): %v", err)
	}
	defer c2.Close()
	if _, ok := c2.(*SubprocessClient); !ok {
		t.Errorf("subprocess transport built %T, want *SubprocessClient", c2)
	}
}

// A subprocess agent whose binary was never found must fail with a remedy, not
// produce a client that dies later with a confusing message.
func TestForAgentRejectsAMissingBinary(t *testing.T) {
	_, err := ForAgent(catalog.Agent{ID: "bash"}, ForAgentOptions{})
	if err == nil {
		t.Fatal("expected an error for a subprocess agent with no binary")
	}
	if !strings.Contains(err.Error(), "--mock") {
		t.Errorf("error should name a way forward: %v", err)
	}
}

// The catalog leaves an unresolved binary NAME in place when discovery finds
// nothing, so "BinaryPath is set" never meant "this can start". The hub took
// that as a green light: it opened a chat, said "Connected to: Bash", and only
// failed when the user sent their first message with
// `exec: "gaia-bash": executable file not found in $PATH`. Refusing here is
// what makes the launch tell the truth.
func TestForAgentRejectsAnUnresolvableBinaryName(t *testing.T) {
	_, err := ForAgent(
		catalog.Agent{ID: "bash", BinaryPath: "gaia-bash-that-was-never-built"},
		ForAgentOptions{},
	)
	if err == nil {
		t.Fatal("a binary that is nowhere on this machine built a client anyway")
	}
	if !strings.Contains(err.Error(), "cannot start agent") {
		t.Errorf("the error does not say the agent cannot start: %v", err)
	}
	if !strings.Contains(err.Error(), "gaia-bash-that-was-never-built") {
		t.Errorf("the error does not name the missing binary: %v", err)
	}
	if !strings.Contains(err.Error(), "PATH") {
		t.Errorf("the error does not say where it looked: %v", err)
	}
}

func TestForAgentRejectsAnUnknownTransport(t *testing.T) {
	_, err := ForAgent(catalog.Agent{ID: "future", Transport: catalog.Transport(99)}, ForAgentOptions{})
	if err == nil {
		t.Fatal("expected an error for an unknown transport rather than a silent default")
	}
	if !strings.Contains(err.Error(), "99") {
		t.Errorf("error should name the transport it cannot reach: %v", err)
	}
}

// Model / MaxSteps must reach the request body, not be silently dropped.
func TestForAgentPlumbsModelAndMaxSteps(t *testing.T) {
	c, err := ForAgent(
		catalog.Agent{ID: "email", Transport: catalog.TransportDaemon},
		ForAgentOptions{Model: "Gemma-4-E4B-it-GGUF", MaxSteps: 7},
	)
	if err != nil {
		t.Fatalf("ForAgent: %v", err)
	}
	defer c.Close()

	sse, ok := c.(*SSEClient)
	if !ok {
		t.Fatalf("got %T", c)
	}
	if sse.opts.Model != "Gemma-4-E4B-it-GGUF" || sse.opts.MaxSteps != 7 {
		t.Errorf("options not plumbed: %+v", sse.opts)
	}
}

// One --dev has to reach the child too: the TUI going verbose while the agent
// it spawned keeps logging errors only is the half-state that sends people
// looking in an empty log file.
//
// Opt-in per agent, because an agent that does not know the flag dies at exec
// on an unknown argument — a verbosity switch must never become a launch
// failure.
func TestDevModeForwardsDevArgsToTheChild(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}

	for _, tc := range []struct {
		name    string
		devArgs []string
		dev     bool
		want    []string
	}{
		{"off, so the child argv is untouched", []string{"--dev"}, false, []string{"--json-events"}},
		{"on, so the child goes verbose too", []string{"--dev"}, true, []string{"--json-events", "--dev"}},
		{"on but the agent has no dev mode", nil, true, []string{"--json-events"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			agent := catalog.Agent{
				ID: "gaia", BinaryPath: self,
				BinaryArgs: []string{"--json-events"}, DevArgs: tc.devArgs,
			}
			c, err := ForAgent(agent, ForAgentOptions{Dev: tc.dev})
			if err != nil {
				t.Fatalf("ForAgent: %v", err)
			}
			defer c.Close()

			sub, ok := c.(*SubprocessClient)
			if !ok {
				t.Fatalf("built %T, want *SubprocessClient", c)
			}
			if got := strings.Join(sub.args, " "); got != strings.Join(tc.want, " ") {
				t.Errorf("child argv = %q, want %q", got, tc.want)
			}
		})
	}
}

// --use-claude must reach the child's argv exactly as the Python side's parser
// declares it — the flag string is the whole contract between the two.
func TestUseClaudeForwardsTheFlagsToTheChild(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}

	for _, tc := range []struct {
		name        string
		useClaude   bool
		claudeModel string
		want        string
	}{
		{"off, so the child argv is untouched", false, "", "--json-events"},
		{"on, so the child switches backends", true, "", "--json-events --use-claude"},
		{"on with an explicit model", true, "claude-sonnet-5",
			"--json-events --use-claude --claude-model claude-sonnet-5"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			agent := catalog.Agent{
				ID: "gaia", BinaryPath: self, BinaryArgs: []string{"--json-events"},
			}
			c, err := ForAgent(agent, ForAgentOptions{
				UseClaude: tc.useClaude, ClaudeModel: tc.claudeModel,
			})
			if err != nil {
				t.Fatalf("ForAgent: %v", err)
			}
			defer c.Close()

			sub, ok := c.(*SubprocessClient)
			if !ok {
				t.Fatalf("built %T, want *SubprocessClient", c)
			}
			if got := strings.Join(sub.args, " "); got != tc.want {
				t.Errorf("child argv = %q, want %q", got, tc.want)
			}
			if want := tc.useClaude; sub.ClaudeAtLaunch() != want {
				t.Errorf("ClaudeAtLaunch() = %v, want %v", sub.ClaudeAtLaunch(), want)
			}
		})
	}
}

// Same aliasing hazard as DevArgs: one --use-claude launch must not leave the
// flag on the catalog entry for every later one.
func TestForwardingUseClaudeDoesNotMutateTheCatalogEntry(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}

	agent := catalog.Agent{
		ID: "gaia", BinaryPath: self, BinaryArgs: []string{"--json-events"},
	}
	c, err := ForAgent(agent, ForAgentOptions{UseClaude: true, ClaudeModel: "claude-sonnet-5"})
	if err != nil {
		t.Fatalf("ForAgent: %v", err)
	}
	defer c.Close()

	if got := strings.Join(agent.BinaryArgs, " "); got != "--json-events" {
		t.Errorf("the catalog entry now carries %q; --use-claude leaked into it", got)
	}
}

// The daemon transport has no backend switch, so accepting --use-claude there
// would leave the user believing they run on Claude while the sidecar quietly
// keeps using Lemonade.
func TestUseClaudeOnTheDaemonTransportIsRefused(t *testing.T) {
	_, err := ForAgent(
		catalog.Agent{ID: "email", Transport: catalog.TransportDaemon},
		ForAgentOptions{UseClaude: true},
	)
	if err == nil {
		t.Fatal("--use-claude on the daemon transport was silently accepted")
	}
	if !strings.Contains(err.Error(), "daemon") {
		t.Errorf("the error does not name the transport that cannot honour it: %v", err)
	}
}

// A Claude model with no Claude mode would be accepted and then change
// nothing — refused loudly on every transport.
func TestClaudeModelWithoutUseClaudeIsRefused(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}
	_, err = ForAgent(
		catalog.Agent{ID: "gaia", BinaryPath: self},
		ForAgentOptions{ClaudeModel: "claude-sonnet-5"},
	)
	if err == nil {
		t.Fatal("a Claude model without --use-claude was silently accepted")
	}
	if !strings.Contains(err.Error(), "--use-claude") {
		t.Errorf("the error does not name the missing flag: %v", err)
	}
}

// The catalog entry outlives the launch. Appending straight onto BinaryArgs
// would let a slice with spare capacity alias the catalog's backing array, so
// one --dev launch would leave --dev on the entry for every later one.
func TestForwardingDevArgsDoesNotMutateTheCatalogEntry(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}

	agent := catalog.Agent{
		ID: "gaia", BinaryPath: self,
		BinaryArgs: []string{"--json-events"}, DevArgs: []string{"--dev"},
	}
	c, err := ForAgent(agent, ForAgentOptions{Dev: true})
	if err != nil {
		t.Fatalf("ForAgent: %v", err)
	}
	defer c.Close()

	if got := strings.Join(agent.BinaryArgs, " "); got != "--json-events" {
		t.Errorf("the catalog entry now carries %q; --dev leaked into it", got)
	}
}
