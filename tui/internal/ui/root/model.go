package root

import (
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/hub"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/status"
)

type view int

const (
	viewHub view = iota
	// viewPreflight is the readiness gate every daemon-backed launch passes
	// through before chat opens.
	viewPreflight
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
	dev        bool
	// bypassPermissions starts agents launched from this session with
	// confirmation prompts off (--bypass-permissions). Off unless the launch
	// asked for it.
	bypassPermissions bool

	// preflight is the gate currently on screen, nil when there is none.
	preflight *preflight.Model
	// pending is the agent that gate is guarding — launched only on ProceedMsg.
	pending *catalog.Agent
	// connect is the mailbox hand-off shown over the gate, nil when there is none.
	connect *connectHandoff
	// pfTransport is built on first launch and reused for the session.
	pfTransport preflight.Transport
	pfOpts      preflight.Options

	// halted is every Outcome the active screen is currently holding on.
	// RootModel does not render it or intercept keys for it — the screen
	// that raised it (preflight.Model) already pauses itself and shows its
	// own explanation; this is purely a state flag automation reads via
	// ControlSnapshot's Overlay. Cleared when the gate closes, whether by
	// proceeding or backing out.
	halted []status.Outcome
	// suppressed is every StepID the user has already proceeded past this
	// session — per-process, never persisted, so relaunching the same agent
	// does not report a fresh halt for a row already accepted once. It does
	// NOT change what the screen itself asks for on each launch.
	suppressed map[string]bool
	// listeners decide whether an Outcome halts. The subscribe seam the
	// issue asks for; defaults to one entry with no registration API beyond
	// it.
	listeners []Listener
}

// WithPreflight points the readiness gate at a specific transport and tunes its
// options. Tests use it to drive the gate against a fake daemon; a real session
// leaves it alone and gets the daemon transport.
func (m RootModel) WithPreflight(t preflight.Transport, opts preflight.Options) RootModel {
	m.pfTransport = t
	m.pfOpts = opts
	return m
}

// WithBypassPermissions starts agents launched from this session with
// confirmation prompts off.
//
// A builder rather than a constructor parameter, for the same reason
// WithPreflight is one: the flag is opt-in and rare, and threading it through
// every caller — including a dozen tests that do not care — would make the
// default path noisier than the feature.
func (m RootModel) WithBypassPermissions(enabled bool) RootModel {
	m.bypassPermissions = enabled
	return m
}

func NewRootModel(cat *catalog.Catalog, dev bool) RootModel {
	m := RootModel{
		activeView: viewHub,
		catalog:    cat,
		dev:        dev,
		suppressed: map[string]bool{},
		listeners:  []Listener{haltOnDisposition},
	}
	// One hub client for the session: it caches the daemon instance whose token
	// authorized the last call, and that token rotates on every daemon restart.
	m.hub = hub.NewHubModel(cat, catalog.NewHubClient(m.logf), dev)
	return m
}

// NewRootModelWithHub builds a root model against a specific hub client. Tests
// point it at a fake daemon; a nil client disables install/uninstall, which
// then fail loudly instead of silently doing nothing.
func NewRootModelWithHub(cat *catalog.Catalog, hc *catalog.HubClient, dev bool) RootModel {
	m := RootModel{
		activeView: viewHub,
		catalog:    cat,
		dev:        dev,
		suppressed: map[string]bool{},
		listeners:  []Listener{haltOnDisposition},
	}
	m.hub = hub.NewHubModel(cat, hc, dev)
	return m
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
		case viewPreflight:
			return m.updatePreflight(msg)
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
		return m.beginPreflight(msg.Agent)

	case preflight.ProceedMsg:
		if !m.gateIsFor(msg.AgentID) {
			return m, nil
		}
		return m.proceedFromGate()

	case preflight.CancelMsg:
		if !m.gateIsFor(msg.AgentID) {
			return m, nil
		}
		return m.cancelFromGate()

	case preflight.ConnectMailboxMsg:
		if !m.gateIsFor(msg.AgentID) {
			return m, nil
		}
		return m.openConnectHandoff(msg.Provider)

	case status.Outcome:
		return m.applyOutcome(msg)

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
		// The mailbox hand-off owns every key while it is up, the way the hub's
		// modals do — otherwise esc would cancel the launch behind it.
		if m.activeView == viewPreflight && m.connect != nil {
			return m.handleConnectKey(msg)
		}
	}

	// The hub's async results go to the hub whatever is on screen. They are
	// answers to work it started, and the chat view would just discard them.
	if hub.OwnsMsg(msg) {
		updated, cmd := m.hub.Update(msg)
		m.hub = updated.(hub.HubModel)
		return m, cmd
	}

	// Forward to active sub-model
	switch m.activeView {
	case viewHub:
		updated, cmd := m.hub.Update(msg)
		m.hub = updated.(hub.HubModel)
		return m, cmd
	case viewPreflight:
		// Everything the gate started answers with a message this package cannot
		// name — the probe result, a fix outcome, download progress, the hold
		// tick — so the gate gets the whole default stream, spinner ticks and all.
		return m.updatePreflight(msg)
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
	case viewPreflight:
		switch {
		case m.connect != nil:
			base = m.connect.view(m.width, m.height)
		case m.preflight != nil:
			base = m.preflight.View()
		}
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

// logf writes transport diagnostics to stderr in dev mode. It must never be
// given a daemon token — daemon.Instance redacts its own token when formatted.
func (m RootModel) logf(format string, args ...any) {
	if !m.dev {
		return
	}
	fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
}

func (m RootModel) launchAgent(agent catalog.Agent) (tea.Model, tea.Cmd) {
	// Interactive: this launch opens the chat view, which renders a mid-run
	// question and answers it.
	c, err := client.ForAgent(agent, client.ForAgentOptions{
		Dev: m.dev, Logf: m.logf, Interactive: true,
		BypassPermissions: m.bypassPermissions,
	})
	if err != nil {
		// Stay in the hub and say why, rather than opening a chat that cannot talk.
		m.hub.SetStatus(err.Error())
		return m, nil
	}
	m.chatClient = c

	m.catalog.SetStatus(agent.ID, catalog.StatusActive)

	chatModel := chat.NewChatModelFromHub(c, agent.ID, agent.Name, m.dev)
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

	// Cancel before closing: the chat model owns the per-turn context, so closing
	// the transport without cancelling it can leave a reader streaming into a
	// screen that no longer exists.
	if m.chat != nil {
		m.chat.CancelActiveTurn()
	}
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
