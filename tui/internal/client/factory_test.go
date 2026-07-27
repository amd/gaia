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
