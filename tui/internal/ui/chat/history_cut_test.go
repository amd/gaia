package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

const cutNotice = "Older messages are now out of the model's view (6 turns). Ask me to recall them if you need them."

func TestHistoryCutLandsInTheTranscriptOnce(t *testing.T) {
	var cut event.CanonicalStatusEvent
	wire := `{"type":"status","message":"` + cutNotice + `","history_cut":{"turns":6,"tokens":60}}`
	if err := json.Unmarshal([]byte(wire), &cut); err != nil {
		t.Fatal(err)
	}
	if cut.HistoryCut == nil || cut.HistoryCut.Turns != 6 || cut.HistoryCut.Tokens != 60 {
		t.Fatalf("history_cut not decoded: %+v", cut.HistoryCut)
	}

	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, cut, event.CanonicalStatusEvent{Type: "status", Message: "Scanning inbox"})

	var notices []Message
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, "out of the model's view") {
			notices = append(notices, msg)
		}
	}
	if len(notices) != 1 || notices[0].Content != cutNotice {
		t.Fatalf("want one transcript notice, got %+v", m.messages)
	}
	for _, a := range m.activity {
		if strings.Contains(a.Content, "out of the model's view") {
			t.Errorf("the notice belongs in the transcript, not the live work log: %+v", a)
		}
	}
	m.updateViewport()
	if view := ansi.Strip(m.viewport.View()); !strings.Contains(view, "out of the model's view") {
		t.Errorf("notice not rendered:\n%s", view)
	}
}

func TestAnAnswerFromAnUnfinishedIndexIsMarkedInTheTranscript(t *testing.T) {
	const line = "Still reading images in report.pdf, so this answer may be incomplete."
	var status event.CanonicalStatusEvent
	wire := `{"type":"status","message":"` + line + `","partial_index":{"names":["report.pdf"],"pages_done":3,"pages_total":9}}`
	if err := json.Unmarshal([]byte(wire), &status); err != nil {
		t.Fatal(err)
	}
	if status.PartialIndex == nil || status.PartialIndex.PagesTotal != 9 {
		t.Fatalf("partial_index not decoded: %+v", status.PartialIndex)
	}

	m, _ := newTestModel(t)
	m.streaming = true
	m = feed(t, m, status)

	var notices []Message
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && msg.Content == line {
			notices = append(notices, msg)
		}
	}
	if len(notices) != 1 {
		t.Fatalf("want the line kept in the transcript once, got %+v", m.messages)
	}
	for _, a := range m.activity {
		if strings.Contains(a.Content, "may be incomplete") {
			t.Errorf("a turn's work log is dropped when it ends, so the warning must not live there: %+v", a)
		}
	}
	m.updateViewport()
	if !strings.Contains(strings.Join(strings.Fields(ansi.Strip(m.viewport.View())), " "), "may be incomplete") {
		t.Errorf("the warning is not on screen:\n%s", ansi.Strip(m.viewport.View()))
	}
}
