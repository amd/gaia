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

// confirmActionResultMsg reports the outcome of delivering an approve/deny
// decision over the resume-model confirm seam (client.AgentConfirmer). Only
// produced when the triggering event carried a confirm_url — see
// (ChatModel).confirmAction.
type confirmActionResultMsg struct {
	Action   string
	Approved bool
	err      error
}

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
		// Only trust the failure classifier where a card was declared. Outside
		// the render domain the sidecar's truncated, string-encoded `summary`
		// fools it into misreading an ordinary partial-success batch as a
		// failure (#2723) — do not widen this gate before that lands; see
		// plan S1 / AC-5 / AC-7c for the harness that proved it.
		if e.Render == "" {
			m.markToolDone(e)
			break
		}
		if outcome, toolErr := event.ToolOutcomeOf(e); outcome == event.ToolOutcomeFailed {
			// ToolOutcomeFailed always ticks red here — deliberately overriding
			// ToolOutcome's "Unknown is never a pass" doc comment, but only for
			// this two-state presentation mapping (S3): Succeeded and Unknown
			// both still tick green via markToolDone below.
			m.setOpenToolOutcome(e.Tool, false)
			m.messages = append(m.messages, Message{
				Role:    RoleError,
				Content: sanitizeErrorText(composeToolErrorText(e.Tool, toolErr)),
			})
			break
		}
		// The sidecar declared a card, so the card is the result. The email
		// agent's pre-scan tool docstring tells the model NOT to describe the
		// results in prose precisely because the client is expected to draw
		// this — ignore `render` and the turn produces one vague sentence.
		m.markToolDone(e)
		identity := ""
		if e.Render == "email_pre_scan" {
			// The one card this session can produce from TWO independent
			// sources (this typed tool_result, or the on-open pre-scan
			// fetch) — update it in place rather than letting each source
			// append its own (#2743).
			identity = preScanCardIdentity
			m.preScanRenderedThisTurn = true
		}
		m.upsertCard(identity, e.Tool, e.Render, e.Data)

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
		// The pause goes to the permanent transcript — never swallowed — AND to
		// the interactive modal, which owns the keyboard until the user answers
		// or the 30s client-side timeout auto-denies. The status line stays
		// durable in scrollback even after the modal resolves and clears (see
		// CanonicalFinalEvent below): the modal is the CURRENT decision surface,
		// this line is the permanent record of it.
		line := "confirmation needed: " + e.Action
		if summary := strings.TrimSpace(e.Summary); summary != "" {
			line += " — " + summary
		}
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: "[!] " + line})

		cm := components.NewConfirmationModel(e.RunID, e.Action, e.Summary, e.ConfirmURL)
		cm.SetWidth(m.cardWidth())
		m.confirmation = &cm
		m.updateViewport()
		return m, tea.Batch(waitForEvent(m.events), components.StartConfirmationTimeout(e.RunID)), true

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
		// Drain here, not on doneMsg: streaming flips false in THIS handler, and doneMsg fires later, after a second query could already be in flight.
		m.drainPendingPreScan()
		m.streaming = false
		m.activity = nil
		// The turn is over, so any question it was waiting on is dead. Leaving
		// the panel up would swallow every keystroke into a question nobody is
		// listening to — the composer becomes unreachable and Esc quits the app.
		m.question = nil
		// Same reasoning for a still-pending confirmation — and on the current
		// email sidecar this is the ORDINARY case, not an edge one: the stateless
		// D1 stub sends this final refusal in the same stream read the
		// needs_confirmation event arrived on, before a human can plausibly react.
		m.resolveConfirmationOnTurnEnd()
		if usage.Steps > 0 {
			m.totalSteps = usage.Steps
		}
		m.updateViewport()
		return m, nil, true

	case event.CanonicalErrorEvent:
		m.flushBuffer()
		m.resolveConfirmationOnTurnEnd()
		m.messages = append(m.messages, Message{Role: RoleError, Content: e.Detail})
		m.drainPendingPreScan()
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

// resolveConfirmationOnTurnEnd clears a still-pending confirmation when the
// run's own terminal event gets there first — which, against the current
// email sidecar's stateless D1 stub, is the NORMAL case: `needs_confirmation`
// is immediately followed, in the same stream read, by a synthesized `final`
// refusal (docs/spec/agent-ui-query-sse-contract.md §5). Leaving the modal up
// would trap every keystroke in a decision that can no longer change anything
// — the composer becomes unreachable, exactly the bug m.question=nil already
// fixes for a mid-run question. The permanent transcript line added when the
// event first arrived (not the modal) is the durable record of what happened.
func (m *ChatModel) resolveConfirmationOnTurnEnd() {
	if m.confirmation == nil {
		return
	}
	if m.confirmation.Pending() {
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "[!] confirmation for '" + m.confirmation.Action() +
				"' resolved: denied — the run ended before it could be answered. Nothing was sent.",
		})
	}
	m.confirmation = nil
}

// resolveConfirmationDecision records a confirmation's outcome — from a
// keypress or the 30s timeout — in the activity log and the permanent
// transcript, then attempts live delivery when (and only when) the triggering
// event carried a confirm_url.
func (m ChatModel) resolveConfirmationDecision(msg components.ConfirmationDecidedMsg) (tea.Model, tea.Cmd) {
	outcome, success := confirmationOutcomeText(msg)
	m.activity = append(m.activity, ActivityItem{
		Kind:    "confirm",
		Content: "confirm " + msg.Action + ": " + outcome,
		Done:    true,
		Success: &success,
	})
	m.messages = append(m.messages, Message{
		Role:    RoleStatus,
		Content: "[!] confirmation for '" + msg.Action + "' resolved: " + outcome,
	})
	m.confirmation = nil
	m.updateViewport()

	if msg.ConfirmURL == "" {
		// The stateless model (what every shipped sidecar speaks today): there is
		// no channel to deliver a decision to, so there is nothing further to do
		// — recording the outcome above is the whole job.
		return m, nil
	}
	return m, m.confirmAction(msg.RunID, msg.Action, msg.Approved)
}

// confirmationOutcomeText is the one-line outcome recorded for a resolved
// confirmation. Never claims delivery that cannot happen: an approval with no
// confirm_url is recorded as exactly that, not as "approved" — see
// ConfirmationModel's doc comment for why (ui/oneshot.go's writeWithheld
// already draws this line for the one-shot surface; this is the same rule
// applied here).
func confirmationOutcomeText(msg components.ConfirmationDecidedMsg) (text string, success bool) {
	switch {
	case msg.TimedOut:
		return "denied (30s timeout — no response)", false
	case msg.Approved && msg.ConfirmURL != "":
		return "approved — delivering", true
	case msg.Approved:
		return "approved, but this transport has no live approval channel yet " +
			"(no confirm_url on the event) — nothing was actually sent", false
	default:
		return "denied — nothing sent", false
	}
}

// confirmAction delivers the user's decision on the transport's out-of-band
// confirm seam. Only reachable when the triggering event carried a
// confirm_url — the resume model (spec §5). No shipped sidecar sets that
// field today, so this path exists for forward compatibility and is not
// exercised against the current email agent; see ConfirmationModel's doc
// comment for the full picture.
func (m ChatModel) confirmAction(runID, action string, approved bool) tea.Cmd {
	confirmer, ok := m.client.(client.AgentConfirmer)
	if !ok {
		return func() tea.Msg {
			return confirmActionResultMsg{Action: action, Approved: approved, err: fmt.Errorf(
				"this agent connection cannot deliver a confirmation decision; " +
					"relaunch the agent through the GAIA daemon transport")}
		}
	}
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), answerTimeout)
		defer cancel()
		err := confirmer.Confirm(ctx, runID, approved)
		return confirmActionResultMsg{Action: action, Approved: approved, err: err}
	}
}

// markToolDone closes out the activity line opened by the matching tool_call.
//
// The canonical tool_result carries no success flag, so the agent's own
// {"ok": bool} / {"success": bool} convention is read out of `data` when present;
// absent that, a delivered result counts as completed. Per-tool detail belongs
// to the render card drawn in the transcript, not to this one-line summary.
func (m *ChatModel) markToolDone(e event.CanonicalToolResultEvent) {
	m.setOpenToolOutcome(e.Tool, toolResultSucceeded(e.Data))
}

// setOpenToolOutcome closes out the activity line opened by the matching
// tool_call with an explicit success value. Shared by markToolDone (which
// derives success from toolResultSucceeded) and the failed-render path in
// handleCanonicalEvent (which must not: that classifier trusts the sidecar's
// `success: true` even when the tool's own nested result says otherwise).
func (m *ChatModel) setOpenToolOutcome(tool string, success bool) {
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
		Content: tool,
		Done:    true,
		Success: &success,
	})
}

// composeToolErrorText builds the RoleError text for a failed render tool:
// the tool name, the error's machine-readable Code when present, then the
// tool's own message verbatim. Kept chat-local rather than shared with
// ui.writeToolError (D1): package ui imports ui/chat (app.go:19), so the
// reverse import would be a cycle.
func composeToolErrorText(tool string, te event.ToolError) string {
	head := tool + " failed"
	if te.Code != "" {
		head += " — " + te.Code
	}
	message := strings.TrimRight(te.Message, "\n")
	if message == "" {
		return head + " (the tool reported no detail)"
	}
	return head + ": " + message
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
