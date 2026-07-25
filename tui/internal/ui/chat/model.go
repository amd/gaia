package chat

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// eventMsg and doneMsg carry the channel they came from. Bubble Tea cannot
// cancel an already-dispatched Cmd, so a cancelled turn's waitForEvent goroutine
// stays parked on its old channel and delivers late — without the tag, that late
// delivery would tear down whatever turn is running by then.
type eventMsg struct {
	ch    <-chan interface{}
	event interface{}
}
type errMsg struct{ err error }
type doneMsg struct{ ch <-chan interface{} }
type sendQueryMsg struct{ query string }
type channelReadyMsg struct{ ch <-chan interface{} }

// ReturnToHubMsg signals the root model to switch back to the hub view.
type ReturnToHubMsg struct{ AgentID string }

// ToggleHelpMsg signals the root model to toggle help overlay.
type ToggleHelpMsg struct{}

var (
	headerStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("150")).
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
	// buffer accumulates streamed answer text. A plain string, not a
	// strings.Builder: Bubble Tea copies the model on every update, and a
	// Builder panics the moment a copied non-zero one is written to again.
	buffer string

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

	// question is the mid-run question the agent is parked on, if any. Non-nil
	// means the run is alive and waiting on THIS client — keystrokes go to it,
	// not to the composer.
	question *components.QuestionModel

	connected    bool
	totalSteps   int
	initialQuery string
	err          error
	queryStart   time.Time // tracks when the current query started
	firstEvent   bool      // whether we've received the first event this turn
	ttft         time.Duration
}

func NewChatModel(c client.AgentClient, agentName string, initialQuery string, debug bool) ChatModel {
	ti := textarea.New()
	ti.Placeholder = "Ask anything... (Enter to send, Ctrl+C to quit)"
	ti.Focus()
	ti.CharLimit = 4096
	ti.SetHeight(1)
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
		if m.supersededTurn(msg.ch) {
			return m, nil
		}
		return m.handleEvent(msg.event)

	case doneMsg:
		if m.supersededTurn(msg.ch) {
			return m, nil
		}
		m.streaming = false
		m.events = nil
		m.cancelFn = nil
		m.question = nil
		m.flushBuffer()
		m.activity = nil
		m.updateViewport()
		return m, nil

	case errMsg:
		m.streaming = false
		m.events = nil
		m.cancelFn = nil
		m.question = nil
		m.err = msg.err
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: msg.err.Error(),
		})
		m.activity = nil
		m.updateViewport()
		return m, nil

	case components.QuestionAnsweredMsg:
		q := m.question
		if q == nil || q.RequestID() != msg.RequestID {
			// A late answer for a question that is no longer up — dropping it is
			// correct, but never silently: the agent moved on.
			return m, nil
		}
		m.messages = append(m.messages, Message{
			Role:    RoleUser,
			Content: q.AnswerLabel(msg.Value),
		})
		m.question = nil
		m.updateViewport()
		return m, m.answerQuestion(msg.RequestID, msg.Value)

	case questionFailedMsg:
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: msg.err.Error(),
		})
		m.updateViewport()
		return m, nil

	case spinner.TickMsg:
		if m.streaming {
			var cmd tea.Cmd
			m.spinner, cmd = m.spinner.Update(msg)
			cmds = append(cmds, cmd)
			// Re-render viewport to update elapsed time display
			m.updateViewport()
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
	// A pending question owns the keyboard: the run is blocked on it, so a
	// keystroke that fell through to the composer would go nowhere. Ctrl+C and
	// Esc still cancel the turn — abandoning a question must stay possible.
	if m.question != nil && msg.Type != tea.KeyCtrlC && msg.Type != tea.KeyEsc {
		q, cmd := m.question.Update(msg)
		m.question = &q
		m.updateViewport()
		return m, cmd
	}

	switch msg.Type {
	case tea.KeyCtrlC:
		if m.streaming && m.cancelFn != nil {
			m.cancelFn()
			m.streaming = false
			m.events = nil
			m.cancelFn = nil
			m.activity = nil
			m.question = nil
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
			m.question = nil
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
		case query == "/clear":
			m.messages = nil
			// Daemon-transport agents are stateless per turn: the host pushes the
			// transcript back as `context`, so clearing the view must clear that
			// too or the "cleared" history keeps being sent.
			if r, ok := m.client.(client.TranscriptResetter); ok {
				r.ResetTranscript()
			}
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
	m.buffer = ""
	m.queryStart = time.Now()
	m.firstEvent = false
	m.ttft = 0
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
			return doneMsg{ch: ch}
		}
		return eventMsg{ch: ch, event: evt}
	}
}

// supersededTurn reports whether a message belongs to a turn that is no longer
// the current one, so it must be ignored rather than allowed to end the live turn.
func (m ChatModel) supersededTurn(ch <-chan interface{}) bool {
	return ch != nil && ch != m.events
}

// CancelActiveTurn stops any in-flight turn. The UI owns the per-turn context, so
// tearing this view down has to cancel it — otherwise the transport keeps
// streaming into a screen nobody is watching and the agent run stays alive.
func (m *ChatModel) CancelActiveTurn() {
	if m.cancelFn != nil {
		m.cancelFn()
		m.cancelFn = nil
	}
	m.streaming = false
	m.events = nil
	m.question = nil
}

func (m ChatModel) handleEvent(evt interface{}) (tea.Model, tea.Cmd) {
	if !m.firstEvent {
		m.firstEvent = true
		m.ttft = time.Since(m.queryStart)
	}

	// The daemon transport speaks the canonical seven-event contract; the
	// subprocess transport speaks the legacy in-process vocabulary below.
	if updated, cmd, handled := m.handleCanonicalEvent(evt); handled {
		return updated, cmd
	}

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
				// Try to extract a clean command from the args JSON
				argStr := extractCommandFromArgs(e.Args)
				if argStr != "" {
					last.Content = e.Tool + ": " + argStr
				}
			}
		}

	case event.ToolResultEvent:
		summary := e.Summary
		if summary == "" {
			summary = e.Title
		}
		// Truncate long summaries (stdout can be very long)
		if len(summary) > 60 {
			summary = summary[:60] + "..."
		}
		// Clean up newlines in summary
		summary = strings.ReplaceAll(summary, "\n", " ")
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
		// Filter out redundant status messages that duplicate thinking/tool events
		msg := e.Message
		if msg == "Thinking" || strings.HasPrefix(msg, "Executing ") {
			// Already shown by ThinkingEvent/ToolStartEvent — skip
		} else if msg != "" {
			m.activity = append(m.activity, ActivityItem{
				Kind:    "status",
				Content: msg,
			})
		}

	case event.AnswerEvent:
		m.flushBuffer()
		duration := time.Since(m.queryStart)
		rendered := components.RenderMarkdown(e.Content)
		m.messages = append(m.messages, Message{
			Role:      RoleAssistant,
			Content:   e.Content,
			Rendered:  rendered,
			Duration:  duration,
			TTFT:      m.ttft,
			Steps:     e.Steps,
			ToolsUsed: e.ToolsUsed,
		})
		m.streaming = false
		m.activity = nil
		m.totalSteps = e.Steps
		m.updateViewport()
		return m, nil

	case event.ChunkEvent:
		m.buffer += e.Content

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
	content := m.buffer
	if content == "" {
		return
	}
	rendered := components.RenderMarkdown(content)
	m.messages = append(m.messages, Message{
		Role:     RoleAssistant,
		Content:  content,
		Rendered: rendered,
	})
	m.buffer = ""
}

func (m *ChatModel) resize() {
	headerH := 1
	statusH := 1
	inputH := 3
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
	if m.question != nil {
		m.question.SetWidth(m.cardWidth())
	}
	m.updateViewport()
}

func (m *ChatModel) updateViewport() {
	var sb strings.Builder

	// Show welcome message if no messages yet
	if len(m.messages) == 0 && !m.streaming {
		sb.WriteString(m.renderWelcome())
		sb.WriteString("\n")
	}

	for i := range m.messages {
		// By index, not by value: rendering a card memoizes onto the message.
		sb.WriteString(m.renderMessage(&m.messages[i]))
		sb.WriteString("\n")
	}

	// The live region appears the moment a turn starts, not once the first tool
	// lands — the silent gap before an agent's first event is exactly when a
	// blank screen reads as a hang.
	if m.streaming {
		sb.WriteString(m.renderLiveRegion())
		sb.WriteString("\n")
	}

	if m.question != nil {
		sb.WriteString(m.question.View())
		sb.WriteString("\n")
	}

	buf := m.buffer
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
		Foreground(lipgloss.Color("150")).
		Render("Welcome to GAIA")

	agent := lipgloss.NewStyle().
		Foreground(lipgloss.Color("252")).
		Render("Connected to: " + m.agentName)

	hint := activityStyle.Render("Type a message and press Enter to start chatting.\nType /help for available commands.")

	return title + "\n" + agent + "\n\n" + hint
}

// cardWidth is the outer width a render card may occupy. The viewport keeps a
// couple of columns for its own gutter, so a card sized to the raw terminal
// width wraps and the borders shear. It never exceeds the viewport itself —
// a card wider than the window it lives in is the same shear by another route.
func (m ChatModel) cardWidth() int {
	w := m.width - 4
	if w > m.viewport.Width && m.viewport.Width > 0 {
		w = m.viewport.Width
	}
	if w < 1 {
		w = 1
	}
	return w
}

func (m ChatModel) renderMessage(msg *Message) string {
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
		panel := answerPanelStyle.Width(panelWidth).Render(content)

		// Perf stats line below the panel
		if msg.Duration > 0 {
			var stats []string
			stats = append(stats, fmt.Sprintf("%.1fs", msg.Duration.Seconds()))
			if msg.TTFT > 0 {
				stats = append(stats, fmt.Sprintf("ttft %.1fs", msg.TTFT.Seconds()))
			}
			// Approximate output tokens (~4 chars per token for English)
			outputTokens := len(msg.Content) / 4
			if outputTokens > 0 {
				stats = append(stats, fmt.Sprintf("~%d tokens", outputTokens))
				// Tokens per second (output only)
				inferTime := msg.Duration - msg.TTFT
				if inferTime > 0 {
					tps := float64(outputTokens) / inferTime.Seconds()
					stats = append(stats, fmt.Sprintf("%.1f tok/s", tps))
				}
			}
			if msg.Steps > 0 {
				stats = append(stats, fmt.Sprintf("%d steps", msg.Steps))
			}
			if msg.ToolsUsed > 0 {
				stats = append(stats, fmt.Sprintf("%d tools", msg.ToolsUsed))
			}
			statsLine := activityStyle.Render("  " + strings.Join(stats, " · "))
			panel += "\n" + statsLine
		}
		return panel

	case RoleCard:
		return msg.renderCard(m.cardWidth())

	case RoleError:
		panelWidth := m.width - 4
		if panelWidth < 20 {
			panelWidth = 20
		}
		return errorPanelStyle.Width(panelWidth).Render("[!] " + msg.Content)

	case RoleStatus:
		return statusMsgStyle.Render("  " + msg.Content)

	default:
		return msg.Content
	}
}

// workLogLines caps the live work log. Bounded so a long turn cannot push the
// transcript off screen, deep enough that repeated tool calls read as progress.
const workLogLines = 5

// stillWorkingAfter is when the live region starts saying the wait is expected.
// A local 4B model routinely takes 60-90s on an inbox triage; without this line
// the user's next move is ctrl+c.
const stillWorkingAfter = 20 * time.Second

// renderLiveRegion draws a bounded work log for the running turn: a header with
// the current step and elapsed time, then the last few activity lines with
// consecutive repeats folded into a counter.
//
// Bounded, not two lines: on a turn touching dozens of messages, two static
// lines are indistinguishable from a hang.
func (m ChatModel) renderLiveRegion() string {
	var lines []string

	elapsed := time.Since(m.queryStart)
	header := "Working"
	for i := len(m.activity) - 1; i >= 0; i-- {
		if m.activity[i].Kind == "step" {
			header = m.activity[i].Content
			break
		}
	}
	lines = append(lines, "  "+stepStyle.Render(m.spinner.View()+" "+header)+"  "+
		activityStyle.Render(formatElapsed(elapsed)))

	log := collapseActivity(m.activity)
	if len(log) > workLogLines {
		log = log[len(log)-workLogLines:]
	}
	for _, item := range log {
		lines = append(lines, m.renderActivityItem(item))
	}
	if len(log) == 0 {
		lines = append(lines, "  "+activityStyle.Render("connecting..."))
	}

	if elapsed >= stillWorkingAfter {
		lines = append(lines, "  "+activityStyle.Render("└ still working — local model, usually 60-90s"))
	}

	return strings.Join(lines, "\n")
}

// collapseActivity drops step markers (the header carries the current one) and
// folds runs of the same tool into "name xN", so a triage that calls one tool
// twenty times shows the repetition instead of flickering on a single line.
func collapseActivity(items []ActivityItem) []ActivityItem {
	var out []ActivityItem
	for _, item := range items {
		if item.Kind == "step" {
			continue
		}
		if n := len(out); n > 0 {
			last := &out[n-1]
			if last.Kind == item.Kind && activityKey(*last) == activityKey(item) {
				last.Repeat++
				last.Done = item.Done
				last.Success = item.Success
				continue
			}
		}
		out = append(out, item)
	}
	return out
}

// activityKey is what "the same activity twice" means: for a tool, the tool name
// without its arguments, so `send_email: a@x` and `send_email: b@y` fold together.
func activityKey(item ActivityItem) string {
	if item.Kind != "tool" {
		return item.Content
	}
	if i := strings.Index(item.Content, ":"); i >= 0 {
		return item.Content[:i]
	}
	return item.Content
}

func formatElapsed(d time.Duration) string {
	total := int(d.Seconds())
	return fmt.Sprintf("%d:%02d", total/60, total%60)
}

// renderActivityItem renders a single work-log line. Markers are ASCII words and
// punctuation, never emoji or colour alone — the state has to survive a terminal
// with no colour and no emoji font.
func (m ChatModel) renderActivityItem(item ActivityItem) string {
	content := item.Content
	if item.Repeat > 0 {
		content += fmt.Sprintf(" x%d", item.Repeat+1)
	}

	switch item.Kind {
	case "thinking":
		if len(content) > 72 {
			content = content[:72] + "..."
		}
		return "       " + thinkingStyle.Render(content)

	case "tool":
		if item.Done {
			if item.Success != nil && !*item.Success {
				return "  " + failStyle.Render("[x] ") + toolNameStyle.Render(content)
			}
			return "  " + successStyle.Render("[ok] ") + toolNameStyle.Render(content)
		}
		return "  " + activityStyle.Render("[..] ") + toolNameStyle.Render(content)

	case "status":
		return "       " + lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Render(content)

	default:
		return "       " + activityStyle.Render(content)
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
		elapsed := time.Since(m.queryStart)
		elapsedStr := fmt.Sprintf("%.0fs", elapsed.Seconds())

		label := "Waiting for agent..."
		if len(m.activity) > 0 {
			last := m.activity[len(m.activity)-1]
			switch last.Kind {
			case "tool":
				parts := strings.SplitN(last.Content, ":", 2)
				label = "Using " + parts[0] + "..."
			case "thinking":
				label = "Thinking..."
			case "step":
				label = last.Content
			case "status":
				label = last.Content
			}
		}
		inputView = m.spinner.View() + " ◆ " + label + "  " + activityStyle.Render(elapsedStr)
	}

	hint := "Ctrl+C quit"
	if m.streaming {
		hint = "Esc cancel"
	} else if m.fromHub {
		hint = "Esc back · Ctrl+C quit"
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

// extractCommandFromArgs tries to extract a clean command string from tool args JSON.
func extractCommandFromArgs(raw json.RawMessage) string {
	var args map[string]interface{}
	if err := json.Unmarshal(raw, &args); err != nil {
		s := string(raw)
		if len(s) > 60 {
			s = s[:60] + "..."
		}
		return s
	}
	// Look for common command fields
	for _, key := range []string{"command", "cmd", "query", "path", "file"} {
		if v, ok := args[key]; ok {
			s := fmt.Sprintf("%v", v)
			if len(s) > 60 {
				s = s[:60] + "..."
			}
			return s
		}
	}
	// Fallback: show first value
	for _, v := range args {
		s := fmt.Sprintf("%v", v)
		if len(s) > 60 {
			s = s[:60] + "..."
		}
		return s
	}
	return ""
}

func (m ChatModel) renderHeader() string {
	title := headerStyle.Render("GAIA")
	name := lipgloss.NewStyle().Foreground(lipgloss.Color("252")).Render(" │ " + m.agentName)
	return title + name
}
