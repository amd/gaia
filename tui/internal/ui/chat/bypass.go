// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Bypass-permissions mode: the agent runs every confirmation-gated tool —
// shell commands, file writes — without asking.
//
// Three rules shape the whole implementation:
//
//  1. OFF on a fresh launch, always. It is the zero value of a bool, restored
//     from nothing, so there is no path that turns it on without someone asking
//     for it on this launch.
//  2. Turning it ON is deliberate: /bypass states what it means and does NOT
//     enable anything; a second, explicit /bypass confirm does. Turning it OFF
//     is one command and never gated — the safe direction is never slowed down.
//  3. While it is on, the UI says so on every single frame, in a band that
//     cannot be scrolled away. A line in scrollback does not qualify: scrollback
//     scrolls, and the whole requirement is that the user always knows the agent
//     is acting without them.

const (
	bypassBannerText = "BYPASS PERMISSIONS — the agent runs every tool " +
		"without asking. /bypass off to stop."
	// Shown when the terminal is too narrow for the sentence. Still says the
	// two things that matter: what is on, and that it is dangerous.
	bypassBannerShort = "BYPASS PERMISSIONS ON"
)

var bypassBannerStyle = lipgloss.NewStyle().
	Bold(true).
	Foreground(theme.OnFill).
	Background(theme.DangerFillBG)

// renderBypassBanner draws the full-width warning band, or "" when bypass is
// off.
//
// Rendered by View() outside the viewport, so it is pinned: scrolling the
// transcript cannot move it, and it is present in the same frame as whatever
// the agent just did unasked.
func (m ChatModel) renderBypassBanner() string {
	if !m.bypassPermissions || m.width <= 0 {
		return ""
	}
	text := "⚠  " + bypassBannerText
	if lipgloss.Width(text) > m.width {
		text = "⚠  " + bypassBannerShort
	}
	if lipgloss.Width(text) > m.width {
		text = "⚠ BYPASS"
	}
	return bypassBannerStyle.Width(m.width).Render(text)
}

// armBypass explains what bypass mode is and asks for a second, explicit
// command. It deliberately does not enable anything.
func (m ChatModel) armBypass() (tea.Model, tea.Cmd) {
	m.bypassArmed = true
	m.messages = append(m.messages, Message{
		Role: RoleStatus,
		Content: "[!] Bypass permissions would let " + m.agentName +
			" run every tool with no prompt — shell commands, file writes, " +
			"anything it decides to do — for the rest of this session.\n" +
			"    Type /bypass confirm to turn it on, or /bypass off at any " +
			"time to turn it back off.",
	})
	m.updateViewport()
	return m, nil
}

// setBypass turns the mode on or off and tells the agent.
//
// The local flag is only the indicator; the agent is what actually stops
// asking, so a transport that cannot carry the toggle must not leave a banner
// claiming autonomy that is not in effect — nor, worse, silently drop a
// request to turn it OFF.
func (m ChatModel) setBypass(enabled bool) (tea.Model, tea.Cmd) {
	m.bypassArmed = false

	bypasser, ok := m.client.(client.PermissionBypasser)
	if !ok {
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: "This agent connection cannot change permission mode — " +
				"it has no control channel. Prompts stay on.",
		})
		m.updateViewport()
		return m, nil
	}
	if err := bypasser.SetBypassPermissions(enabled); err != nil {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: "Could not change permission mode: " + err.Error(),
		})
		m.updateViewport()
		return m, nil
	}

	m.bypassPermissions = enabled
	if enabled {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "[!] BYPASS PERMISSIONS IS ON. " + m.agentName +
				" will run tools without asking until you type /bypass off.",
		})
	} else {
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "[✓] Bypass permissions off — you will be asked again before gated tools run.",
		})
	}
	m.updateViewport()
	return m, nil
}

// bypassNote records a one-line answer to a /bypass command that changed
// nothing.
func (m ChatModel) bypassNote(text string) ChatModel {
	m.bypassArmed = false
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: text})
	m.updateViewport()
	return m
}

// applyLaunchBypass reflects a --bypass-permissions launch flag into the model.
//
// The flag reaches the AGENT through its own argv; this only makes the UI tell
// the truth about it from the first frame. Without it the banner would appear
// only after the first manual toggle, which is the exact failure the banner
// exists to prevent.
func (m ChatModel) applyLaunchBypass() ChatModel {
	type launchBypasser interface{ BypassAtLaunch() bool }
	if b, ok := m.client.(launchBypasser); ok && b.BypassAtLaunch() {
		m.bypassPermissions = true
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "[!] Launched with --bypass-permissions: " + m.agentName +
				" runs tools without asking. Type /bypass off to turn it off.",
		})
	}
	return m
}

// bypassHelpLine documents the command wherever the TUI lists what it can do.
func bypassHelpLine() string {
	return "/bypass — let the agent run tools without asking (off by default)"
}

// isBypassCommand reports whether a composed line is one of the /bypass forms,
// so the composer never sends it to the agent as a question.
func isBypassCommand(query string) bool {
	switch strings.TrimSpace(query) {
	case "/bypass", "/bypass on", "/bypass off", "/bypass confirm":
		return true
	}
	return false
}
