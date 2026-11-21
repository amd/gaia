package control

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

type batchEndSpy struct{ seen []tea.Msg }

func (s *batchEndSpy) Init() tea.Cmd { return nil }
func (s *batchEndSpy) View() string  { return "" }
func (s *batchEndSpy) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	s.seen = append(s.seen, msg)
	return s, nil
}

// The model never sees the mark itself, only that the batch behind it is over —
// which is what lets a key that might start a word be read as a whole answer.
func TestTheRecorderTellsTheModelABatchEnded(t *testing.T) {
	spy := &batchEndSpy{}
	rec := NewRecorder(spy, NewState(nil))

	key := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y")}
	next, _ := rec.Update(key)
	next.Update(MarkMsg{ID: 1})

	if len(spy.seen) != 2 {
		t.Fatalf("the model saw %d messages, want the key and the batch end: %#v", len(spy.seen), spy.seen)
	}
	if _, ok := spy.seen[1].(InputBatchEndMsg); !ok {
		t.Errorf("after the key the model saw %#v, want InputBatchEndMsg", spy.seen[1])
	}
	for _, msg := range spy.seen {
		if _, leaked := msg.(MarkMsg); leaked {
			t.Error("the mark itself reached the model")
		}
	}
}
