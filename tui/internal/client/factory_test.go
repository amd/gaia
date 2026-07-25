package client

import (
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

	subAgent := catalog.Agent{ID: "bash", BinaryPath: "/usr/bin/true", BinaryArgs: []string{"--json-events"}}
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
