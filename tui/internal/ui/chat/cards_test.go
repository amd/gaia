package chat

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

const prescanPayload = `{
  "kind": "email_pre_scan",
  "urgent": [], "actionable": [], "informational_count": 6,
  "suggested_archives": [], "suggested_drafts": [], "needs_review": [],
  "preferences_applied": null,
  "scanned": 9,
  "needs_you": [
    {"ref":1,"kind":"urgent","message_id":"m1","sender":"\"Sarah Chen\" <sarah@example.com>",
     "subject":"Prod incident follow-up","why":"asked for a reply by Friday"}
  ],
  "needs_you_total": 1,
  "bulk": {"count": 7, "filter_tests": ["category_fyi"]}
}`

// newTestChat returns a model sized to an 80x24 terminal with a turn in flight.
func newTestChat(t *testing.T) ChatModel {
	t.Helper()
	m := NewChatModel(&nullClient{}, "email", "", false)
	m.width, m.height = 80, 24
	m.resize()
	m.streaming = true
	m.queryStart = time.Now()
	return m
}

// The flagship path: the email agent's pre-scan tool deliberately tells the model
// NOT to describe its results in prose because it expects the client to draw the
// card. A client that ignores `render` therefore shows a terse one-liner and
// nothing else — the data is on the wire and nobody draws it.
func TestToolResultWithRenderDrawsACard(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{
			Type:   "tool_result",
			Tool:   "pre_scan_inbox",
			Render: "email_pre_scan",
			Data:   json.RawMessage(prescanPayload),
		},
	)

	var card *Message
	for i := range m.messages {
		if m.messages[i].Role == RoleCard {
			card = &m.messages[i]
		}
	}
	if card == nil {
		t.Fatal("tool_result carrying render=email_pre_scan produced no card message")
	}
	if card.Render != "email_pre_scan" || card.ToolName != "pre_scan_inbox" {
		t.Errorf("card = {render:%q tool:%q}, want {email_pre_scan pre_scan_inbox}", card.Render, card.ToolName)
	}

	rendered := ansi.Strip(m.renderMessage(card, nil))
	t.Logf("\n%s", rendered)
	for _, want := range []string{"Inbox · 9 scanned", "NEEDS YOU", "REPLY", "Sarah Chen", "asked for a reply by Friday"} {
		if !strings.Contains(rendered, want) {
			t.Errorf("card render missing %q:\n%s", want, rendered)
		}
	}
	// The card is the result, so the activity line no longer repeats the key.
	for _, item := range m.activity {
		if strings.Contains(item.Content, "render:") {
			t.Errorf("activity line still carries the raw render key: %q", item.Content)
		}
	}
}

func TestUnknownRenderStillDrawsACard(t *testing.T) {
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type:   "tool_result",
		Tool:   "some_tool",
		Render: "some_future_card",
		Data:   json.RawMessage(`{"anything":1}`),
	})

	card := lastCard(t, m)
	rendered := ansi.Strip(m.renderMessage(&card, nil))
	t.Logf("\n%s", rendered)
	if !strings.Contains(rendered, "Unsupported card type") {
		t.Errorf("unknown render did not degrade to the generic card:\n%s", rendered)
	}
	if strings.TrimSpace(rendered) == "" {
		t.Error("unknown render blanked the message")
	}
}

func TestToolResultWithoutRenderMakesNoCard(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_inbox"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "list_inbox", Data: json.RawMessage(`{"ok":true}`)},
	)
	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			t.Fatalf("a tool_result with no render key produced a card: %+v", msg)
		}
	}
}

// A card drawn mid-turn stays above the live region, so work and results keep
// the order they happened in.
func TestCardRendersInlineAboveTheLiveRegion(t *testing.T) {
	tall := newTestChat(t)
	// A taller window than 24 rows so the whole turn is visible at once — the
	// viewport pins to the bottom, and a scrolled-off card proves nothing here.
	tall.height = 48
	tall.resize()

	m := feed(t, tall,
		event.CanonicalStatusEvent{Type: "status", Message: "scanning inbox"},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "pre_scan_inbox",
			Render: "email_pre_scan", Data: json.RawMessage(prescanPayload)},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_messages"},
	)
	m.updateViewport()

	view := ansi.Strip(m.viewport.View())
	cardAt := strings.Index(view, "NEEDS YOU")
	liveAt := strings.Index(view, "archive_messages")
	if cardAt < 0 || liveAt < 0 {
		t.Fatalf("expected both the card and the live region in the viewport:\n%s", view)
	}
	if cardAt > liveAt {
		t.Errorf("card rendered below the live region; ordering of work and results is lost:\n%s", view)
	}
}

func TestCardFitsAn80ColumnTerminal(t *testing.T) {
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "pre_scan_inbox",
		Render: "email_pre_scan", Data: json.RawMessage(prescanPayload),
	})

	rendered := func() string { c := lastCard(t, m); return ansi.Strip(m.renderMessage(&c, nil)) }()
	for i, line := range strings.Split(rendered, "\n") {
		if w := ansi.StringWidth(line); w > 80 {
			t.Errorf("card line %d is %d columns wide, overflowing an 80-column terminal: %q", i, w, line)
		}
	}
}

// The full frame has to survive 80x24 without the layout breaking — the card
// scrolls inside the viewport, it never widens the screen.
func TestFullViewAt80x24(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "pre_scan_inbox",
			Render: "email_pre_scan", Data: json.RawMessage(prescanPayload)},
	)
	m.messages = append([]Message{{Role: RoleUser, Content: "triage my inbox"}}, m.messages...)
	m.updateViewport()

	view := ansi.Strip(m.View())
	t.Logf("\n%s", view)

	lines := strings.Split(view, "\n")
	if len(lines) > 24 {
		t.Errorf("full view is %d rows, want at most 24", len(lines))
	}
	for i, line := range lines {
		if w := ansi.StringWidth(line); w > 80 {
			t.Errorf("view line %d is %d columns wide, want at most 80: %q", i, w, line)
		}
	}
}

func lastCard(t *testing.T, m ChatModel) Message {
	t.Helper()
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleCard {
			return m.messages[i]
		}
	}
	t.Fatal("no card message was produced")
	return Message{}
}

// The card render is memoized per width to keep streaming cheap. A resize must
// invalidate it — serving the old layout at a new width shears the border.
func TestCardCacheInvalidatesOnResize(t *testing.T) {
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "pre_scan_inbox",
		Render: "email_pre_scan", Data: json.RawMessage(prescanPayload),
	})

	card := lastCard(t, m)
	wide := ansi.Strip(card.renderCard(76))
	again := ansi.Strip(card.renderCard(76))
	if wide != again {
		t.Error("same width returned a different render")
	}
	narrow := ansi.Strip(card.renderCard(40))
	if narrow == wide {
		t.Fatal("a new width returned the cached render for the old one")
	}
	for i, line := range strings.Split(narrow, "\n") {
		if w := ansi.StringWidth(line); w != 40 {
			t.Errorf("line %d after resize is %d columns, want 40: %q", i, w, line)
		}
	}
}
