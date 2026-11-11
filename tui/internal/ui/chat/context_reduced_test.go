package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

const reducedNotice = "Moved 3 older tool results out of the prompt (~12.4K tokens); still readable"

func TestContextReductionLandsInTheTranscriptEachTime(t *testing.T) {
	var reduced event.CanonicalStatusEvent
	wire := `{"type":"status","message":"` + reducedNotice + `","context_reduced":{"reason":"proactive","results":3,"tokens_freed":12400,"prompt_tokens_before":10100,"prompt_tokens_after":6200,"context_window":16384,"items":[]}}`
	if err := json.Unmarshal([]byte(wire), &reduced); err != nil {
		t.Fatal(err)
	}
	cr := reduced.ContextReduced
	if cr == nil || cr.Results != 3 || cr.TokensFreed != 12400 || cr.ContextWindow != 16384 || cr.Reason != "proactive" {
		t.Fatalf("context_reduced not decoded: %+v", cr)
	}

	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, reduced, event.CanonicalStatusEvent{Type: "status", Message: "Reading the files"}, reduced)

	var notices []Message
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && msg.Content == reducedNotice {
			notices = append(notices, msg)
		}
	}
	if len(notices) != 2 {
		t.Fatalf("want one transcript line per batch, got %+v", m.messages)
	}
	for _, a := range m.activity {
		if strings.Contains(a.Content, "out of the prompt") {
			t.Errorf("the notice belongs in the transcript, not the live work log: %+v", a)
		}
	}
	m.updateViewport()
	if view := ansi.Strip(m.viewport.View()); !strings.Contains(view, "out of the prompt") {
		t.Errorf("notice not rendered:\n%s", view)
	}
}
