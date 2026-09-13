// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Full-access mode: the agent runs every confirmation-gated tool — shell
// commands, file writes — without asking.
//
// Named "bypass permissions" internally and "full access" to the user: the
// mode is something people deliberately turn on for a working session, and a
// name that sounds like defeating a safeguard reads as something you should
// not do. /bypass still works as an alias.
//
// Three rules shape the whole implementation:
//
//  1. OFF on a fresh launch, always. It is the zero value of a bool, restored
//     from nothing, so there is no path that turns it on without someone asking
//     for it on this launch.
//  2. Turning it ON is deliberate: /full-access states what it means and does
//     NOT enable anything; a second, explicit /full-access confirm does.
//     Turning it OFF is one command and never gated — the safe direction is
//     never slowed down.
//  3. While it is on, the UI says so on every single frame, in a band that
//     cannot be scrolled away. A line in scrollback does not qualify: scrollback
//     scrolls, and the whole requirement is that the user always knows the agent
//     is acting without them.

const (
	bypassBannerText = "FULL ACCESS — the agent runs every tool " +
		"without asking. /full-access off to stop."
	// Shown when the terminal is too narrow for the sentence. Still says the
	// two things that matter: what is on, and that it is dangerous.
	bypassBannerShort = "FULL ACCESS ON"
)

// Coloured text, not a filled band. A full-width red bar across every frame is
// read once and then resented — it competes with the answer for the rest of the
// session, which is the opposite of staying noticeable. The warning colour and
// the glyph carry it; the requirement is that it is always THERE and unscrollable,
// not that it shouts.
var bypassBannerStyle = lipgloss.NewStyle().Foreground(theme.Danger)

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
		Content: "[!] Full access would let " + m.agentName +
			" run every tool with no prompt — shell commands, file writes, " +
			"anything it decides to do — for the rest of this session.\n" +
			"    Type /full-access confirm to turn it on, or /full-access off at any " +
			"time to turn it back off.\n" +
			"    /full-access always keeps it on for every future session too.",
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
			Content: "[!] FULL ACCESS IS ON. " + m.agentName +
				" will run tools without asking until you type /full-access off.",
		})
	} else {
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "[✓] Full access off — you will be asked again before gated tools run.",
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

// applyLaunchBypass reflects a full-access launch into the model.
//
// The flag reaches the AGENT through its own argv; this only makes the UI tell
// the truth about it from the first frame. Without it the banner would appear
// only after the first manual toggle, which is the exact failure the banner
// exists to prevent.
//
// It also names WHICH source turned it on. A persisted preference is the one
// that can be on without anybody asking for it today, so "why is this on?"
// has to be answerable from the transcript — and the answer has to name the
// command that makes it stop, which differs by source.
func (m ChatModel) applyLaunchBypass() ChatModel {
	type launchBypasser interface{ BypassAtLaunch() bool }
	b, ok := m.client.(launchBypasser)
	if !ok || !b.BypassAtLaunch() {
		return m
	}
	m.bypassPermissions = true

	reason := "Launched with --full-access"
	undo := "Type /full-access off to turn it off for this session."
	if preflight.ReadFullAccess().Enabled {
		reason = "Full access is ON for every session (full_access in your GAIA config)"
		undo = "Type /full-access off for this session, or /full-access never to stop it coming back."
	}
	m.messages = append(m.messages, Message{
		Role:    RoleStatus,
		Content: "[!] " + reason + ": " + m.agentName + " runs tools without asking.\n    " + undo,
	})
	return m
}

// bypassHelpLine documents the command wherever the TUI lists what it can do.
func bypassHelpLine() string {
	return "/full-access — let the agent run tools without asking (off by default);\n" +
		"              add `always` to keep it on for every session, `never` to stop"
}

// isBypassCommand reports whether a composed line is one of the full-access
// forms, so the composer never sends it to the agent as a question.
//
// /bypass is the old name and still works; both spellings are recognised so a
// user who learned the first one is never told their command does not exist.
func isBypassCommand(query string) bool {
	switch strings.TrimSpace(query) {
	case "/full-access", "/full-access on", "/full-access off", "/full-access confirm",
		"/full-access always", "/full-access never",
		"/bypass", "/bypass on", "/bypass off", "/bypass confirm":
		return true
	}
	return false
}

// setFullAccessDefault persists the preference so it survives this session.
//
// Turning it ON also turns it on NOW (the user asked for the mode, not just a
// line in a file). Turning it OFF only clears the preference and leaves the
// session alone: /full-access off is the command for "stop now", and quietly
// doing both would make one command mean two things.
func (m ChatModel) setFullAccessDefault(enabled bool) (tea.Model, tea.Cmd) {
	path, err := preflight.WriteFullAccess(enabled)
	if err != nil {
		return m.bypassNote("Could not save the setting: " + err.Error()), nil
	}
	if !enabled {
		note := "[✓] Full access will NOT come back on the next launch (saved in " + path + ")."
		if m.bypassPermissions {
			note += "\n    It is still on for THIS session — /full-access off to stop it now."
		}
		return m.bypassNote(note), nil
	}
	if m.bypassPermissions {
		return m.bypassNote("[✓] Full access saved as the default for every session (" + path + ")."), nil
	}
	// Not on yet: the same two-step confirmation any other turn-on goes
	// through. Saving the preference must not be a way to skip being told
	// what the mode does.
	saved := m.bypassNote("[✓] Saved as the default for every session (" + path + ").")
	return saved.armBypass()
}
