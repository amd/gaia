package chat

import "github.com/amd/gaia/tui/internal/control"

// IsStreaming reports whether a query is currently in flight.
func (m ChatModel) IsStreaming() bool { return m.streaming }

// AgentID is the catalog id of the agent this chat is bound to.
func (m ChatModel) AgentID() string { return m.agentID }

// ControlSnapshot lets the control API describe a standalone chat session (the
// `gaia chat --subprocess` entry point, where ChatModel is the root model).
func (m ChatModel) ControlSnapshot() control.Snapshot {
	snap := control.Snapshot{
		View:      "chat",
		Agent:     m.agentID,
		Streaming: m.streaming,
	}
	if m.providerPanel != nil {
		snap.Overlay = "provider"
	}
	snap.Chat = m.controlChatState()
	return snap
}

// controlChatState reports the transcript's own diagnostics — see
// control.ChatState for why each field is there rather than left to be
// inferred from the rendered screen.
func (m ChatModel) controlChatState() *control.ChatState {
	owner := "terminal"
	if m.mouseCaptured {
		owner = "app"
	}
	motion := ""
	if m.mouseCaptured {
		motion = "cell"
		if m.mouseCaptureAllMotion {
			motion = "all"
		}
	}
	return &control.ChatState{
		Messages:     len(m.messages),
		ScrollY:      m.viewport.YOffset,
		ContentRows:  m.viewport.TotalLineCount(),
		AtBottom:     m.viewport.AtBottom(),
		FollowTail:   m.followTail,
		MouseOwner:   owner,
		MouseMotion:  motion,
		SelectMode:   m.mouseSelectMode,
		ViewportRows: m.viewport.Height,
		HeaderRows:   m.contentHeaderRows(),
		HelpOpen:     m.help.Open,
		Cost:         m.controlSessionCost(),
	}
}

// controlSessionCost publishes the session ledger, nil before the first turn.
func (m ChatModel) controlSessionCost() *control.SessionCost {
	if len(m.cost.turns) == 0 {
		return nil
	}
	d, steps, tools, in, out, cached, measured := m.cost.totals()
	sc := &control.SessionCost{
		Turns:         len(m.cost.turns),
		MeasuredTurns: measured,
		ActiveSeconds: d.Seconds(),
		Steps:         steps,
		ToolCalls:     tools,
		InputTokens:   in,
		OutputTokens:  out,
		CachedTokens:  cached,
		Model:         m.costModelName(),
	}
	if price := lookupPrice(m.modelID); price != nil {
		usd := price.totalUSD(in, cached, out)
		sc.USD = &usd
	}
	return sc
}
