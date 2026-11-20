package chat

import (
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// confirmAnswerHold is how long a lone y or n waits before it answers a prompt.
// A letter followed inside it by more typing was the start of a sentence, and
// goes to the composer instead. Short enough that a deliberate press still
// feels immediate. A var so tests need not wait it out.
var confirmAnswerHold = 200 * time.Millisecond

// answerGuard is what keeps typing meant for the composer from answering a
// pending prompt.
type answerGuard struct {
	// seq is the prompt the fields below belong to.
	seq uint64
	// held is the y or n waiting out confirmAnswerHold; id names its timer.
	held string
	id   int
	// armed is the a or A that highlighted "always", kept so the letter can be
	// given back to the composer if more typing follows it.
	armed string
}

// confirmHoldMsg ends the hold on one answer key.
type confirmHoldMsg struct {
	seq uint64
	id  int
}

// handleConfirmationKey routes one key while a prompt is up. A letter answers
// only when it cannot be part of a message: the composer is empty and nothing
// is typed right after it. Everything else typed is kept in the composer.
func (m ChatModel) handleConfirmationKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	if !m.confirmation.Pending() {
		return m.forwardToConfirmation(msg)
	}
	if m.answer.seq != m.confirmation.Seq() {
		m.answer = answerGuard{seq: m.confirmation.Seq()}
	}

	if msg.Paste {
		m.answer.held, m.answer.armed = "", ""
		return m.typeIntoComposer(normalizePastedText(string(msg.Runes)))
	}

	if text, ok := typedText(msg); ok {
		// More typing straight after an answer letter: it was a word, not an answer.
		if m.answer.held != "" {
			text = m.answer.held + text
			m.answer.held = ""
			return m.typeIntoComposer(text)
		}
		if m.answer.armed != "" {
			text = m.answer.armed + text
			m.answer.armed = ""
			return m.typeIntoComposer(text)
		}
		if m.input.Value() != "" {
			return m.typeIntoComposer(text)
		}
		switch text {
		case "y", "Y", "n", "N":
			m.answer.held = text
			m.answer.id++
			hold := confirmHoldMsg{seq: m.answer.seq, id: m.answer.id}
			return m, tea.Tick(confirmAnswerHold, func(time.Time) tea.Msg { return hold })
		case "a", "A":
			if m.confirmation.OffersAlways() {
				m.answer.armed = text
				return m.forwardToConfirmation(msg)
			}
		case "j", "k", " ":
			if m.confirmation.Scrolls() {
				return m.forwardToConfirmation(msg)
			}
		}
		return m.typeIntoComposer(text)
	}

	switch msg.Type {
	case tea.KeyEnter:
		// Enter straight after y or n is as deliberate as an answer gets.
		if m.answer.held != "" {
			return m.releaseHeldAnswer()
		}
	case tea.KeyBackspace:
		switch {
		case m.answer.held != "":
			m.answer.held = ""
			return m, nil
		case m.confirmation.Chosen():
			m.answer.armed = ""
			m.confirmation.ClearChoice()
			m.updateViewport()
			return m, nil
		}
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		m.syncComposerHeight()
		return m, cmd
	}
	m.answer.held, m.answer.armed = "", ""
	return m.forwardToConfirmation(msg)
}

// typedText is the text a key types into the composer, if it types any.
func typedText(msg tea.KeyMsg) (string, bool) {
	if msg.Alt {
		return "", false
	}
	switch msg.Type {
	case tea.KeyRunes:
		return string(msg.Runes), true
	case tea.KeySpace:
		return " ", true
	}
	return "", false
}

// typeIntoComposer keeps text typed over a prompt. It also drops any highlight:
// an Enter that follows typing is meant for the message, not for the prompt.
func (m ChatModel) typeIntoComposer(text string) (tea.Model, tea.Cmd) {
	m.confirmation.ClearChoice()
	m.input.InsertString(text)
	m.syncComposerHeight()
	m.updateViewport()
	return m, nil
}

func (m ChatModel) forwardToConfirmation(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	c, cmd := m.confirmation.Update(msg)
	m.confirmation = &c
	m.updateViewport()
	return m, cmd
}

// releaseHeldAnswer gives the held y or n to the prompt as its answer.
func (m ChatModel) releaseHeldAnswer() (tea.Model, tea.Cmd) {
	key := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(m.answer.held)}
	m.answer.held = ""
	return m.forwardToConfirmation(key)
}

// resolveConfirmHold answers the prompt once the hold ends with nothing typed
// after the letter. A hold left by an answered or replaced prompt does nothing.
func (m ChatModel) resolveConfirmHold(msg confirmHoldMsg) (tea.Model, tea.Cmd) {
	if m.confirmation == nil || !m.confirmation.Pending() || m.answer.held == "" ||
		msg.seq != m.confirmation.Seq() || msg.seq != m.answer.seq || msg.id != m.answer.id {
		return m, nil
	}
	return m.releaseHeldAnswer()
}

// answeringByLetter reports whether y, a and n answer the pending prompt right
// now. With a message in the composer they are typed into it instead.
func (m ChatModel) answeringByLetter() bool {
	return m.input.Value() == ""
}
