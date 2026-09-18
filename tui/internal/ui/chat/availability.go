// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
)

// capabilityProbeTimeout bounds the one-shot warm-up. Generous next to the
// 8s round-trip inside it, because a first probe may spawn the sidecar — but
// finite, so a wedged daemon cannot leave a goroutine running for the session.
const capabilityProbeTimeout = 60 * time.Second

// capabilityProber is implemented by transports that need an async warm-up
// before client.CapabilityReporter.Supports has a real answer (SSEClient's
// contract negotiation) -- a transport without this interface (a subprocess
// client, or a test double) has nothing to probe.
type capabilityProber interface {
	ProbeCapabilities(ctx context.Context) error
}

// capabilitiesProbedMsg carries a ProbeCapabilities failure back into
// Update() purely for logging. There is nothing to re-render specially: the
// palette and /help already read Supports live (availableCommandSet), and a
// failed probe just leaves a gated command in its "unknown" -- never hidden
// -- state, same as before this ran.
type capabilitiesProbedMsg struct{ err error }

// probeCapabilitiesCmd is Init's async warm-up for capability gating.
// SSEClient.Supports cannot block on a network round-trip from
// paletteFiltered/syncPalette's synchronous, per-keystroke call sites, so the
// probe runs once, up front, off the UI goroutine, and the answer is ready
// well before a user reaches for a gated command.
func (m ChatModel) probeCapabilitiesCmd() tea.Cmd {
	prober, ok := m.client.(capabilityProber)
	if !ok {
		return nil
	}
	return func() tea.Msg {
		// Bounded: the probe can spawn a sidecar, and an unbounded one would
		// leave a goroutine alive for the whole session. Timing out leaves the
		// capability unknown, which shows the command rather than hiding it.
		ctx, cancel := context.WithTimeout(context.Background(), capabilityProbeTimeout)
		defer cancel()
		return capabilitiesProbedMsg{err: prober.ProbeCapabilities(ctx)}
	}
}

// commandSupported answers whether m.client currently supports cap, via
// client.CapabilityReporter when the transport implements it. A transport
// that does not implement the interface at all (a test double, or a future
// transport that never grew one) is treated the same as one that has not
// finished probing yet -- unknown, never hidden. See CapabilityReporter's own
// doc comment (client.go) for why hiding on doubt is worse than a refusal
// that explains itself.
func (m ChatModel) commandSupported(c client.Capability) (supported, known bool) {
	reporter, ok := m.client.(client.CapabilityReporter)
	if !ok {
		return false, false
	}
	return reporter.Supports(c)
}

// availableCommandSet is the ONE source of truth for which slash commands
// this session can actually run -- the "/" palette (palette.go) and the
// /help panel (helpoverlay.go, via components.HelpState) both read it, so
// they can never disagree about what an agent supports.
func (m ChatModel) availableCommandSet() map[string]bool {
	set := make(map[string]bool, len(paletteCommands))
	for _, c := range paletteCommands {
		switch c.Name {
		case "/memory":
			// known && !supported is the only case worth hiding for: a peer
			// that has actually answered and is below the memory contract.
			// Everything else -- not yet probed, or no CapabilityReporter at
			// all -- shows the command and lets the real attempt explain
			// itself (memoryview.go).
			if supported, known := m.commandSupported(client.CapabilityMemory); known && !supported {
				continue
			}
		case "/setup":
			if m.agentID != setupAgentID {
				continue
			}
		case modelCommandPrefix, "/provider":
			if !m.supportsModelCommand() {
				continue
			}
		}
		set[c.Name] = true
	}
	return set
}

// availableCommandNames is availableCommandSet in paletteCommands' own
// declared order, for surfaces (the /help panel) that render an ordered list
// rather than test membership.
func (m ChatModel) availableCommandNames() []string {
	set := m.availableCommandSet()
	names := make([]string, 0, len(set))
	for _, c := range paletteCommands {
		if set[c.Name] {
			names = append(names, c.Name)
		}
	}
	return names
}

// AvailableCommandNames is availableCommandNames, exported for the root
// model (a different package), which owns the /help panel on the path where
// a preflight gate wraps this ChatModel.
func (m ChatModel) AvailableCommandNames() []string {
	return m.availableCommandNames()
}
