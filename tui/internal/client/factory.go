package client

import (
	"fmt"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
)

// ForAgentOptions configures the transport built by ForAgent.
type ForAgentOptions struct {
	// Dev is developer mode (the TUI's --dev). It turns on the subprocess
	// transport's stderr diagnostics and appends the agent's DevArgs to its
	// argv, so the child logs verbosely too.
	Dev bool
	// Model / MaxSteps override the sidecar defaults on the daemon transport.
	// Ignored by the subprocess transport, which takes its model via BinaryArgs.
	Model    string
	MaxSteps int
	// Logf receives transport diagnostics. Never given a token.
	Logf func(format string, args ...any)
	// Interactive declares that a human is watching and can answer a question
	// the agent asks mid-run. Only the interactive chat view sets it; a one-shot
	// leaves it false so an agent that needs an answer says so and stops,
	// instead of parking until the question times out.
	Interactive bool
	// BypassPermissions starts the agent with confirmation prompts OFF: every
	// gated tool runs without asking. Off unless the launch explicitly asked
	// for it, and the UI must say so on every frame while it is on.
	BypassPermissions bool
	// UseClaude routes the agent's inference to Anthropic's Claude API instead
	// of the local Lemonade backend — the conversation leaves the machine.
	// Subprocess transport only; the daemon transport refuses it.
	UseClaude bool
	// ClaudeModel picks which Claude model UseClaude uses; empty lets the
	// agent pick its default. Meaningless without UseClaude, and refused.
	ClaudeModel string
}

// BypassPermissionsFlag is the argument that starts a subprocess agent with
// prompts off. Must match the flag gaia_agent.stdio's parser declares, and
// SubprocessClient.BypassAtLaunch scans argv for exactly this string.
const BypassPermissionsFlag = "--bypass-permissions"

// UseClaudeFlag is the argument that points a subprocess agent at Anthropic's
// Claude API instead of the local Lemonade backend. Must match the flag the
// agent's parser declares, and SubprocessClient.ClaudeAtLaunch scans argv for
// exactly this string.
const UseClaudeFlag = "--use-claude"

// ClaudeModelFlag selects the Claude model; forwarded only alongside
// UseClaudeFlag, followed by the model id as its own argv entry.
const ClaudeModelFlag = "--claude-model"

// ForAgent builds the transport a catalog entry declares.
//
// This is the single transport switch: the chat UI, the hub, and the
// non-interactive CLI paths all go through it, so adding a transport never means
// finding every launch site. It deliberately lives here rather than on a Bubble
// Tea model — the headless CLI paths need it without a UI.
func ForAgent(agent catalog.Agent, opts ForAgentOptions) (AgentClient, error) {
	// A model with no backend switch would be accepted and then change nothing.
	if opts.ClaudeModel != "" && !opts.UseClaude {
		return nil, fmt.Errorf(
			"a Claude model (%q) was set without Claude mode — pass --use-claude too, "+
				"or drop --claude-model", opts.ClaudeModel)
	}

	switch agent.Transport {
	case catalog.TransportDaemon:
		if opts.UseClaude {
			return nil, fmt.Errorf(
				"agent %q runs over the daemon transport, which cannot switch inference "+
					"backends — --use-claude only works for an agent the TUI spawns itself. "+
					"Drop the flag, or run the flagship (`gaia tui`), which is one",
				agent.ID)
		}
		return NewSSEClient(agent.ID, daemon.New(daemon.Options{Logf: opts.Logf}), SSEOptions{
			Model:       opts.Model,
			MaxSteps:    opts.MaxSteps,
			Logf:        opts.Logf,
			Interactive: opts.Interactive,
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
		// Appended, never mutated in place: BinaryArgs belongs to the catalog
		// entry, and appending to it directly would let a full slice alias the
		// catalog's backing array and leak --dev into the next launch.
		args := agent.BinaryArgs
		if opts.Dev && len(agent.DevArgs) > 0 {
			args = append(append([]string{}, args...), agent.DevArgs...)
		}
		if opts.BypassPermissions {
			args = append(append([]string{}, args...), BypassPermissionsFlag)
		}
		if opts.UseClaude {
			extra := []string{UseClaudeFlag}
			if opts.ClaudeModel != "" {
				extra = append(extra, ClaudeModelFlag, opts.ClaudeModel)
			}
			args = append(append([]string{}, args...), extra...)
		}
		if agent.CanonicalEvents {
			return NewCanonicalSubprocessClient(bin, args, opts.Dev), nil
		}
		return NewSubprocessClient(bin, args, opts.Dev), nil

	default:
		return nil, fmt.Errorf(
			"agent %q declares transport %d, which this build does not know how to reach — "+
				"upgrade GAIA or fix the catalog entry", agent.ID, int(agent.Transport))
	}
}
