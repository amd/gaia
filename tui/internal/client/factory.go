package client

import (
	"fmt"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
)

// ForAgentOptions configures the transport built by ForAgent.
type ForAgentOptions struct {
	// Debug enables the subprocess transport's stderr diagnostics.
	Debug bool
	// Model / MaxSteps override the sidecar defaults on the daemon transport.
	// Ignored by the subprocess transport, which takes its model via BinaryArgs.
	Model    string
	MaxSteps int
	// Logf receives transport diagnostics. Never given a token.
	Logf func(format string, args ...any)
}

// ForAgent builds the transport a catalog entry declares.
//
// This is the single transport switch: the chat UI, the hub, and the
// non-interactive CLI paths all go through it, so adding a transport never means
// finding every launch site. It deliberately lives here rather than on a Bubble
// Tea model — the headless CLI paths need it without a UI.
func ForAgent(agent catalog.Agent, opts ForAgentOptions) (AgentClient, error) {
	switch agent.Transport {
	case catalog.TransportDaemon:
		return NewSSEClient(agent.ID, daemon.New(daemon.Options{Logf: opts.Logf}), SSEOptions{
			Model:    opts.Model,
			MaxSteps: opts.MaxSteps,
			Logf:     opts.Logf,
		}), nil

	case catalog.TransportSubprocess:
		if agent.BinaryPath == "" {
			return nil, fmt.Errorf(
				"agent %q uses the subprocess transport but no binary was found — "+
					"build it, put it on PATH, or pass --mock <path> to run against a stub",
				agent.ID)
		}
		// Resolved HERE, before any caller can report "connected".
		bin, err := catalog.ResolveExecutable(agent.BinaryPath, agent.ID)
		if err != nil {
			return nil, fmt.Errorf("cannot start agent %q: %w", agent.ID, err)
		}
		return NewSubprocessClient(bin, agent.BinaryArgs, opts.Debug), nil

	default:
		return nil, fmt.Errorf(
			"agent %q declares transport %d, which this build does not know how to reach — "+
				"upgrade GAIA or fix the catalog entry", agent.ID, int(agent.Transport))
	}
}
