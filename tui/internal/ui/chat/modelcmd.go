// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	"github.com/amd/gaia/tui/internal/client"
)

// `/model` and `/model <id>` — live model switching. Unlike /bypass this
// command genuinely needs the AGENT: only it can discover which local
// Lemonade models are downloaded and validate a Claude id against a live
// credential. So it is recognized here (so the composer never treats it as a
// question typed at the LLM) but still dispatched over the query channel —
// see gaia_agent.stdio.run_model_command for why that channel, not the
// fire-and-forget control one /bypass uses, is the only one this transport
// can reliably carry a response back on.

// modelSwitchAgentID is the only agent whose transport understands `/model`
// today — the flagship's stdio.py intercepts it before it ever reaches the
// LLM (run_model_command). Every other agent (a daemon-relay agent like
// email, or another subprocess agent) has no such interception, so sending it
// the literal text would ship as an uncomprehended chat question — mirrors
// the existing preScanAgentID scoping (model.go) for the same reason: the
// feature lives agent-side, not transport-side, so it has to be gated by
// WHICH agent, not by what kind of connection this is.
const modelSwitchAgentID = "gaia"

// supportsModelCommand reports whether this session's agent understands
// `/model`. Checked before dispatch (see submit) so an unsupported agent gets
// an explicit refusal — the same shape as setBypass's capability check in
// bypass.go — instead of `/model` silently turning into a literal question.
func (m ChatModel) supportsModelCommand() bool {
	return m.agentID == modelSwitchAgentID
}

// isModelCommand reports whether a composed line is `/model` or `/model
// <id>`, so the composer routes it to a command dispatch instead of asking
// the agent a free-text question — mirrors isBypassCommand. Recognition is
// independent of supportsModelCommand: a line still LOOKS like a command on
// an agent that doesn't support it, and submit() answers that case with a
// refusal rather than falling through to a literal question either way.
func isModelCommand(query string) bool {
	trimmed := strings.TrimSpace(query)
	return trimmed == modelCommandPrefix ||
		strings.HasPrefix(trimmed, modelCommandPrefix+" ")
}

// modelCommandPrefix must match gaia_agent.stdio.MODEL_COMMAND_PREFIX — the
// agent dispatches on the same literal.
const modelCommandPrefix = "/model"

// modelCommandArg is the id in `/model <id>`, or "" for a bare `/model`
// (which asks the agent to LIST models rather than switch to one).
func modelCommandArg(query string) string {
	return strings.TrimSpace(
		strings.TrimPrefix(strings.TrimSpace(query), modelCommandPrefix))
}

// refuseModelSwitch returns the reason `/model <id>` must not be sent to the
// agent at all, or "" when it should be.
//
// This is a PRE-flight check, not a replacement for the agent's own: the
// agent validates again (_switch_model in stdio.py) and is authoritative,
// because only it can enumerate the local models Lemonade actually has
// downloaded. What this catches is the two cases where a round-trip could
// only ever end in the same refusal, and where the round-trip itself is the
// problem — a switch turn against a wedged or Claude-only session can sit
// there for its full timeout before saying "no".
//
// It never rewrites the request into one that would work. Choosing a backend
// is the user's call; the refusal names both ways forward and stops.
func (m ChatModel) refuseModelSwitch(arg string) string {
	if arg == "" {
		return "" // a bare `/model` lists; there is nothing to validate
	}

	if client.IsClaudeModelID(arg) {
		if _, ok := client.KnownClaudeModel(arg); ok {
			return ""
		}
		return "Unknown Claude model `" + arg + "`. Accepted ids: `" +
			strings.Join(client.ClaudeModelIDs(), "`, `") + "`. " +
			"There is no date suffix — it is `" + client.ExampleClaudeModelID + "`."
	}

	// A local id, and the last thing the agent said was that the local server
	// is not answering. Worth saying immediately — but ONCE.
	//
	// lemonadeUp is a cached snapshot: it is only ever written by a model-state
	// ping (canonical.go), which arrives at startup and after a successful
	// switch, so nothing refreshes it when the user starts Lemonade — via
	// /setup or in another terminal — and retries. A refusal that kept firing
	// off that stale snapshot would be a dead end no retry could ever clear,
	// which is the failure this whole change exists to remove. So the second
	// attempt goes through to the agent, which probes live (_lemonade_models)
	// and is the only authority on the answer.
	if m.lemonadeKnown && !m.lemonadeUp && !m.lemonadeDownRefused {
		where := m.lemonadeBaseURL
		if where == "" {
			where = "its configured address"
		}
		return "Cannot switch to local model `" + arg +
			"`: the Lemonade server was not reachable at " + where + ". " +
			"Start it with `lemonade-server serve`, or with /setup, and send this " +
			"again — the retry re-checks. " + m.remoteAlternative() +
			" Nothing was switched — this session is still on " +
			m.currentModelName() + "."
	}
	return ""
}

// remoteAlternative names the other way forward, phrased for where the
// session actually is. "Stay remote" is only true if it already is remote —
// suggesting it to a local session both misreads the state and pushes
// off-machine inference at someone who may have no ANTHROPIC_API_KEY.
func (m ChatModel) remoteAlternative() string {
	if m.claudeMode {
		return "Or stay on Claude — this session already is."
	}
	return "Or move to Claude with `/model " + client.ExampleClaudeModelID +
		"`, which sends the conversation to Anthropic and needs ANTHROPIC_API_KEY."
}

// currentModelName names the model this session is on right now, for a
// message that has to say what did NOT change. Falls back to the launch
// flag's model before the agent's first ping (see renderModelChip for why
// that gap exists), and to a plain description when neither is known.
func (m ChatModel) currentModelName() string {
	if m.modelDisplay != "" {
		return m.modelDisplay
	}
	if m.launchClaudeModel != "" {
		return claudeLaunchName(m.launchClaudeModel)
	}
	if m.claudeMode {
		return "Claude"
	}
	return "its current model"
}
