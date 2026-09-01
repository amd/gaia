// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "strings"

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
	return trimmed == "/model" || strings.HasPrefix(trimmed, "/model ")
}
