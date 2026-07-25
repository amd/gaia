package chat

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// respondingClient records the out-of-band answers the chat model delivers.
type respondingClient struct {
	nullClient
	answers [][2]string
	err     error
}

func (r *respondingClient) Respond(_ context.Context, requestID, value string) error {
	r.answers = append(r.answers, [2]string{requestID, value})
	return r.err
}

func needsInput() event.CanonicalNeedsInputEvent {
	return event.CanonicalNeedsInputEvent{
		Type:      "needs_input",
		RunID:     "run-1",
		RequestID: "q1",
		Question:  "I don't have access to a Gmail mailbox yet. Connect one now?",
		Options: []event.CanonicalInputOption{
			{Value: "yes", Label: "Connect Gmail", Description: "Opens your browser to sign in."},
			{Value: "no", Label: "Not now", Description: "Change nothing."},
		},
		AllowFreeText:  true,
		RespondURL:     "/v1/email/query/run-1/respond",
		TimeoutSeconds: 240,
	}
}

func modelWith(t *testing.T, c *respondingClient) ChatModel {
	t.Helper()
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 80, 24
	m.resize()
	m.streaming = true
	return m
}

// A question does NOT end the turn: the run is parked on the same stream.
func TestNeedsInputKeepsTheTurnAlive(t *testing.T) {
	m := modelWith(t, &respondingClient{})
	m = feed(t, m, needsInput())

	if !m.streaming {
		t.Error("needs_input must not end the turn — the run is waiting on it")
	}
	if m.question == nil {
		t.Fatal("the question was not put up")
	}
	if m.question.RequestID() != "q1" {
		t.Errorf("request id = %q", m.question.RequestID())
	}
	view := m.View()
	for _, want := range []string{"Connect Gmail", "Opens your browser", "Not now"} {
		if !strings.Contains(view, want) {
			t.Errorf("question not rendered in chat (missing %q):\n%s", want, view)
		}
	}
}

// Answering delivers the value on the transport's out-of-band seam and shows the
// chosen option in the transcript.
func TestAnsweringDeliversValueToTheTransport(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())

	updated, cmd := m.Update(components.QuestionAnsweredMsg{RequestID: "q1", Value: "yes"})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("answering produced no command")
	}
	if msg := cmd(); msg != nil {
		t.Fatalf("expected a clean delivery, got %#v", msg)
	}
	if len(c.answers) != 1 || c.answers[0] != [2]string{"q1", "yes"} {
		t.Fatalf("transport answers = %v", c.answers)
	}
	if m.question != nil {
		t.Error("the question should be cleared once answered")
	}
	if !m.streaming {
		t.Error("the run continues after the answer")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleUser || last.Content != "Connect Gmail" {
		t.Errorf("transcript entry = %+v, want the chosen option's label", last)
	}
}

// A failed delivery is surfaced, never swallowed — otherwise it looks exactly
// like an agent that stopped thinking.
func TestFailedAnswerIsSurfaced(t *testing.T) {
	c := &respondingClient{err: errors.New("the 'email' run had already ended")}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())

	_, cmd := m.Update(components.QuestionAnsweredMsg{RequestID: "q1", Value: "yes"})
	msg := cmd()
	failure, ok := msg.(questionFailedMsg)
	if !ok {
		t.Fatalf("expected questionFailedMsg, got %#v", msg)
	}
	updated, _ := m.Update(failure)
	m = updated.(ChatModel)
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "already ended") {
		t.Errorf("failure not surfaced: %+v", last)
	}
}

// A transport with no Respond is a dead end for the user; say so.
func TestAnswerOnTransportWithoutRespondIsLoud(t *testing.T) {
	c := &nullClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 80, 24
	m.streaming = true
	m = feed(t, m, needsInput())

	_, cmd := m.Update(components.QuestionAnsweredMsg{RequestID: "q1", Value: "yes"})
	failure, ok := cmd().(questionFailedMsg)
	if !ok {
		t.Fatalf("expected questionFailedMsg, got %#v", cmd())
	}
	if !strings.Contains(failure.err.Error(), "cannot answer questions mid-run") {
		t.Errorf("unhelpful error: %v", failure.err)
	}
}

// A late answer for a question that is no longer up must not be delivered.
func TestStaleAnswerIsDropped(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())

	updated, cmd := m.Update(components.QuestionAnsweredMsg{RequestID: "old", Value: "yes"})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("a stale answer must not be sent")
	}
	if len(c.answers) != 0 {
		t.Errorf("transport received a stale answer: %v", c.answers)
	}
	if m.question == nil {
		t.Error("the live question must stay up")
	}
}

// While a question is up, keystrokes drive it rather than the composer.
func TestKeysRouteToThePendingQuestion(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m = feed(t, m, needsInput())

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("2")})
	m = updated.(ChatModel)
	if cmd == nil {
		t.Fatal("the number shortcut did not answer the question")
	}
	answer, ok := cmd().(components.QuestionAnsweredMsg)
	if !ok {
		t.Fatalf("expected QuestionAnsweredMsg, got %#v", cmd())
	}
	if answer.Value != "no" {
		t.Errorf("answer = %q, want no", answer.Value)
	}
}

// Cancelling the turn takes the question down with it.
func TestCancellingTheTurnClearsTheQuestion(t *testing.T) {
	c := &respondingClient{}
	m := modelWith(t, c)
	m.cancelFn = func() {}
	m = feed(t, m, needsInput())

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if m.question != nil {
		t.Error("an abandoned question must not stay on screen")
	}
	if m.streaming {
		t.Error("Esc must still cancel the turn while a question is up")
	}
}

// A turn that ends while a question is up must take the question down with it.
// Otherwise handleKey keeps routing every keystroke into a dead question: the
// composer is unreachable and Esc quits the app instead of dismissing it.
func TestTerminalEventClearsThePendingQuestion(t *testing.T) {
	for _, tc := range []struct {
		name string
		evt  interface{}
	}{
		{"final", event.CanonicalFinalEvent{Type: "final", Answer: "nothing was answered"}},
		{"error", event.CanonicalErrorEvent{Type: "error", Detail: "the run failed"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := modelWith(t, &respondingClient{})
			m = feed(t, m, needsInput())
			if m.question == nil {
				t.Fatal("the question was not put up")
			}
			m = feed(t, m, tc.evt)

			if m.question != nil {
				t.Fatal("the question outlived the turn it belonged to")
			}
			// The composer takes text again.
			updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("h")})
			if got := updated.(ChatModel).input.Value(); got != "h" {
				t.Errorf("composer value = %q, want \"h\" — keystrokes are still trapped", got)
			}
		})
	}
}

// The whole panel has to fit an 80x24 terminal.
func TestQuestionFitsTheDefaultTerminal(t *testing.T) {
	m := modelWith(t, &respondingClient{})
	m = feed(t, m, needsInput())
	for _, line := range strings.Split(stripANSIChat(m.View()), "\n") {
		if w := len([]rune(line)); w > 80 {
			t.Errorf("line is %d cols wide (max 80): %q", w, line)
		}
	}
}

func stripANSIChat(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); {
		if s[i] == 0x1b {
			for i < len(s) && s[i] != 'm' {
				i++
			}
			i++
			continue
		}
		b.WriteByte(s[i])
		i++
	}
	return b.String()
}

// A status line longer than the pane must WRAP, not clip. The viewport does not
// soft-wrap, so the tail is simply lost — and on the capability notice the tail
// is the remedy: the version that fixes it and the command to run. Losing that
// leaves an error that says what broke and nothing about what to do.
func TestLongStatusLineWrapsInsteadOfLosingItsTail(t *testing.T) {
	notice := "the installed 'email' agent speaks contract 2.5, so it cannot ask " +
		"questions mid-task — in-conversation mailbox setup needs 2.6 or newer. " +
		"Update it with `gaia hub uninstall email` then `gaia hub install email`."

	for _, cols := range []int{80, 100, 120} {
		t.Run(fmt.Sprintf("%dcols", cols), func(t *testing.T) {
			m := modelWith(t, &respondingClient{})
			m.width, m.height = cols, 24
			m.resize()
			m = feed(t, m, event.CanonicalNoticeEvent{Text: notice})

			view := stripANSIChat(m.View())
			// The remedy — the actionable half — has to be on screen somewhere.
			for _, want := range []string{"2.6 or newer", "gaia hub install email"} {
				if !strings.Contains(view, want) {
					t.Errorf("the notice lost %q at %d cols:\n%s", want, cols, view)
				}
			}
			for _, line := range strings.Split(view, "\n") {
				if w := len([]rune(line)); w > cols {
					t.Errorf("line is %d cols wide (max %d): %q", w, cols, line)
				}
			}
		})
	}
}

// A long free-text answer is echoed into the transcript as a user line, which is
// rendered bare — so it needs the same wrap.
func TestLongUserLineWraps(t *testing.T) {
	m := modelWith(t, &respondingClient{})
	m.width, m.height = 80, 24
	m.resize()
	m.messages = append(m.messages, Message{
		Role:    RoleUser,
		Content: strings.Repeat("some-long-client-id.apps.googleusercontent.com ", 3),
	})
	m.updateViewport()

	for _, line := range strings.Split(stripANSIChat(m.View()), "\n") {
		if w := len([]rune(line)); w > 80 {
			t.Errorf("line is %d cols wide (max 80): %q", w, line)
		}
	}
}
