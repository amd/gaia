package chat

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

type eventMsg struct{ event interface{} }
type errMsg struct{ err error }
type doneMsg struct{}
type sendQueryMsg struct{ query string }
type channelReadyMsg struct{ ch <-chan interface{} }

// ReturnToHubMsg signals the root model to switch back to the hub view.
type ReturnToHubMsg struct{ AgentID string }

// ToggleHelpMsg signals the root model to toggle help overlay.
type ToggleHelpMsg struct{}

var (
	headerStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("212")).
			Padding(0, 1)

	userStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("39"))

	assistantStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("252"))

	errorStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("196"))

	activityStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("243"))

	toolNameStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("75"))

	successStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("42"))

	failStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("196"))

	dividerStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("238"))

	thinkingStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("42"))

	stepStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("39"))

	statusMsgStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("243")).
			Italic(true)

	answerPanelStyle = lipgloss.NewStyle().
				Border(lipgloss.RoundedBorder()).
				BorderForeground(lipgloss.Color("42")).
				Padding(0, 1)

	errorPanelStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("196")).
			Padding(0, 1)
)

type ChatModel struct {
	messages  []Message
	activity  []ActivityItem
	streaming bool
	buffer    strings.Builder

	input    textarea.Model
	viewport viewport.Model
	spinner  spinner.Model

	client    client.AgentClient
	events    <-chan interface{}
	cancelFn  context.CancelFunc
	agentName string
	agentID   string
	debug     bool
	fromHub   bool

	width  int
	height int

	connected    bool
	totalSteps   int
	initialQuery string
	err          error
}

func NewChatModel(c client.AgentClient, agentName string, initialQuery string, debug bool) ChatModel {
	ti := textarea.New()
	ti.Placeholder = "Ask anything... (Enter to send, Ctrl+C to quit)"
	ti.Focus()
	ti.CharLimit = 4096
	ti.SetHeight(3)
	ti.ShowLineNumbers = false

	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(lipgloss.Color("205"))

	vp := viewport.New(80, 20)
	vp.SetContent("")

	return ChatModel{
		client:       c,
		agentName:    agentName,
		agentID:      agentName,
		initialQuery: initialQuery,
		debug:        debug,
		input:        ti,
		spinner:      sp,
		viewport:     vp,
		connected:    true,
	}
}

// NewChatModelFromHub creates a ChatModel launched from the hub, enabling Esc-to-return behavior.
func NewChatModelFromHub(c client.AgentClient, agentID, agentName string, debug bool) ChatModel {
	m := NewChatModel(c, agentName, "", debug)
	m.agentID = agentID
	m.fromHub = true
	return m
}

func (m ChatModel) Init() tea.Cmd {
	cmds := []tea.Cmd{
		m.spinner.Tick,
		textarea.Blink,
	}
	if m.initialQuery != "" {
		cmds = append(cmds, func() tea.Msg {
			return sendQueryMsg{query: m.initialQuery}
		})
	}
	return tea.Batch(cmds...)
}

func (m ChatModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.KeyMsg:
		return m.handleKey(msg)

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.resize()
		return m, nil

	case sendQueryMsg:
		return m.sendQuery(msg.query)

	case channelReadyMsg:
		m.events = msg.ch
		return m, waitForEvent(m.events)

	case eventMsg:
		return m.handleEvent(msg.event)

	case doneMsg:
		m.streaming = false
		m.events = nil
		m.cancelFn = nil
		m.flushBuffer()
		m.activity = nil
		m.updateViewport()
		return m, nil

	case errMsg:
		m.streaming = false
		m.events = nil
		m.cancelFn = nil
		m.err = msg.err
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: msg.err.Error(),
		})
		m.activity = nil
		m.updateViewport()
		return m, nil

	case spinner.TickMsg:
		if m.streaming {
			var cmd tea.Cmd
			m.spinner, cmd = m.spinner.Update(msg)
			cmds = append(cmds, cmd)
		}
		return m, tea.Batch(cmds...)
	}

	if !m.streaming {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		cmds = append(cmds, cmd)
	}

	return m, tea.Batch(cmds...)
}

func (m ChatModel) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC:
		if m.streaming && m.cancelFn != nil {
			m.cancelFn()
			m.streaming = false
			m.events = nil
			m.cancelFn = nil
			m.activity = nil
			m.messages = append(m.messages, Message{
				Role:    RoleStatus,
				Content: "cancelled",
			})
			m.updateViewport()
			return m, nil
		}
		return m, tea.Quit

	case tea.KeyEsc:
		if m.streaming && m.cancelFn != nil {
			m.cancelFn()
			m.streaming = false
			m.events = nil
			m.cancelFn = nil
			m.activity = nil
			m.messages = append(m.messages, Message{
				Role:    RoleStatus,
				Content: "cancelled",
			})
			m.updateViewport()
			return m, nil
		}
		if m.fromHub {
			return m, func() tea.Msg {
				return ReturnToHubMsg{AgentID: m.agentID}
			}
		}
		return m, tea.Quit

	case tea.KeyEnter:
		if m.streaming {
			return m, nil
		}
		if msg.Alt {
			return m, nil
		}
		query := strings.TrimSpace(m.input.Value())
		if query == "" {
			return m, nil
		}
		m.input.Reset()

		// Handle slash commands
		switch {
		case query == "/help":
			return m, func() tea.Msg { return ToggleHelpMsg{} }
		case query == "/hub":
			if m.fromHub {
				return m, func() tea.Msg {
					return ReturnToHubMsg{AgentID: m.agentID}
				}
			}
			m.messages = append(m.messages, Message{
				Role:    RoleStatus,
				Content: "Not launched from hub. Use Ctrl+C to quit.",
			})
			m.updateViewport()
			return m, nil
		case query == "/init":
			m.messages = append(m.messages, Message{
				Role:    RoleStatus,
				Content: fmt.Sprintf("Initializing %s...", m.agentName),
			})
			m.updateViewport()
			return m, nil
		case query == "/clear":
			m.messages = nil
			m.updateViewport()
			return m, nil
		}

		return m.sendQuery(query)

	case tea.KeyPgUp:
		m.viewport.HalfViewUp()
		return m, nil

	case tea.KeyPgDown:
		m.viewport.HalfViewDown()
		return m, nil
	}

	if !m.streaming {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}

	return m, nil
}

func (m ChatModel) sendQuery(query string) (tea.Model, tea.Cmd) {
	m.messages = append(m.messages, Message{
		Role:    RoleUser,
		Content: query,
	})
	m.streaming = true
	m.activity = nil
	m.buffer.Reset()
	m.updateViewport()

	ctx, cancel := context.WithCancel(context.Background())
	m.cancelFn = cancel

	c := m.client
	return m, tea.Batch(
		m.spinner.Tick,
		func() tea.Msg {
			ch, err := c.Send(ctx, query)
			if err != nil {
				return errMsg{err: err}
			}
			return channelReadyMsg{ch: ch}
		},
	)
}

func waitForEvent(ch <-chan interface{}) tea.Cmd {
	return func() tea.Msg {
		if ch == nil {
			return doneMsg{}
		}
		evt, ok := <-ch
		if !ok {
			return doneMsg{}
		}
		return eventMsg{event: evt}
	}
}

func (m ChatModel) handleEvent(evt interface{}) (tea.Model, tea.Cmd) {
	switch e := evt.(type) {
	case event.ThinkingEvent:
		m.activity = append(m.activity, ActivityItem{
			Kind:    "thinking",
			Content: e.Content,
		})

	case event.ToolStartEvent:
		m.activity = append(m.activity, ActivityItem{
			Kind:    "tool",
			Content: e.Tool,
		})

	case event.ToolArgsEvent:
		if len(m.activity) > 0 {
			last := &m.activity[len(m.activity)-1]
			if last.Kind == "tool" {
				args := string(e.Args)
				if len(args) > 80 {
					args = args[:80] + "..."
				}
				last.Content = e.Tool + ": " + args
			}
		}

	case event.ToolResultEvent:
		summary := e.Summary
		if summary == "" {
			summary = e.Title
		}
		if len(m.activity) > 0 {
			last := &m.activity[len(m.activity)-1]
			if last.Kind == "tool" {
				last.Done = true
				last.Success = &e.Success
				if summary != "" {
					last.Content += " → " + summary
				}
			}
		}

	case event.ToolEndEvent:
		if len(m.activity) > 0 {
			last := &m.activity[len(m.activity)-1]
			if last.Kind == "tool" && !last.Done {
				last.Done = true
				last.Success = &e.Success
			}
		}

	case event.StepEvent:
		m.totalSteps = e.Step
		m.activity = append(m.activity, ActivityItem{
			Kind:    "step",
			Content: fmt.Sprintf("Step %d/%d", e.Step, e.Total),
		})

	case event.StatusEvent:
		if e.Status == "complete" {
			m.flushBuffer()
			m.streaming = false
			m.activity = nil
			m.updateViewport()
			return m, nil
		}
		m.activity = append(m.activity, ActivityItem{
			Kind:    "status",
			Content: e.Message,
		})

	case event.AnswerEvent:
		m.flushBuffer()
		rendered := components.RenderMarkdown(e.Content)
		m.messages = append(m.messages, Message{
			Role:     RoleAssistant,
			Content:  e.Content,
			Rendered: rendered,
		})
		m.streaming = false
		m.activity = nil
		m.totalSteps = e.Steps
		m.updateViewport()
		return m, nil

	case event.ChunkEvent:
		m.buffer.WriteString(e.Content)

	case event.AgentErrorEvent:
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: e.Content,
		})
		m.streaming = false
		m.activity = nil
		m.updateViewport()
		return m, nil

	case event.ErrorEvent:
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: e.Content,
		})
		m.streaming = false
		m.activity = nil
		m.updateViewport()
		return m, nil

	case event.DoneEvent:
		m.flushBuffer()
		m.streaming = false
		m.activity = nil
		m.updateViewport()
		return m, nil
	}

	m.updateViewport()
	return m, waitForEvent(m.events)
}

func (m *ChatModel) flushBuffer() {
	content := m.buffer.String()
	if content == "" {
		return
	}
	rendered := components.RenderMarkdown(content)
	m.messages = append(m.messages, Message{
		Role:     RoleAssistant,
		Content:  content,
		Rendered: rendered,
	})
	m.buffer.Reset()
}

func (m *ChatModel) resize() {
	headerH := 1
	statusH := 1
	inputH := 5
	padding := 2

	vpHeight := m.height - headerH - statusH - inputH - padding
	if vpHeight < 1 {
		vpHeight = 1
	}
	vpWidth := m.width
	if vpWidth < 10 {
		vpWidth = 10
	}

	m.viewport.Width = vpWidth
	m.viewport.Height = vpHeight
	m.input.SetWidth(vpWidth - 2)

	components.SetWordWrap(vpWidth - 4)
	m.updateViewport()
}

func (m *ChatModel) updateViewport() {
	var sb strings.Builder

	// Show welcome message if no messages yet
	if len(m.messages) == 0 && !m.streaming {
		sb.WriteString(m.renderWelcome())
		sb.WriteString("\n")
	}

	for _, msg := range m.messages {
		sb.WriteString(m.renderMessage(msg))
		sb.WriteString("\n")
	}

	// Live region: only the latest activity item is rendered (overwriting pattern)
	if m.streaming && len(m.activity) > 0 {
		last := m.activity[len(m.activity)-1]
		sb.WriteString(m.renderLiveActivity(last))
		sb.WriteString("\n")
	}

	buf := m.buffer.String()
	if m.streaming && buf != "" {
		sb.WriteString(assistantStyle.Render(buf))
		sb.WriteString("\n")
	}

	m.viewport.SetContent(sb.String())
	m.viewport.GotoBottom()
}

func (m ChatModel) renderWelcome() string {
	title := lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("212")).
		Render("Welcome to GAIA")

	agent := lipgloss.NewStyle().
		Foreground(lipgloss.Color("252")).
		Render("Connected to: " + m.agentName)

	hint := activityStyle.Render("Type a message and press Enter to start chatting.\nType /help for available commands.")

	return title + "\n" + agent + "\n\n" + hint
}

func (m ChatModel) renderMessage(msg Message) string {
	switch msg.Role {
	case RoleUser:
		return userStyle.Render("▶ You: ") + msg.Content

	case RoleAssistant:
		content := msg.Content
		if msg.Rendered != "" {
			content = msg.Rendered
		}
		panelWidth := m.width - 4
		if panelWidth < 20 {
			panelWidth = 20
		}
		return answerPanelStyle.Width(panelWidth).Render(content)

	case RoleError:
		panelWidth := m.width - 4
		if panelWidth < 20 {
			panelWidth = 20
		}
		return errorPanelStyle.Width(panelWidth).Render("⚠️  " + msg.Content)

	case RoleStatus:
		return statusMsgStyle.Render("  " + msg.Content)

	default:
		return msg.Content
	}
}

// renderLiveActivity renders the current live activity indicator (overwriting previous).
func (m ChatModel) renderLiveActivity(item ActivityItem) string {
	switch item.Kind {
	case "thinking":
		content := item.Content
		if len(content) > 60 {
			content = content[:60] + "..."
		}
		return "  " + thinkingStyle.Render("🧠 ") + activityStyle.Render(content)

	case "tool":
		if item.Done {
			if item.Success != nil && *item.Success {
				return "  " + successStyle.Render("✓ ") + toolNameStyle.Render(item.Content)
			} else if item.Success != nil {
				return "  " + failStyle.Render("✗ ") + toolNameStyle.Render(item.Content)
			}
		}
		return "  " + toolNameStyle.Render("🔧 "+item.Content)

	case "step":
		return "  " + stepStyle.Render("📝 "+item.Content)

	case "status":
		return "  " + activityStyle.Render("— "+item.Content)

	default:
		return "  " + activityStyle.Render(item.Content)
	}
}

func (m ChatModel) View() string {
	if m.width == 0 {
		return m.renderWelcome()
	}

	header := m.renderHeader()
	divider := dividerStyle.Render(strings.Repeat("─", m.width))
	vpView := m.viewport.View()

	inputView := m.input.View()
	if m.streaming {
		label := "Thinking..."
		if len(m.activity) > 0 {
			last := m.activity[len(m.activity)-1]
			switch last.Kind {
			case "tool":
				parts := strings.SplitN(last.Content, ":", 2)
				label = "Using " + parts[0] + "..."
			case "thinking":
				label = "Thinking..."
			case "step":
				label = last.Content + "..."
			}
		}
		inputView = m.spinner.View() + " ◆ " + label
	}

	hint := "Ctrl+C to quit"
	if m.streaming {
		hint = "Esc to cancel"
	} else if m.fromHub {
		hint = "Esc to return · Ctrl+C to quit"
	}

	statusBar := components.RenderStatusBar(components.StatusBarState{
		AgentName: m.agentName,
		Connected: m.connected,
		Steps:     m.totalSteps,
		Streaming: m.streaming,
		Hint:      hint,
	}, m.width)

	return lipgloss.JoinVertical(lipgloss.Left,
		header,
		divider,
		vpView,
		divider,
		inputView,
		statusBar,
	)
}

func (m ChatModel) renderHeader() string {
	title := headerStyle.Render("GAIA")
	name := lipgloss.NewStyle().Foreground(lipgloss.Color("252")).Render(" │ " + m.agentName)
	return title + name
}
