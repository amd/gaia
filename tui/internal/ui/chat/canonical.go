package chat

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// answerTimeout bounds the out-of-band POST that delivers an answer. Short: the
// daemon is local, and a hung answer must not look like a hung agent.
const answerTimeout = 15 * time.Second

// questionFailedMsg reports that an answer never reached the agent.
type questionFailedMsg struct{ err error }

// handleCanonicalEvent renders the canonical `/query` SSE vocabulary — what the
// daemon transport streams. handled is false for anything else, so the legacy
// in-process types (used by the subprocess transport) fall through untouched.
func (m ChatModel) handleCanonicalEvent(evt interface{}) (ChatModel, tea.Cmd, bool) {
	switch e := evt.(type) {
	case event.CanonicalStatusEvent:
		// One live line, replaced — not a log. A user watching a 200s turn needs
		// to know what is happening NOW; an accumulating list of "Step 2/50"
		// and "Thinking" answers a question nobody asked and buries the tool
		// call that actually says what the agent is doing.
		if msg := userFacingStatus(e.Message); msg != "" {
			m.setLiveStatus(msg)
		}

	case event.CanonicalTokenEvent:
		m.buffer += e.Delta

	case event.CanonicalToolCallEvent:
		label := e.Tool
		if arg := extractCommandFromArgs(e.Args); arg != "" {
			label += ": " + arg
		}
		m.activity = append(m.activity, ActivityItem{Kind: "tool", Content: label})

	case event.CanonicalToolResultEvent:
		m.markToolDone(e)
		if e.Render != "" {
			// The sidecar declared a card, so the card is the result. The email
			// agent's pre-scan tool docstring tells the model NOT to describe the
			// results in prose precisely because the client is expected to draw
			// this — ignore `render` and the turn produces one vague sentence.
			m.messages = append(m.messages, Message{
				Role:     RoleCard,
				ToolName: e.Tool,
				Render:   e.Render,
				Data:     e.Data,
			})
		}

	case event.CanonicalNeedsInputEvent:
		// The run is parked waiting for this answer, on the stream we are still
		// reading. Put the question up and keep reading — the answer goes back
		// out of band (see answerQuestion) and the same stream resumes.
		m.question = questionFromEvent(e)
		m.question.SetWidth(m.cardWidth())
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "the agent needs an answer to continue",
		})

	case event.CanonicalNeedsConfirmationEvent:
		// The approval UI is a later phase. Until then the pause is surfaced as a
		// message the user can actually read — never swallowed — and the run
		// continues to its own terminal event.
		line := "confirmation needed: " + e.Action
		if summary := strings.TrimSpace(e.Summary); summary != "" {
			line += " — " + summary
		}
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: "[!] " + line})

	case event.CanonicalFinalEvent:
		usage := event.CanonicalUsageOf(e)
		// `answer` is the contract's authoritative field (§4), so it wins over the
		// streamed tokens rather than the other way round — otherwise the view and
		// the transcript pushed back as `context` could disagree. The buffered
		// tokens are the fallback for a sidecar that streams and then sends an
		// empty `final`. Either way the text is replaced, never printed twice.
		content := e.Answer
		if content == "" {
			content = m.buffer
		}
		m.buffer = ""
		m.messages = append(m.messages, Message{
			Role:      RoleAssistant,
			Content:   content,
			Rendered:  components.RenderMarkdown(content),
			Duration:  time.Since(m.queryStart),
			TTFT:      m.ttft,
			Steps:     usage.Steps,
			ToolsUsed: usage.ToolsUsed,
		})
		m.streaming = false
		m.activity = nil
		// The turn is over, so any question it was waiting on is dead. Leaving
		// the panel up would swallow every keystroke into a question nobody is
		// listening to — the composer becomes unreachable and Esc quits the app.
		m.question = nil
		if usage.Steps > 0 {
			m.totalSteps = usage.Steps
		}
		m.updateViewport()
		return m, nil, true

	case event.CanonicalErrorEvent:
		m.flushBuffer()
		m.messages = append(m.messages, Message{Role: RoleError, Content: e.Detail})
		m.streaming = false
		m.activity = nil
		m.question = nil
		m.updateViewport()
		return m, nil, true

	case event.CanonicalUnsupportedEvent:
		// Contract §7: a newer agent's event type is shown, never dropped.
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: fmt.Sprintf("unsupported event %q from the agent (update GAIA to render it)", e.EventType),
		})

	case event.CanonicalNoticeEvent:
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: e.Text})

	case event.CanonicalMalformedEvent:
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "unreadable agent event skipped: " + e.Reason,
		})

	default:
		return m, nil, false
	}

	m.updateViewport()
	return m, waitForEvent(m.events), true
}

// questionFromEvent builds the picker from the wire event.
func questionFromEvent(e event.CanonicalNeedsInputEvent) *components.QuestionModel {
	opts := make([]components.QuestionOption, 0, len(e.Options))
	for _, o := range e.Options {
		label := o.Label
		if label == "" {
			label = o.Value
		}
		opts = append(opts, components.QuestionOption{
			Value:       o.Value,
			Label:       label,
			Description: o.Description,
		})
	}
	question := strings.TrimSpace(e.Question)
	if question == "" {
		question = "The agent needs an answer to continue."
	}
	q := components.NewQuestionModel(e.RequestID, question, opts, e.AllowFreeText, e.Sensitive)
	return &q
}

// answerQuestion delivers the answer on the transport's out-of-band seam.
//
// A transport with no Respond is a real dead end for the user — the agent is
// waiting on something this client structurally cannot send — so it says so
// rather than dropping the keystroke.
func (m ChatModel) answerQuestion(requestID, value string) tea.Cmd {
	responder, ok := m.client.(client.AgentResponder)
	if !ok {
		return func() tea.Msg {
			return questionFailedMsg{err: fmt.Errorf(
				"this agent connection cannot answer questions mid-run; " +
					"relaunch the agent through the GAIA daemon transport")}
		}
	}
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), answerTimeout)
		defer cancel()
		if err := responder.Respond(ctx, requestID, value); err != nil {
			return questionFailedMsg{err: err}
		}
		return nil
	}
}

// markToolDone closes out the activity line opened by the matching tool_call.
//
// The canonical tool_result carries no success flag, so the agent's own
// {"ok": bool} / {"success": bool} convention is read out of `data` when present;
// absent that, a delivered result counts as completed. Per-tool detail belongs
// to the render card drawn in the transcript, not to this one-line summary.
func (m *ChatModel) markToolDone(e event.CanonicalToolResultEvent) {
	success := toolResultSucceeded(e.Data)

	for i := len(m.activity) - 1; i >= 0; i-- {
		item := &m.activity[i]
		if item.Kind != "tool" || item.Done {
			continue
		}
		item.Done = true
		item.Success = &success
		return
	}

	// A result with no matching call still has to be visible.
	m.activity = append(m.activity, ActivityItem{
		Kind:    "tool",
		Content: e.Tool,
		Done:    true,
		Success: &success,
	})
}

func toolResultSucceeded(data json.RawMessage) bool {
	if len(data) == 0 {
		return true
	}
	var probe struct {
		OK      *bool `json:"ok"`
		Success *bool `json:"success"`
	}
	if err := json.Unmarshal(data, &probe); err != nil {
		return true
	}
	if probe.OK != nil {
		return *probe.OK
	}
	if probe.Success != nil {
		return *probe.Success
	}
	return true
}

// userFacingStatus keeps only what a person watching the turn can act on, and
// rewrites agent-loop vocabulary into it. Returns "" for noise.
//
// Dropped: the model name (identical on every message of every turn), the step
// counter (a loop bound, not progress), and bare "Thinking" (the spinner
// already says that).
func userFacingStatus(raw string) string {
	msg := strings.TrimSpace(raw)
	switch {
	case msg == "":
		return ""
	case msg == "Thinking":
		return ""
	case strings.HasPrefix(msg, "Processing with "):
		return ""
	case strings.HasPrefix(msg, "Step ") && strings.Contains(msg, "/"):
		return ""
	case strings.HasPrefix(msg, "Completed in "):
		return ""
	}
	return msg
}

// setLiveStatus replaces the current status line instead of appending one, so
// the activity area shows the latest stage rather than a transcript of stages.
func (m *ChatModel) setLiveStatus(msg string) {
	for i := len(m.activity) - 1; i >= 0; i-- {
		if m.activity[i].Kind == "status" {
			m.activity[i].Content = msg
			return
		}
		// A completed tool call is evidence of work and stays; a status line
		// after it is a NEW stage, so stop looking and add one.
		if m.activity[i].Kind == "tool" {
			break
		}
	}
	m.activity = append(m.activity, ActivityItem{Kind: "status", Content: msg})
}
