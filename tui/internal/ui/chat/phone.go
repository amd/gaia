// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/phone"
	"github.com/amd/gaia/tui/internal/ui/phonepanel"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// /phone opens the phone alerts panel; while the chat is up, an open alert
// shows as one line under the header. See docs/guides/phone-alerts.mdx.

// phoneBannerEvery is how often the chat asks for open alerts.
const phoneBannerEvery = 15 * time.Second

// newPhoneAPI builds the phone client; start=false never starts a daemon.
// A variable so tests stand in a fake daemon.
var newPhoneAPI = func(start bool) *phone.Client { return phone.NewDaemon(start) }

type phoneBannerTickMsg struct{}

type phoneBannerMsg struct {
	alerts []phone.Alert
	err    error
}

func phoneBannerPoll(after time.Duration) tea.Cmd {
	if after <= 0 {
		return func() tea.Msg { return phoneBannerTickMsg{} }
	}
	return tea.Tick(after, func(time.Time) tea.Msg { return phoneBannerTickMsg{} })
}

func fetchPhoneBanner(api *phone.Client) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		alerts, err := api.Alerts(ctx, true)
		return phoneBannerMsg{alerts: alerts, err: err}
	}
}

func (m ChatModel) openPhonePanel() (tea.Model, tea.Cmd) {
	p := phonepanel.New(newPhoneAPI(true), m.width, m.height)
	m.phonePanel = &p
	m.palette.open = false
	return m, p.Init()
}

// routePhoneMsg sends the panel what it owns, and runs the banner poll.
func (m ChatModel) routePhoneMsg(msg tea.Msg) (tea.Model, tea.Cmd, bool) {
	switch msg := msg.(type) {
	case phoneBannerTickMsg:
		if m.phoneBannerOff {
			return m, nil, true
		}
		if m.phoneBannerAPI == nil {
			// Attach-only, and kept, so a poll is one request to a known daemon.
			m.phoneBannerAPI = newPhoneAPI(false)
		}
		return m, fetchPhoneBanner(m.phoneBannerAPI), true
	case phoneBannerMsg:
		next := m.applyPhoneBanner(msg)
		if next.phoneBannerOff {
			return next, nil, true
		}
		return next, phoneBannerPoll(phoneBannerEvery), true
	case phonepanel.CloseMsg:
		m.phonePanel = nil
		m.resize()
		m.updateViewport()
		// What was handled in the panel leaves the banner now, not in 15 s.
		if m.phoneBannerOff || m.phoneBannerAPI == nil {
			return m, nil, true
		}
		return m, fetchPhoneBanner(m.phoneBannerAPI), true
	}
	if m.phonePanel == nil {
		return m, nil, false
	}
	switch msg := msg.(type) {
	case tea.KeyMsg:
		if msg.Type == tea.KeyCtrlC {
			return m, nil, false
		}
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.resize()
	case tea.MouseMsg, phonepanel.Msg:
	default:
		return m, nil, false
	}
	next, cmd := m.phonePanel.Update(msg)
	p := next.(phonepanel.Model)
	m.phonePanel = &p
	return m, cmd, true
}

func (m ChatModel) applyPhoneBanner(msg phoneBannerMsg) ChatModel {
	was := m.phoneBanner
	switch {
	case errors.Is(msg.err, phone.ErrUnavailable):
		// This daemon has no phone alerts: stop asking.
		m.phoneBannerOff = true
		m.phoneBanner = ""
	case msg.err != nil:
		// The service isn't up yet, or phone isn't set up: the banner only
		// ever reports an open alert, and /phone shows the error itself.
		m.phoneBanner = ""
	case len(msg.alerts) == 0:
		m.phoneBanner = ""
	default:
		m.phoneBanner = msg.alerts[0].Title
		if more := len(msg.alerts) - 1; more > 0 {
			m.phoneBanner += fmt.Sprintf(" (+%d more)", more)
		}
	}
	if m.phoneBanner != was {
		m.resize()
		m.updateViewport()
	}
	return m
}

var phoneBannerStyle = lipgloss.NewStyle().Foreground(theme.Warning)

func (m ChatModel) renderPhoneBanner() string {
	if m.phoneBanner == "" || m.width <= 0 {
		return ""
	}
	return phoneBannerStyle.Render(truncateRunes(" ⚠ "+m.phoneBanner+" · /phone", max(m.width-1, 1)))
}
