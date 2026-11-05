// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/homeassistant"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// `/home`: connect Home Assistant, test it, or disconnect it.
//
// Every step goes through the flagship sidecar's /v1/gaia/home-assistant
// routes. The token is typed into a masked question, handed straight to the
// connect call, and never stored in the model or written to the transcript.

// newHomeAPI builds the backend; a var so tests script it.
var newHomeAPI = func() homeassistant.API { return homeassistant.New() }

// homeTimeout bounds one call. Connect checks the live server, and the first
// call may start the daemon and the sidecar.
const homeTimeout = 90 * time.Second

// homeQuestionPrefix marks a QuestionModel this file owns.
const homeQuestionPrefix = "gaia-home-assistant:"

const (
	homeQAddress   = homeQuestionPrefix + "address"
	homeQToken     = homeQuestionPrefix + "token"
	homeQConnected = homeQuestionPrefix + "connected"
)

type homeStep int

const (
	homeStepChecking homeStep = iota + 1
	homeStepAddress
	homeStepToken
	homeStepConnecting
	homeStepConnected
	homeStepTesting
	homeStepDisconnecting
)

// homeState is the open /home panel. It never holds the token.
type homeState struct {
	step    homeStep
	address string
	url     string
}

// homeBusy reports whether a call is in flight.
func (s *homeState) busy() bool {
	switch s.step {
	case homeStepChecking, homeStepConnecting, homeStepTesting, homeStepDisconnecting:
		return true
	}
	return false
}

type homeStatusMsg struct {
	status homeassistant.Status
	err    error
}

type homeConnectedMsg struct {
	status homeassistant.Status
	err    error
}

type homeTestedMsg struct {
	result homeassistant.TestResult
	err    error
}

type homeDisconnectedMsg struct {
	status homeassistant.Status
	err    error
}

func homeCall[T any](call func(context.Context, homeassistant.API) T) tea.Cmd {
	api := newHomeAPI()
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), homeTimeout)
		defer cancel()
		return call(ctx, api)
	}
}

func isHomeQuestion(requestID string) bool {
	return strings.HasPrefix(requestID, homeQuestionPrefix)
}

// startHome opens /home by reading the saved state.
func (m ChatModel) startHome() (tea.Model, tea.Cmd) {
	if m.agentID != setupAgentID {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: m.agentName + " cannot use Home Assistant — only the gaia flagship agent can.",
		})
		m.updateViewport()
		return m, nil
	}
	if m.home != nil {
		if m.home.busy() {
			return m.statusNote("Home Assistant is still answering — one moment."), nil
		}
		m.question = nil
	}
	m.home = &homeState{step: homeStepChecking}
	return m.statusNote("Checking Home Assistant…"), homeCall(func(ctx context.Context, api homeassistant.API) tea.Msg {
		s, err := api.Status(ctx)
		return homeStatusMsg{status: s, err: err}
	})
}

func (m ChatModel) handleHomeStatus(msg homeStatusMsg) (tea.Model, tea.Cmd) {
	if m.home == nil {
		return m, nil
	}
	if msg.err != nil {
		m.home = nil
		return m.homeError("Could not check Home Assistant: " + msg.err.Error()), nil
	}
	if msg.status.Connected {
		return m.showHomeConnected(msg.status), nil
	}
	m = m.statusNote(homeSetupText())
	return m.askHomeAddress(homeassistant.DefaultAddress), nil
}

// homeSetupText is the three things to do in Home Assistant first.
func homeSetupText() string {
	return strings.Join([]string{
		"Home Assistant: not connected.",
		"",
		"In Home Assistant:",
		"1. Settings › Devices & services › Add integration — add",
		"   \"Model Context Protocol Server\".",
		"2. Settings › Voice assistants › Expose — choose the devices GAIA may use.",
		"3. Your profile › Security › Long-lived access tokens — create a token.",
		"",
		"Then enter the address and token below.",
	}, "\n")
}

func (m ChatModel) askHomeAddress(prefill string) ChatModel {
	m.home.step = homeStepAddress
	q := components.NewQuestionModel(homeQAddress,
		"Home Assistant address", nil, true, false).
		WithValue(prefill).
		WithEscHint("esc close")
	m.question = &q
	m.updateViewport()
	return m
}

func (m ChatModel) askHomeToken(prompt string) ChatModel {
	m.home.step = homeStepToken
	q := components.NewQuestionModel(homeQToken, prompt, nil, true,
		true /* sensitive: masked, never echoed */).
		WithEscHint("esc close")
	m.question = &q
	m.updateViewport()
	return m
}

// showHomeConnected shows where it is connected and what can be done.
func (m ChatModel) showHomeConnected(s homeassistant.Status) ChatModel {
	m.home.step = homeStepConnected
	m.home.url = s.URL
	lines := []string{"Home Assistant: connected at " + s.URL}
	if s.Enabled != nil && !*s.Enabled {
		lines = append(lines, "It is turned off. Disconnect and connect again to turn it back on.")
	} else if !s.Active {
		lines = append(lines, "It is not turned on for GAIA. Disconnect and connect again to turn it on.")
	}
	m = m.statusNote(strings.Join(lines, "\n"))
	q := components.NewQuestionModel(homeQConnected, "Home Assistant",
		[]components.QuestionOption{
			{Value: "test", Label: "Test", Description: "Check that Home Assistant still answers"},
			{Value: "disconnect", Label: "Disconnect", Description: "GAIA forgets the address and token"},
			{Value: "close", Label: "Close"},
		}, false, false).WithEscHint("esc close")
	m.question = &q
	m.updateViewport()
	return m
}

// handleHomeAnswer advances the panel. The caller has cleared m.question.
func (m ChatModel) handleHomeAnswer(requestID, value string) (tea.Model, tea.Cmd) {
	if m.home == nil {
		return m, nil
	}
	switch requestID {
	case homeQAddress:
		addr := strings.TrimSpace(value)
		if addr == "" {
			return m.askHomeAddress(homeassistant.DefaultAddress), nil
		}
		m.home.address = addr
		return m.askHomeToken("Long-lived access token (paste it; it stays hidden)"), nil

	case homeQToken:
		token := strings.TrimSpace(value)
		if token == "" {
			return m.askHomeToken("That was empty. Paste the long-lived access token"), nil
		}
		addr := m.home.address
		m.home.step = homeStepConnecting
		return m.statusNote("Connecting to Home Assistant at " + addr + "…"),
			homeCall(func(ctx context.Context, api homeassistant.API) tea.Msg {
				s, err := api.Connect(ctx, addr, token)
				return homeConnectedMsg{status: s, err: err}
			})

	case homeQConnected:
		switch value {
		case "test":
			m.home.step = homeStepTesting
			return m.statusNote("Testing Home Assistant…"),
				homeCall(func(ctx context.Context, api homeassistant.API) tea.Msg {
					r, err := api.Test(ctx)
					return homeTestedMsg{result: r, err: err}
				})
		case "disconnect":
			m.home.step = homeStepDisconnecting
			return m.statusNote("Disconnecting Home Assistant…"),
				homeCall(func(ctx context.Context, api homeassistant.API) tea.Msg {
					s, err := api.Disconnect(ctx)
					return homeDisconnectedMsg{status: s, err: err}
				})
		default:
			m.home = nil
			m.updateViewport()
			return m, nil
		}
	}
	return m, nil
}

func (m ChatModel) handleHomeConnected(msg homeConnectedMsg) (tea.Model, tea.Cmd) {
	if m.home == nil {
		return m, nil
	}
	if msg.err != nil {
		// The sidecar's words are the fix; the typed address comes back.
		m = m.homeError(homeDetail(msg.err))
		return m.askHomeAddress(m.home.address), nil
	}
	detail := msg.status.Detail
	if detail == "" {
		detail = "Connected."
	}
	m = m.statusNote(detail + "\nType /clear to start a new chat that can use it — this chat keeps the tools it had.")
	if msg.status.URL == "" {
		msg.status.URL = m.home.address
	}
	return m.showHomeConnected(msg.status), nil
}

func (m ChatModel) handleHomeTested(msg homeTestedMsg) (tea.Model, tea.Cmd) {
	if m.home == nil {
		return m, nil
	}
	switch {
	case msg.err != nil:
		m = m.homeError("Could not test Home Assistant: " + homeDetail(msg.err))
	case msg.result.OK:
		m = m.statusNote(homeOr(msg.result.Detail, "Home Assistant answered."))
	default:
		m = m.homeError(homeOr(msg.result.Detail, "Home Assistant did not answer."))
	}
	return m.showHomeConnected(homeassistant.Status{Connected: true, Active: true, URL: m.home.url}), nil
}

func (m ChatModel) handleHomeDisconnected(msg homeDisconnectedMsg) (tea.Model, tea.Cmd) {
	if m.home == nil {
		return m, nil
	}
	m.home = nil
	if msg.err != nil {
		return m.homeError("Could not disconnect Home Assistant: " + homeDetail(msg.err)), nil
	}
	return m.statusNote(homeOr(msg.status.Detail, "Home Assistant disconnected.")), nil
}

// closeHome is Esc on one of the panel's questions.
func (m ChatModel) closeHome() (tea.Model, tea.Cmd) {
	saved := m.home != nil && m.home.step == homeStepConnected
	m.home = nil
	m.question = nil
	if saved {
		m.updateViewport()
		return m, nil
	}
	return m.statusNote("Closed — nothing was saved. Type /home to start again."), nil
}

func (m ChatModel) homeError(text string) ChatModel {
	m.messages = append(m.messages, Message{Role: RoleError, Content: sanitizeErrorText(text)})
	m.updateViewport()
	return m
}

// homeDetail is the backend's own words for a refusal, else the error.
func homeDetail(err error) string {
	var se *homeassistant.StatusError
	if errors.As(err, &se) && se.Detail != "" {
		return se.Detail
	}
	return err.Error()
}

func homeOr(s, fallback string) string {
	if strings.TrimSpace(s) == "" {
		return fallback
	}
	return s
}
