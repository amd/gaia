// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	tea "github.com/charmbracelet/bubbletea"
)

// HelpState is an open-or-closed help panel and its scroll position.
//
// It lives here rather than in the root model because the root model is not
// always there. `gaia run <agent>` puts the chat model straight in front of
// Bubble Tea with no root wrapper, so a help panel implemented only in root is
// simply absent on that path — which is what happened: `/help` in a `run gaia`
// session toggled nothing and printed nothing, silently, for every user who
// launched an agent directly rather than through the hub.
type HelpState struct {
	Open   bool
	Ctx    HelpContext
	Scroll int
}

// Toggle opens or closes the panel, always starting a fresh open at the top.
func (h *HelpState) Toggle(ctx HelpContext) {
	h.Open = !h.Open
	h.Ctx = ctx
	h.Scroll = 0
}

// Dismiss closes the panel. Safe to call when it is already closed.
func (h *HelpState) Dismiss() {
	h.Open = false
	h.Scroll = 0
}

// HandleKey routes one keystroke while the panel is open. It reports whether it
// consumed the key: navigation keys scroll the panel, and anything else closes
// it — so a panel is never a trap, whatever the user reaches for.
func (h *HelpState) HandleKey(msg tea.KeyMsg, width, height int) bool {
	if !h.Open {
		return false
	}
	if delta, jump, handled := HelpScrollKey(msg, height); handled {
		max := HelpMaxScroll(h.Ctx, width, height)
		if jump {
			h.Scroll = clampInt(delta, 0, max)
		} else {
			h.Scroll = clampInt(h.Scroll+delta, 0, max)
		}
		return true
	}
	h.Dismiss()
	return true
}

// Render composites the panel over base, or returns base untouched when closed.
func (h HelpState) Render(base string, width, height int) string {
	if !h.Open {
		return base
	}
	return RenderHelpOverlay(h.Ctx, base, width, height, h.Scroll)
}

// helpScrollToEnd is an intentionally-oversized jump target for the End key —
// callers always clamp it against HelpMaxScroll, so its only job is to be
// bigger than any real scroll range.
const helpScrollToEnd = 1 << 30

// HelpScrollKey maps a keystroke to a scroll movement, mirroring the keys the
// transcript itself uses so the panel needs no separate vocabulary.
func HelpScrollKey(msg tea.KeyMsg, height int) (delta int, jump, handled bool) {
	switch msg.Type {
	case tea.KeyUp:
		return -1, false, true
	case tea.KeyDown:
		return 1, false, true
	case tea.KeyPgUp:
		return -helpPageStep(height), false, true
	case tea.KeyPgDown:
		return helpPageStep(height), false, true
	case tea.KeyHome:
		return 0, true, true
	case tea.KeyEnd:
		return helpScrollToEnd, true, true
	}
	return 0, false, false
}

// helpPageStep mirrors the transcript's own PgUp/PgDn: half the window, never
// less than one line.
func helpPageStep(height int) int {
	if step := height / 2; step > 0 {
		return step
	}
	return 1
}
