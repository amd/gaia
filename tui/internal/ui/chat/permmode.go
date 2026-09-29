// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
)

// Permission modes, cycled with Shift+Tab the way Claude Code cycles its own:
//
//	ask → accept edits → full access → ask
//
// "ask" prompts for every gated tool. "accept edits" lets file edits inside the
// workspace run unasked; commands and code still ask. "full access" is the
// existing /full-access mode, and entering it keeps that mode's rule that
// turning it ON is deliberate: until full access has been confirmed once this
// session (or the session launched with it), Shift+Tab explains it and drops
// back to ask instead of switching it on. Leaving a mode is never gated.
type permissionMode int

const (
	modeAsk permissionMode = iota
	modeAcceptEdits
	modeFullAccess
)

func (p permissionMode) label() string {
	switch p {
	case modeAcceptEdits:
		return "accept edits"
	case modeFullAccess:
		return "full access"
	default:
		return "ask"
	}
}

// permissionMode is the mode the agent is actually in, derived from the two
// flags the transport confirmed — never tracked separately, so it cannot drift.
func (m ChatModel) permissionMode() permissionMode {
	switch {
	case m.fullAccess:
		return modeFullAccess
	case m.acceptEdits:
		return modeAcceptEdits
	default:
		return modeAsk
	}
}

// permissionModeHint is the one line a prompt shows about the mode.
func (m ChatModel) permissionModeHint() string {
	return "mode: " + m.permissionMode().label() + " · shift+tab to change"
}

// permissionModeStatus is the status-bar item naming the mode. Full access has
// its own, louder indicators (the banner and "/full-access off").
func (m ChatModel) permissionModeStatus() string {
	switch m.permissionMode() {
	case modeAcceptEdits:
		return "⏵⏵ accept edits (shift+tab)"
	case modeFullAccess:
		return ""
	default:
		return "ask mode (shift+tab)"
	}
}

// cyclePermissionMode is Shift+Tab.
func (m ChatModel) cyclePermissionMode() (tea.Model, tea.Cmd) {
	var next tea.Model
	switch m.permissionMode() {
	case modeAsk:
		if m.canAcceptEdits() || !m.canFullAccess() {
			// With no control channel at all this reports why nothing changed.
			next, _ = m.setAcceptEdits(true)
			break
		}
		// A transport with no accept-edits step goes straight to the next one.
		next, _ = m.toFullAccess()
	case modeAcceptEdits:
		updated, _ := m.setAcceptEdits(false)
		cm := updated.(ChatModel)
		if cm.acceptEdits {
			// Could not leave accept edits; setAcceptEdits already said why.
			next = cm
			break
		}
		next, _ = cm.toFullAccess()
	default:
		updated, _ := m.setFullAccess(false)
		next = updated
		// Out of full access always lands on ask, whatever was on before it.
		if cm := updated.(ChatModel); !cm.fullAccess && cm.acceptEdits {
			next, _ = cm.setAcceptEdits(false)
		}
	}
	cm := next.(ChatModel)
	cm.refreshConfirmationModeHint()
	return cm, nil
}

// toFullAccess is the full-access step of the cycle: on at once once unlocked
// this session, otherwise the existing explanation, leaving the mode on ask.
func (m ChatModel) toFullAccess() (tea.Model, tea.Cmd) {
	if !m.fullAccessUnlocked {
		return m.armFullAccess()
	}
	return m.setFullAccess(true)
}

// canAcceptEdits reports whether this connection can carry "accept edits".
func (m ChatModel) canAcceptEdits() bool {
	_, ok := m.client.(client.AcceptEditsSetter)
	return ok && livePermissionsAvailable(m.client)
}

// canFullAccess reports whether this connection can carry full access.
func (m ChatModel) canFullAccess() bool {
	_, ok := m.client.(client.FullAccessSetter)
	return ok && livePermissionsAvailable(m.client)
}

// refreshConfirmationModeHint keeps a prompt already on screen honest about the
// mode after a Shift+Tab.
func (m *ChatModel) refreshConfirmationModeHint() {
	if m.confirmation == nil {
		return
	}
	c := *m.confirmation
	c.SetModeHint(m.permissionModeHint())
	m.confirmation = &c
	m.updateViewport()
}

// setAcceptEdits turns "accept edits" on or off and tells the agent. Like
// setFullAccess, the local flag only follows a transport that confirmed it.
func (m ChatModel) setAcceptEdits(enabled bool) (tea.Model, tea.Cmd) {
	if m.acceptEdits == enabled {
		return m, nil
	}
	setter, ok := m.client.(client.AcceptEditsSetter)
	if !ok || !livePermissionsAvailable(m.client) {
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: "This agent connection cannot change permission mode — " +
				"it has no control channel. Prompts stay on.",
		})
		m.updateViewport()
		return m, nil
	}
	if err := setter.SetAcceptEdits(enabled); err != nil {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: "Could not change permission mode: " + err.Error(),
		})
		m.updateViewport()
		return m, nil
	}
	m.acceptEdits = enabled
	if enabled {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "[✓] Accept edits on — file edits in this folder run without asking. " +
				"Commands, code and anything outside it still ask. Shift+Tab to change.",
		})
	}
	m.updateViewport()
	return m, nil
}
