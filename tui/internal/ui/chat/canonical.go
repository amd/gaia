package chat

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// handleCanonicalEvent renders the canonical `/query` SSE vocabulary — what the
// daemon transport streams. handled is false for anything else, so the legacy
// in-process types (used by the subprocess transport) fall through untouched.
func (m ChatModel) handleCanonicalEvent(evt interface{}) (ChatModel, tea.Cmd, bool) {
	switch e := evt.(type) {
	case event.CanonicalStatusEvent:
		if msg := strings.TrimSpace(e.Message); msg != "" {
			m.activity = append(m.activity, ActivityItem{Kind: "status", Content: msg})
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

	case event.CanonicalNeedsConfirmationEvent:
		// The approval UI is a later phase. Until then the pause is surfaced as a
		// message the user can actually read — never swallowed — and the run
		// continues to its own terminal event.
		line := "confirmation needed: " + e.Action
		if summary := strings.TrimSpace(e.Summary); summary != "" {
			line += " — " + summary
		}
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: "⚠️  " + line})

	case event.CanonicalFinalEvent:
		usage := event.CanonicalUsageOf(e)
		// Tokens already streamed the answer into the buffer; the terminal
		// `final` must not print it a second time.
		content := m.buffer
		m.buffer = ""
		if content == "" {
			content = e.Answer
		}
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
		m.updateViewport()
		return m, nil, true

	case event.CanonicalUnsupportedEvent:
		// Contract §7: a newer agent's event type is shown, never dropped.
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: fmt.Sprintf("unsupported event %q from the agent (update GAIA to render it)", e.EventType),
		})

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

// markToolDone closes out the activity line opened by the matching tool_call.
//
// The canonical tool_result carries no success flag, so the agent's own
// {"ok": bool} / {"success": bool} convention is read out of `data` when present;
// absent that, a delivered result counts as completed. The typed render card
// (Phase 5) is what will show per-tool detail.
func (m *ChatModel) markToolDone(e event.CanonicalToolResultEvent) {
	success := toolResultSucceeded(e.Data)

	label := ""
	if e.Render != "" {
		label = "render:" + e.Render
	}

	for i := len(m.activity) - 1; i >= 0; i-- {
		item := &m.activity[i]
		if item.Kind != "tool" || item.Done {
			continue
		}
		item.Done = true
		item.Success = &success
		if label != "" {
			item.Content += " → " + label
		}
		return
	}

	// A result with no matching call still has to be visible.
	content := e.Tool
	if label != "" {
		content += " → " + label
	}
	m.activity = append(m.activity, ActivityItem{
		Kind:    "tool",
		Content: content,
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
