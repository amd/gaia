package root

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/hub"
)

type view int

const (
	viewHub  view = iota
	viewChat
)

type RootModel struct {
	activeView view
	hub        hub.HubModel
	chat       *chat.ChatModel
	chatClient client.AgentClient
	catalog    *catalog.Catalog
	showHelp   bool
	helpCtx    components.HelpContext
	width      int
	height     int
	debug      bool
}

func NewRootModel(cat *catalog.Catalog, debug bool) RootModel {
	return RootModel{
		activeView: viewHub,
		hub:        hub.NewHubModel(cat, debug),
		catalog:    cat,
		debug:      debug,
	}
}

func (m RootModel) Init() tea.Cmd {
	return m.hub.Init()
}

func (m RootModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		// Forward to active sub-model
		switch m.activeView {
		case viewHub:
			updated, cmd := m.hub.Update(msg)
			m.hub = updated.(hub.HubModel)
			return m, cmd
		case viewChat:
			if m.chat != nil {
				updated, cmd := m.chat.Update(msg)
				chatModel := updated.(chat.ChatModel)
				m.chat = &chatModel
				return m, cmd
			}
		}
		return m, nil

	case hub.LaunchAgentMsg:
		return m.launchAgent(msg.Agent)

	case chat.ReturnToHubMsg:
		return m.returnToHub(msg.AgentID)

	case chat.ToggleHelpMsg:
		m.showHelp = !m.showHelp
		m.helpCtx = components.HelpContextChat
		return m, nil

	case components.HelpContext:
		m.showHelp = !m.showHelp
		m.helpCtx = msg
		return m, nil

	case tea.KeyMsg:
		if m.showHelp {
			// Any key dismisses help overlay
			m.showHelp = false
			return m, nil
		}
	}

	// Forward to active sub-model
	switch m.activeView {
	case viewHub:
		updated, cmd := m.hub.Update(msg)
		m.hub = updated.(hub.HubModel)
		return m, cmd
	case viewChat:
		if m.chat != nil {
			updated, cmd := m.chat.Update(msg)
			chatModel := updated.(chat.ChatModel)
			m.chat = &chatModel
			return m, cmd
		}
	}

	return m, nil
}

func (m RootModel) View() string {
	var base string
	switch m.activeView {
	case viewHub:
		base = m.hub.View()
	case viewChat:
		if m.chat != nil {
			base = m.chat.View()
		}
	}

	if m.showHelp {
		return components.RenderHelpOverlay(m.helpCtx, base, m.width, m.height)
	}

	return base
}

func (m RootModel) launchAgent(agent catalog.Agent) (tea.Model, tea.Cmd) {
	cmdLine := agent.BinaryPath
	if len(agent.BinaryArgs) > 0 {
		cmdLine += " " + strings.Join(agent.BinaryArgs, " ")
	}

	c := client.NewSubprocessClient(cmdLine, m.debug)
	m.chatClient = c

	m.catalog.SetStatus(agent.ID, catalog.StatusActive)

	chatModel := chat.NewChatModelFromHub(c, agent.ID, agent.Name, m.debug)
	m.chat = &chatModel
	m.activeView = viewChat

	// Forward initial window size + init the chat model
	var cmds []tea.Cmd
	cmds = append(cmds, m.chat.Init())
	if m.width > 0 && m.height > 0 {
		cmds = append(cmds, func() tea.Msg {
			return tea.WindowSizeMsg{Width: m.width, Height: m.height}
		})
	}

	return m, tea.Batch(cmds...)
}

func (m RootModel) returnToHub(agentID string) (tea.Model, tea.Cmd) {
	m.catalog.SetStatus(agentID, catalog.StatusIdle)

	if m.chatClient != nil {
		m.chatClient.Close()
		m.chatClient = nil
	}
	m.chat = nil
	m.activeView = viewHub

	// Re-send window size to hub
	var cmds []tea.Cmd
	if m.width > 0 && m.height > 0 {
		cmds = append(cmds, func() tea.Msg {
			return tea.WindowSizeMsg{Width: m.width, Height: m.height}
		})
	}

	return m, tea.Batch(cmds...)
}
