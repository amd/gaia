// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/ui/modelsetup"
)

// /models, /gpu and first-run setup open the setup screens over the chat.
// Downloads started from /models keep going when the screen closes, and the
// status line says how far they are, so the user can keep chatting.

// newChatSetupAPI is the backend the screens choose through. A var so tests
// script it instead of starting a daemon.
var newChatSetupAPI = func() setupapi.API { return setupapi.NewDaemon() }

// chatSetupMotion is how much the screens animate. A var for tests.
var chatSetupMotion = modelsetup.EnvMotion

// gpuProbedMsg says whether this PC has an integrated GPU whose memory share
// /gpu can change, so the palette offers /gpu only where it means something.
type gpuProbedMsg struct{ applies bool }

// newGPUProbeAPI never starts a daemon: the probe only asks one that runs.
var newGPUProbeAPI = func() setupapi.API { return setupapi.NewAttachedDaemon() }

func probeGPUCmd() tea.Cmd {
	api := newGPUProbeAPI()
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		g, err := api.GPU(ctx, 0)
		return gpuProbedMsg{applies: err == nil && g.Applies}
	}
}

// openModelSetup opens the screens in mode. An earlier /models still
// downloading in the background is shown again rather than replaced.
func (m ChatModel) openModelSetup(mode modelsetup.Mode) (tea.Model, tea.Cmd) {
	m.palette.open = false
	if m.modelSetup != nil {
		if busy, _ := m.modelSetup.Downloading(); busy {
			if m.modelSetup.Mode() == mode || mode == modelsetup.ModeModels {
				m.modelSetupOpen = true
				return m, nil
			}
			return m.statusNote("A model download is still running. Type /models to see it, and try again when it finishes."), nil
		}
		if mode == modelsetup.ModeModels && m.modelSetup.Mode() == modelsetup.ModeModels {
			// A download that stopped while hidden: show it as it ended, so
			// a failure can be retried and a lost connection reconnected.
			m.modelSetupOpen = true
			return m, nil
		}
	}
	s := modelsetup.New(newChatSetupAPI(), modelsetup.Options{
		Mode:         mode,
		Cloud:        m.skipLocalChatSetup(),
		Motion:       chatSetupMotion(),
		OfferInstall: mode == modelsetup.ModeFirstRun,
	})
	s, _ = s.Update(tea.WindowSizeMsg{Width: m.width, Height: m.height})
	m.modelSetup = &s
	m.modelSetupOpen = true
	return m, s.Init()
}

// updateModelSetup routes a message while the screens exist. handled false
// lets the chat handle it too (a resize) or instead (ctrl+c).
func (m ChatModel) updateModelSetup(msg tea.Msg) (tea.Model, tea.Cmd, bool) {
	switch msg := msg.(type) {
	case modelsetup.FinishedMsg:
		m.modelSetup, m.modelSetupOpen = nil, false
		m.lemonadeKnown, m.lemonadeDownRefused = false, false
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: "[✓] Setup complete."})
		m.updateViewport()
		return m, m.releaseAfterSetupGate(), true
	case modelsetup.BackMsg:
		m.modelSetup, m.modelSetupOpen = nil, false
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: setupLeftNotice})
		m.updateViewport()
		return m, m.releaseAfterSetupGate(), true
	case modelsetup.ClosedMsg:
		m.modelSetupOpen = false
		if !msg.Downloading {
			m.modelSetup = nil
			return m, nil, true
		}
		return m.statusNote("Downloads keep going; the status line shows how far they are. Type /models to see them."), nil, true
	case modelsetup.InstallServerMsg:
		m.modelSetup, m.modelSetupOpen = nil, false
		next, cmd := m.startServerInstall()
		return next, cmd, true
	case tea.WindowSizeMsg:
		s, cmd := m.modelSetup.Update(msg)
		m.modelSetup = &s
		return m, cmd, false
	case tea.KeyMsg:
		if !m.modelSetupOpen {
			return m, nil, false
		}
		if msg.Type == tea.KeyCtrlC {
			if err := m.modelSetup.Abandon(); err != nil {
				m.messages = append(m.messages, Message{Role: RoleError,
					Content: "Could not stop the model download: " + err.Error()})
			}
			return m, nil, false
		}
		s, cmd := m.modelSetup.Update(msg)
		m.modelSetup = &s
		return m, cmd, true
	}
	if !modelsetup.IsMsg(msg) {
		return m, nil, false
	}
	was, _ := m.modelSetup.Downloading()
	s, cmd := m.modelSetup.Update(msg)
	m.modelSetup = &s
	if now, _ := s.Downloading(); was && !now && !m.modelSetupOpen {
		// Ended while hidden: say how, where the user is looking. Only a
		// clean finish lets the screens go; /models picks the rest back up.
		switch kind, detail := s.Outcome(); kind {
		case "lost":
			m.messages = append(m.messages, Message{Role: RoleError,
				Content: "Lost touch with the model download: " + detail + ". Type /models to reconnect."})
		case "failed":
			m.messages = append(m.messages, Message{Role: RoleError,
				Content: detail + " didn't finish downloading. Type /models to see why and retry it."})
		default:
			m.modelSetup = nil
			m.messages = append(m.messages, Message{Role: RoleStatus, Content: "Model downloads finished. Type /models to see them."})
		}
		m.updateViewport()
		return m, nil, true
	}
	return m, cmd, true
}

// setupLeftNotice is what the chat says when first-run setup was left
// before it finished.
const setupLeftNotice = "Setup isn't finished, so chat may have no model to run on. " +
	"Type /setup to choose your models, or /provider to use a cloud provider."

// downloadHint is the status line's "downloading 38%" while a background
// download runs.
func (m ChatModel) downloadHint() string {
	if m.modelSetup == nil || m.modelSetupOpen {
		return ""
	}
	busy, pct := m.modelSetup.Downloading()
	if !busy {
		return ""
	}
	return fmt.Sprintf("downloading %d%% · /models", pct)
}
