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
// Three rules shape the whole implementation:
//
//  1. OFF unless someone asked: --full-access on this launch, or a default
//     they saved with /full-access always or `gaia config set full_access
//     true`. Nothing else turns it on, and a saved default names itself in the
//     transcript, so "why is this on?" always has an answer.
//  2. Turning it ON is deliberate: /full-access states what it means and does
//     NOT enable anything; a second, explicit /full-access confirm does.
//     Turning it OFF is one command and never gated — the safe direction is
//     never slowed down.
//  3. While it is on, the UI says so on every single frame, in a band that
//     cannot be scrolled away. A line in scrollback does not qualify: scrollback
//     scrolls, and the whole requirement is that the user always knows the agent
//     is acting without them.

const (
	fullAccessBannerText = "FULL ACCESS — the agent runs every tool " +
		"without asking. /full-access off to stop."
	// Shown when the terminal is too narrow for the sentence. Still says the
	// two things that matter: what is on, and that it is dangerous.
	fullAccessBannerShort = "FULL ACCESS ON"
)

// Coloured text, not a filled band. A full-width red bar across every frame is
// read once and then resented — it competes with the answer for the rest of the
// session, which is the opposite of staying noticeable. The warning colour and
// the glyph carry it; the requirement is that it is always THERE and unscrollable,
// not that it shouts.
var fullAccessBannerStyle = lipgloss.NewStyle().Foreground(theme.Danger)

// renderFullAccessBanner draws the full-width warning band, or "" when full access is
// off.
//
// Rendered by View() outside the viewport, so it is pinned: scrolling the
// transcript cannot move it, and it is present in the same frame as whatever
// the agent just did unasked.
func (m ChatModel) renderFullAccessBanner() string {
	if !m.fullAccess || m.width <= 0 {
		return ""
	}
	text := "⚠  " + fullAccessBannerText
	if lipgloss.Width(text) > m.width {
		text = "⚠  " + fullAccessBannerShort
	}
	if lipgloss.Width(text) > m.width {
		text = "⚠ FULL ACCESS"
	}
	return fullAccessBannerStyle.Width(m.width).Render(text)
}

// armFullAccess explains what full access is and asks for a second, explicit
// command. It deliberately does not enable anything.
func (m ChatModel) armFullAccess() (tea.Model, tea.Cmd) {
	m.fullAccessArmed = true
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

// setFullAccess turns the mode on or off and tells the agent.
//
// The local flag is only the indicator; the agent is what actually stops
// asking, so a transport that cannot carry the toggle must not leave a banner
// claiming autonomy that is not in effect — nor, worse, silently drop a
// request to turn it OFF.
func (m ChatModel) setFullAccess(enabled bool) (tea.Model, tea.Cmd) {
	m.fullAccessArmed = false

	setter, ok := m.client.(client.FullAccessSetter)
	if !ok {
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: "This agent connection cannot change permission mode — " +
				"it has no control channel. Prompts stay on.",
		})
		m.updateViewport()
		return m, nil
	}
	if err := setter.SetFullAccess(enabled); err != nil {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: "Could not change permission mode: " + err.Error(),
		})
		m.updateViewport()
		return m, nil
	}

	m.fullAccess = enabled
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

// fullAccessNote records a one-line answer to a /full-access command that changed
// nothing.
func (m ChatModel) fullAccessNote(text string) ChatModel {
	m.fullAccessArmed = false
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: text})
	m.updateViewport()
	return m
}

// applyLaunchFullAccess reflects a full-access launch into the model.
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
func (m ChatModel) applyLaunchFullAccess() ChatModel {
	type launchFullAccesser interface{ FullAccessAtLaunch() bool }
	b, ok := m.client.(launchFullAccesser)
	if !ok || !b.FullAccessAtLaunch() {
		return m
	}
	m.fullAccess = true

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

// fullAccessHelpLine documents the command wherever the TUI lists what it can do.
func fullAccessHelpLine() string {
	return "/full-access — let the agent run tools without asking (off by default);\n" +
		"              add `always` to keep it on for every session, `never` to stop"
}

// isFullAccessCommand reports whether a composed line is one of the full-access
// forms, so the composer never sends it to the agent as a question.
//
// The retired /bypass forms are recognised too, so they get a rename notice
// instead of reaching the agent as a question.
func isFullAccessCommand(query string) bool {
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
		return m.fullAccessNote("Could not save the setting: " + err.Error()), nil
	}
	if !enabled {
		note := "[✓] Full access will NOT come back on the next launch (saved in " + path + ")."
		if m.fullAccess {
			note += "\n    It is still on for THIS session — /full-access off to stop it now."
		}
		return m.fullAccessNote(note), nil
	}
	if m.fullAccess {
		return m.fullAccessNote("[✓] Full access saved as the default for every session (" + path + ")."), nil
	}
	// Not on yet: the same two-step confirmation any other turn-on goes
	// through. Saving the preference must not be a way to skip being told
	// what the mode does.
	saved := m.fullAccessNote("[✓] Saved as the default for every session (" + path + ").")
	return saved.armFullAccess()
}

// WithNotice appends one status line before the first frame, for a launch that
// has something to say about how it was started.
func (m ChatModel) WithNotice(text string) ChatModel {
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: text})
	return m
}
