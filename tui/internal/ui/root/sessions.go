// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/sessionstore"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/sessions"
)

// WithResume reopens session s at launch, its transcript already on screen.
func (m FlagshipModel) WithResume(s sessionstore.Session, turns []sessionstore.Turn) FlagshipModel {
	m.resumeID = s.ID
	m.pendingTranscript = resumedTranscript(s, turns)
	return m
}

// WithSessionPicker opens the /sessions list as soon as the chat does
// (--resume with no id).
func (m FlagshipModel) WithSessionPicker() FlagshipModel {
	m.pickSessionOnLaunch = true
	return m
}

// WithSessionsDir points the store somewhere else; tests use a temp dir.
func (m FlagshipModel) WithSessionsDir(dir string) FlagshipModel {
	m.sessionsDir = dir
	return m
}

// SessionID is the session the chat on screen writes to; empty before launch.
func (m FlagshipModel) SessionID() string { return m.sessionID }

// keepsSessions reports whether agent writes a session the list can reopen:
// only the flagship the TUI spawns over stdio does.
func keepsSessions(agent catalog.Agent) bool {
	return agent.ID == catalog.FlagshipID && agent.Transport == catalog.TransportSubprocess
}

func (m FlagshipModel) storeDir() (string, error) {
	if m.sessionsDir != "" {
		return m.sessionsDir, nil
	}
	return sessionstore.Dir()
}

// sessionLaunch is the history file and kind the agent is launched with: the
// session being resumed, or a new one. Empty for an agent that keeps none.
func (m *FlagshipModel) sessionLaunch(agent catalog.Agent) (id, file string, err error) {
	if !keepsSessions(agent) {
		return "", "", nil
	}
	dir, err := m.storeDir()
	if err != nil {
		return "", "", err
	}
	id = m.resumeID
	m.resumeID = ""
	if id == "" {
		if id, err = sessionstore.NewID(); err != nil {
			return "", "", err
		}
	}
	return id, sessionstore.HistoryPath(dir, id), nil
}

// launchOrPick is the gate's hand-off: straight into chat or, for a bare
// --resume, the session list first, so the one chat that opens is the one
// picked and its startup work (opening /model turn, warm-up) is never spent on
// a chat that is about to be replaced.
func (m FlagshipModel) launchOrPick(agent catalog.Agent, verified bool) (tea.Model, tea.Cmd) {
	if !m.pickSessionOnLaunch {
		return m.launchAgent(agent, verified)
	}
	m.pickSessionOnLaunch = false
	m.agent = agent
	m.launchVerified = verified
	return m.openSessions(false)
}

// say puts a line in the chat, or holds it for the first chat frame when the
// list was opened before any chat existed.
func (m *FlagshipModel) say(text string) {
	if m.chat != nil {
		m.chat.AppendStatus(text)
		return
	}
	if m.launchNotice != "" {
		text = m.launchNotice + " " + text
	}
	m.launchNotice = text
}

func (m FlagshipModel) openSessions(allKinds bool) (tea.Model, tea.Cmd) {
	if !keepsSessions(m.agent) {
		m.say(noSessionsReason(m.agent))
		return m.leaveSessions()
	}
	dir, err := m.storeDir()
	if err != nil {
		m.say("Cannot list sessions: " + err.Error())
		return m.leaveSessions()
	}
	panel := sessions.New(dir, m.sessionID, allKinds, daemon.FileHeld, m.width, m.height)
	m.sessionsPanel = &panel
	m.activeView = viewSessions
	return m, panel.Init()
}

func noSessionsReason(agent catalog.Agent) string {
	if agent.ID == catalog.FlagshipID {
		return "This GAIA runs through the background service (the Agent Hub or installer build), " +
			"which does not keep sessions to reopen yet — only the GAIA agent the TUI starts itself does."
	}
	return fmt.Sprintf("%s does not keep sessions to reopen — only the built-in GAIA agent does.", agent.Name)
}

// leaveSessions closes the list: back to the chat, or into a new one when the
// list came first (bare --resume) and nothing was picked.
func (m FlagshipModel) leaveSessions() (tea.Model, tea.Cmd) {
	m.sessionsPanel = nil
	if m.chat == nil {
		return m.launchAgent(m.agent, m.launchVerified)
	}
	m.activeView = viewChat
	return m.syncChatSize()
}

// updateSessions drives the open list. Input is the list's alone; everything
// else also reaches the chat behind it, so a running turn, its spinner and its
// startup work carry on while the user looks for a session.
func (m FlagshipModel) updateSessions(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.sessionsPanel == nil {
		return m, nil
	}
	var cmds []tea.Cmd
	switch msg.(type) {
	case tea.KeyMsg, tea.MouseMsg:
	default:
		if m.chat != nil {
			updated, cmd := m.chat.Update(msg)
			c := updated.(chat.ChatModel)
			m.chat = &c
			cmds = append(cmds, cmd)
		}
	}
	updated, cmd := m.sessionsPanel.Update(msg)
	panel := updated.(sessions.Model)
	m.sessionsPanel = &panel
	return m, tea.Batch(append(cmds, cmd)...)
}

// resumeSession replaces the chat on screen with session s: its transcript is
// redrawn and the agent is restarted on its history file, so the model
// remembers it too.
func (m FlagshipModel) resumeSession(s sessionstore.Session) (tea.Model, tea.Cmd) {
	switch {
	case m.chat != nil && s.ID == m.sessionID:
		m.say("That is the session you are in.")
		return m.leaveSessions()
	case s.Live:
		m.say(fmt.Sprintf("%q is open in another GAIA window. Close it there, then reopen it here.", title(s)))
		return m.leaveSessions()
	}
	dir, err := m.storeDir()
	if err != nil {
		m.say("Cannot reopen the session: " + err.Error())
		return m.leaveSessions()
	}
	turns, err := sessionstore.Transcript(dir, s.ID)
	if err != nil {
		m.say("Cannot reopen the session: " + err.Error())
		return m.leaveSessions()
	}
	m.sessionsPanel = nil
	m.resumeID = s.ID
	m.pendingTranscript = resumedTranscript(s, turns)
	// A chat on screen already passed the gate on this machine.
	verified := m.chat != nil || m.launchVerified
	return m.launchAgent(m.agent, verified)
}

func title(s sessionstore.Session) string {
	if s.Title == "" {
		return "untitled session"
	}
	return s.Title
}

// resumedTranscript redraws a session's finished turns, then says where the
// conversation picks up.
func resumedTranscript(s sessionstore.Session, turns []sessionstore.Turn) []chat.Message {
	msgs := make([]chat.Message, 0, 2*len(turns)+1)
	for _, t := range turns {
		msgs = append(msgs, chat.Message{Role: chat.RoleUser, Content: t.User})
		if t.Error != "" {
			msgs = append(msgs, chat.Message{Role: chat.RoleError, Content: t.Error})
		} else {
			msgs = append(msgs, chat.Message{Role: chat.RoleAssistant, Content: t.Answer})
		}
	}
	note := fmt.Sprintf("Reopened %q — GAIA remembers everything above; carry on below.", title(s))
	if s.Interrupted {
		note = fmt.Sprintf("Reopened %q — its last turn stopped before it finished; ask again to retry it.", title(s))
	}
	return append(msgs, chat.Message{Role: chat.RoleStatus, Content: note})
}
