package root

import (
	"fmt"
	"os"
	"sync"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/gateway"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/status"
)

type view int

const (
	// viewSplash is the first frame: GAIA's mascot while the readiness gate
	// spins up behind it. It exists so the launch never opens on a blank
	// terminal, and it is what makes the boot feel like starting a product
	// rather than waiting on a probe.
	viewSplash view = iota
	// viewPreflight is the readiness gate every launch passes through before
	// chat opens.
	viewPreflight
	viewChat
	// viewGateway connects GAIA to the AMD LLM gateway (Lemonade cloud offload).
	viewGateway
)

// FlagshipModel is the whole TUI: splash, readiness, chat, for exactly one
// agent. GAIA ships one, so there is nothing to browse and nothing to pick —
// the launch goes straight at it.
type FlagshipModel struct {
	activeView view
	agent      catalog.Agent
	chat       *chat.ChatModel
	// chatClient is held behind a pointer shared by every copy Bubble Tea
	// makes, so whoever tears the program down can close the child this model
	// opened. Stored by value it would live on a copy that dies with the
	// Update call, and the agent process would outlive the TUI — on Windows
	// nothing reaps it, so gaia-agent stays running after the terminal is gone.
	chatClient *clientBox
	// help is the shared overlay state machine (components.HelpState) — the
	// same one the chat view uses on a direct launch, so open/scroll/dismiss
	// behavior can never diverge between the two paths.
	help   components.HelpState
	width  int
	height int
	dev    bool
	// bypassPermissions starts the agent with confirmation prompts off
	// (--bypass-permissions). Off unless the launch asked for it.
	bypassPermissions bool
	// useClaude starts the agent against Anthropic's Claude API instead of the
	// local Lemonade backend (--use-claude). claudeModel optionally picks the
	// Claude model.
	useClaude   bool
	claudeModel string
	// model overrides the agent's own default (--model). Only a daemon-backed
	// agent can honour it; cli.checkModelSupported refuses it for the rest.
	model string

	// gw is the AMD LLM gateway screen, nil until the user opens it.
	gw *gateway.GatewayModel

	// preflight is the gate currently on screen, nil when there is none.
	preflight *preflight.Model
	// pending is the agent that gate is guarding — launched only on ProceedMsg.
	pending *catalog.Agent
	// connect is the mailbox hand-off shown over the gate, nil when there is none.
	connect *connectHandoff
	// pfTransport is built on first launch and reused for the session.
	pfTransport preflight.Transport
	pfOpts      preflight.Options
	// pfLocal overrides the local runner's options. Tests point it at a
	// different binary name; a real session leaves it alone.
	pfLocal *preflight.LocalOptions

	// halted is every Outcome the active screen is currently holding on.
	// FlagshipModel does not render it or intercept keys for it — the screen
	// that raised it (preflight.Model) already pauses itself and shows its own
	// explanation; this is purely a state flag automation reads via
	// ControlSnapshot's Overlay. Cleared when the gate closes, whether by
	// proceeding or backing out.
	halted []status.Outcome
	// suppressed is every StepID the user has already proceeded past this
	// session — per-process, never persisted.
	suppressed map[string]bool
	// listeners decide whether an Outcome halts.
	listeners []Listener
	// beginPending is set when the launch was asked to start before the
	// terminal size was known. See beginMsg.
	beginPending bool
}

// clientBox owns the agent client across the copies Bubble Tea makes of the
// model. See FlagshipModel.chatClient.
type clientBox struct {
	mu sync.Mutex
	c  client.AgentClient
}

func (b *clientBox) set(c client.AgentClient) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.c = c
}

func (b *clientBox) close() {
	b.mu.Lock()
	c := b.c
	b.c = nil
	b.mu.Unlock()
	if c != nil {
		c.Close()
	}
}

// NewFlagshipModel builds the TUI around one agent.
func NewFlagshipModel(agent catalog.Agent, dev bool) FlagshipModel {
	return FlagshipModel{
		activeView: viewSplash,
		agent:      agent,
		chatClient: &clientBox{},
		dev:        dev,
		suppressed: map[string]bool{},
		listeners:  []Listener{haltOnDisposition},
	}
}

// Close releases everything the session opened — the agent child in
// particular. The host calls it once the event loop has stopped.
//
// Cancel before close: the chat model owns the per-turn context, so closing the
// transport without cancelling it can leave a reader streaming into a screen
// that no longer exists.
func (m FlagshipModel) Close() error {
	if m.chat != nil {
		m.chat.CancelActiveTurn()
	}
	if m.chatClient != nil {
		m.chatClient.close()
	}
	return nil
}

// WithPreflight points the readiness gate at a specific transport and tunes its
// options. Tests use it to drive the gate against a fake daemon; a real session
// leaves it alone and gets the daemon transport.
func (m FlagshipModel) WithPreflight(t preflight.Transport, opts preflight.Options) FlagshipModel {
	m.pfTransport = t
	m.pfOpts = opts
	return m
}

// WithLocalPreflight overrides what the local runner looks for. Tests point it
// at a mock binary so the gate answers about the thing the launch will spawn.
func (m FlagshipModel) WithLocalPreflight(opts preflight.LocalOptions) FlagshipModel {
	m.pfLocal = &opts
	return m
}

// WithBypassPermissions starts the agent with confirmation prompts off.
//
// A builder rather than a constructor parameter, for the same reason
// WithPreflight is one: the flag is opt-in and rare, and threading it through
// every caller — including a dozen tests that do not care — would make the
// default path noisier than the feature.
func (m FlagshipModel) WithBypassPermissions(enabled bool) FlagshipModel {
	m.bypassPermissions = enabled
	return m
}

// WithClaude starts the agent against Anthropic's Claude API instead of the
// local Lemonade backend.
func (m FlagshipModel) WithClaude(enabled bool, model string) FlagshipModel {
	m.useClaude = enabled
	m.claudeModel = model
	return m
}

// WithModel overrides the agent's own default model (--model).
//
// It has to be threaded all the way here because the router builds the client
// AFTER the gate passes: dropping it made `gaia tui run email --model X` accept
// the flag and quietly run the default instead, which is the silent-fallback
// shape the rules forbid.
func (m FlagshipModel) WithModel(model string) FlagshipModel {
	m.model = model
	return m
}

// Init opens the gate immediately. The splash is what is on screen while the
// first probe runs, not a screen the user has to dismiss.
func (m FlagshipModel) Init() tea.Cmd { return beginCmd() }

func beginCmd() tea.Cmd { return func() tea.Msg { return beginMsg{} } }

// beginMsg leaves the splash and starts the readiness gate. A message rather
// than a direct call so the splash gets at least one rendered frame — Init's
// command runs after the first View.
//
// It waits for the terminal size before acting. Bubble Tea's first View happens
// before the first WindowSizeMsg, so acting immediately would race: the splash
// would render once at an unknown size — where the banner is deliberately
// compact, since an over-tall frame there scrolls the terminal and misaligns
// every frame after it — and the gate would replace it before the real size
// ever arrived. Whether the mascot appeared at all came down to which message
// won. Now it always does, at the size it was measured for.
type beginMsg struct{}

func (m FlagshipModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		if m.beginPending {
			// Re-emit rather than starting here: Bubble Tea renders between
			// messages, so bouncing through the queue is what gives the splash
			// its one frame at the real size. Starting inline would set the
			// size and switch the view in the same update, and the mascot would
			// never be drawn at all.
			m.beginPending = false
			return m, beginCmd()
		}
		switch m.activeView {
		case viewPreflight:
			return m.updatePreflight(msg)
		case viewChat:
			if m.chat != nil {
				updated, cmd := m.chat.Update(msg)
				chatModel := updated.(chat.ChatModel)
				m.chat = &chatModel
				return m, cmd
			}
		case viewGateway:
			return m.updateGateway(msg)
		}
		return m, nil

	case beginMsg:
		if m.width == 0 || m.height == 0 {
			// No size yet — the WindowSizeMsg branch starts the gate instead.
			m.beginPending = true
			return m, nil
		}
		return m.beginPreflight(m.agent)

	case chat.OpenGatewayMsg:
		return m.openGateway()

	case gateway.CloseMsg:
		m.gw = nil
		m.activeView = viewChat
		return m, nil

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

	case chat.ToggleHelpMsg:
		m.help.Toggle(components.HelpContextChat)
		return m, nil

	case components.HelpContext:
		m.help.Toggle(msg)
		return m, nil

	case tea.KeyMsg:
		if m.help.Open {
			// Navigation keys scroll the open panel; anything else dismisses
			// it — HelpState owns that vocabulary for every view.
			m.help.HandleKey(msg, m.width, m.height)
			return m, nil
		}
		// The mailbox hand-off owns every key while it is up — otherwise esc
		// would cancel the launch behind it.
		if m.activeView == viewPreflight && m.connect != nil {
			return m.handleConnectKey(msg)
		}
		// Nothing on the splash is interactive except the way out, and a user
		// who presses ctrl+c there means it.
		if m.activeView == viewSplash && msg.String() == "ctrl+c" {
			return m, tea.Quit
		}
	}

	switch m.activeView {
	case viewPreflight:
		// Everything the gate started answers with a message this package cannot
		// name — the probe result, a fix outcome, setup progress, the hold tick —
		// so the gate gets the whole default stream, spinner ticks and all.
		return m.updatePreflight(msg)
	case viewChat:
		if m.chat != nil {
			updated, cmd := m.chat.Update(msg)
			chatModel := updated.(chat.ChatModel)
			m.chat = &chatModel
			return m, cmd
		}
	case viewGateway:
		// Everything the screen started answers with a message this package
		// cannot name (probe, install, auth, model list, cursor blink), so it
		// gets the whole default stream.
		return m.updateGateway(msg)
	}

	return m, nil
}

// openGateway switches to the AMD LLM gateway screen. A Lemonade that cannot
// be reached is passed into the screen rather than swallowed here, so the user
// sees why on the screen they asked for.
func (m FlagshipModel) openGateway() (tea.Model, tea.Cmd) {
	c, err := gateway.NewClient()
	gw := gateway.New(c, err)
	m.gw = &gw
	m.activeView = viewGateway

	cmds := []tea.Cmd{gw.Init()}
	if m.width > 0 && m.height > 0 {
		updated, cmd := gw.Update(tea.WindowSizeMsg{Width: m.width, Height: m.height})
		sized := updated.(gateway.GatewayModel)
		m.gw = &sized
		cmds = append(cmds, cmd)
	}
	return m, tea.Batch(cmds...)
}

func (m FlagshipModel) updateGateway(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.gw == nil {
		return m, nil
	}
	updated, cmd := m.gw.Update(msg)
	gw := updated.(gateway.GatewayModel)
	m.gw = &gw
	return m, cmd
}

func (m FlagshipModel) View() string {
	var base string
	switch m.activeView {
	case viewSplash:
		base = m.renderSplash()
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
	case viewGateway:
		if m.gw != nil {
			base = m.gw.View()
		}
	}

	if m.help.Open {
		return m.help.Render(base, m.width, m.height)
	}

	return base
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// logf writes transport diagnostics to stderr in dev mode. It must never be
// given a daemon token — daemon.Instance redacts its own token when formatted.
func (m FlagshipModel) logf(format string, args ...any) {
	if !m.dev {
		return
	}
	fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
}

func (m FlagshipModel) launchAgent(agent catalog.Agent, setupVerified bool) (tea.Model, tea.Cmd) {
	// Interactive: this launch opens the chat view, which renders a mid-run
	// question and answers it.
	c, err := client.ForAgent(agent, client.ForAgentOptions{
		Dev: m.dev, Logf: m.logf, Interactive: true,
		Model:             m.model,
		BypassPermissions: m.bypassPermissions,
		UseClaude:         m.useClaude,
		ClaudeModel:       m.claudeModel,
	})
	if err != nil {
		// Nothing to fall back to, so this is the gate's problem: re-raise it as
		// a blocked report rather than open a chat that cannot talk.
		return m.haltOnLaunchFailure(agent, err)
	}
	m.chatClient.set(c)

	chatModel := chat.NewChatModelForFlagship(c, agent.ID, agent.Name, m.dev, setupVerified)
	m.chat = &chatModel
	m.activeView = viewChat

	var cmds []tea.Cmd
	cmds = append(cmds, m.chat.Init())
	if m.width > 0 && m.height > 0 {
		cmds = append(cmds, m.sizeCmd())
	}

	return m, tea.Batch(cmds...)
}
