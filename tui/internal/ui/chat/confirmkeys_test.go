package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/ui/components"
)

func runeKeyMsg(s string) tea.KeyMsg { return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)} }

// answerKey answers the prompt the way a person does: the letter, then nothing
// typed after it ("a" also takes its Enter). It returns what the answer
// produced, so a caller can read the decision off the command.
func answerKey(t *testing.T, m ChatModel, key string) (tea.Model, tea.Cmd) {
	t.Helper()
	updated, cmd := m.handleKey(runeKeyMsg(key))
	m = updated.(ChatModel)
	switch strings.ToLower(key) {
	case "a":
		if m.confirmation != nil && m.confirmation.Chosen() {
			return m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
		}
	case "y", "n":
		if cmd == nil {
			return m, nil
		}
		hold, ok := cmd().(confirmHoldMsg)
		if !ok {
			t.Fatalf("key %q did not start the answer hold", key)
		}
		return m.Update(hold)
	}
	return m, cmd
}

func promptStillWaiting(t *testing.T, m ChatModel) {
	t.Helper()
	if m.confirmation == nil || !m.confirmation.Pending() {
		t.Fatal("the prompt was answered by typing")
	}
}

// typeAndSettle types text as the control API does, then lets every hold it
// started run out, so a held letter that was going to answer gets the chance to.
func typeAndSettle(t *testing.T, m ChatModel, text string) ChatModel {
	t.Helper()
	var cmds []tea.Cmd
	for _, key := range control.TextKeyMsgs(text) {
		updated, cmd := m.Update(key)
		m = updated.(ChatModel)
		if cmd != nil {
			cmds = append(cmds, cmd)
		}
	}
	updated, cmd := m.Update(control.InputBatchEndMsg{})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Fatalf("typing %q answered the prompt when the batch ended", text)
	}
	for _, cmd := range cmds {
		if hold, ok := cmd().(confirmHoldMsg); ok {
			updated, decide := m.Update(hold)
			m = updated.(ChatModel)
			if decide != nil {
				t.Fatalf("typing %q answered the prompt", text)
			}
		}
	}
	return m
}

// The defect as reported: the next message typed over a waiting prompt lost its
// first words and approved on the y of "year".
func TestTypingAMessageOverAPromptNeitherAnswersNorLosesIt(t *testing.T) {
	const sentence = "/research What year was the Golden Gate Bridge opened, and by whom?"
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	m = typeAndSettle(t, m, sentence)

	promptStillWaiting(t, m)
	if got := m.input.Value(); got != sentence {
		t.Errorf("the message was not kept whole:\n got %q\nwant %q", got, sentence)
	}
	if len(c.decisions) != 0 {
		t.Errorf("typing delivered a decision: %v", c.decisions)
	}
}

// A sentence can open with an answer letter. Sent as one burst — the control
// API's `text`, or a fast typist — none of them may answer.
func TestASentenceOpeningWithAnAnswerLetterIsText(t *testing.T) {
	for _, sentence := range []string{"yes please", "no wait", "add a test", "Now stop", "nn", "ay"} {
		t.Run(sentence, func(t *testing.T) {
			m, c := liveModel(t)
			m.streaming = true
			m = feed(t, m, gatedShellCall())

			m = typeAndSettle(t, m, sentence)

			promptStillWaiting(t, m)
			if got := m.input.Value(); got != sentence {
				t.Errorf("composer = %q, want %q", got, sentence)
			}
			if m.confirmation.Chosen() {
				t.Error("typing left a choice highlighted for Enter")
			}
			if len(c.decisions) != 0 {
				t.Errorf("typing delivered a decision: %v", c.decisions)
			}
		})
	}
}

// A message already in the composer when the prompt arrives: the letters still
// type, and Enter does not approve.
func TestLettersTypeIntoADraftWrittenBeforeThePrompt(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = typeInto(t, m, "also check ")
	m = feed(t, m, gatedShellCall())

	m = typeAndSettle(t, m, "any yaml")
	m, _ = press(t, m, tea.KeyEnter)

	promptStillWaiting(t, m)
	if got := m.input.Value(); got != "also check any yaml" {
		t.Errorf("composer = %q", got)
	}
	if len(c.decisions) != 0 {
		t.Errorf("typing delivered a decision: %v", c.decisions)
	}
	if !strings.Contains(m.View(), "Tab then Enter answers") {
		t.Errorf("with a draft the screen must say how to answer:\n%s", m.View())
	}
	if strings.Contains(m.View(), "sends to the running turn") {
		t.Errorf("Enter cannot send while a prompt is up, so the row must not say it does:\n%s", m.View())
	}
}

// A lone y, with nothing typed after it, approves once the hold ends — which is
// what a driver sending keys ["y"] to a visible prompt does.
func TestALoneLetterAnswersAfterTheHold(t *testing.T) {
	for key, want := range map[string]bool{"y": true, "Y": true, "n": false, "N": false} {
		m, _ := liveModel(t)
		m.streaming = true
		m = feed(t, m, gatedShellCall())

		updated, cmd := m.handleKey(runeKeyMsg(key))
		m = updated.(ChatModel)
		promptStillWaiting(t, m)
		if m.input.Value() != "" {
			t.Errorf("%q: a held answer must not show in the composer", key)
		}
		_, decide := m.Update(cmd())
		if decide == nil {
			t.Fatalf("%q: the hold ended without an answer", key)
		}
		decided, ok := decide().(components.ConfirmationDecidedMsg)
		if !ok || decided.Approved != want || decided.Always {
			t.Errorf("%q: decided %#v", key, decide())
		}
	}
}

// The control API marks the end of each batch it injects, so a driver's
// keys ["y"] answers there and then — no clock a harness could race.
func TestAControlBatchEndingOnAnAnswerLetterAnswersAtOnce(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, _ := m.handleKey(runeKeyMsg("y"))
	updated, cmd := updated.(ChatModel).Update(control.InputBatchEndMsg{})
	if cmd == nil {
		t.Fatal("keys [\"y\"] did not answer when its batch ended")
	}
	decided := cmd().(components.ConfirmationDecidedMsg)
	if !decided.Approved || decided.Always {
		t.Fatalf("decided %#v", decided)
	}
	updated, deliver := updated.(ChatModel).Update(decided)
	applyDelivery(t, updated.(ChatModel), deliver)
	if len(c.decisions) != 1 {
		t.Errorf("decisions delivered = %v", c.decisions)
	}
}

// Enter straight after the letter answers without waiting.
func TestEnterAfterALetterAnswersAtOnce(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, _ := m.handleKey(runeKeyMsg("y"))
	_, cmd := updated.(ChatModel).handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd == nil {
		t.Fatal("Enter after y did not answer")
	}
	if decided := cmd().(components.ConfirmationDecidedMsg); !decided.Approved {
		t.Errorf("decided %#v", decided)
	}
}

// A hold that was typed over, or left by an earlier prompt, answers nothing.
func TestAStaleHoldAnswersNothing(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, first := m.handleKey(runeKeyMsg("y"))
	m = typeInto(t, updated.(ChatModel), "es")
	m, _ = press(t, m, tea.KeyBackspace)
	m, _ = press(t, m, tea.KeyBackspace)
	m, _ = press(t, m, tea.KeyBackspace)
	if _, cmd := m.Update(first()); cmd != nil {
		t.Error("a hold whose letter became text still answered")
	}

	// The same hold arriving after the prompt was replaced.
	updated, held := m.handleKey(runeKeyMsg("y"))
	m = updated.(ChatModel)
	next := gatedShellCall()
	next.ConfirmID = "cid-2"
	m = feed(t, m, next)
	updated, cmd := m.Update(held())
	if cmd != nil {
		t.Error("a hold from the earlier prompt answered the one that replaced it")
	}
	promptStillWaiting(t, updated.(ChatModel))
}

// "Always" is the widest grant, so its letter only highlights it.
func TestAlwaysNeedsEnter(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, cmd := m.handleKey(runeKeyMsg("a"))
	m = updated.(ChatModel)
	if cmd != nil {
		t.Fatal("a alone granted always")
	}
	promptStillWaiting(t, m)
	if !strings.Contains(m.View(), "enter to confirm") {
		t.Errorf("the highlighted choice must say Enter confirms it:\n%s", m.View())
	}

	updated, cmd = m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)
	decided, ok := cmd().(components.ConfirmationDecidedMsg)
	if !ok || !decided.Always {
		t.Fatalf("Enter on the highlighted always decided %#v", cmd())
	}
	updated, deliver := m.Update(decided)
	applyDelivery(t, updated.(ChatModel), deliver)
	if len(c.decisions) != 1 {
		t.Errorf("decisions delivered = %v", c.decisions)
	}
}

// Backspace takes the highlight back; it is not a deny.
func TestBackspaceClearsAHighlightedAlways(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, _ := m.handleKey(runeKeyMsg("a"))
	m, _ = press(t, updated.(ChatModel), tea.KeyBackspace)
	m, cmd := press(t, m, tea.KeyEnter)

	promptStillWaiting(t, m)
	if cmd != nil || m.confirmation.Chosen() {
		t.Error("Enter answered after the highlight was taken back")
	}
}

// With a message in the composer, Tab and Enter answer without a letter.
func TestTabThenEnterAnswersOverADraft(t *testing.T) {
	want := []struct {
		tabs     int
		approved bool
		always   bool
	}{{1, true, false}, {2, true, true}, {3, false, false}, {4, true, false}}
	for _, tc := range want {
		m, _ := liveModel(t)
		m.streaming = true
		m = typeInto(t, m, "draft")
		m = feed(t, m, gatedShellCall())

		for i := 0; i < tc.tabs; i++ {
			m, _ = press(t, m, tea.KeyTab)
		}
		m, cmd := press(t, m, tea.KeyEnter)
		if cmd == nil {
			t.Fatalf("%d tabs then Enter did not answer", tc.tabs)
		}
		decided := cmd().(components.ConfirmationDecidedMsg)
		if decided.Approved != tc.approved || decided.Always != tc.always {
			t.Errorf("%d tabs: decided %#v", tc.tabs, decided)
		}
		if m.input.Value() != "draft" {
			t.Errorf("answering changed the draft: %q", m.input.Value())
		}
	}
}

// Typing after a highlight takes it back: the Enter that follows a sentence is
// meant for the message.
func TestTypingClearsAHighlightedChoice(t *testing.T) {
	m, c := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	m, _ = press(t, m, tea.KeyTab)
	m = typeAndSettle(t, m, "wait, what does this do")
	m, cmd := press(t, m, tea.KeyEnter)

	promptStillWaiting(t, m)
	if cmd != nil || len(c.decisions) != 0 {
		t.Error("Enter after typing confirmed a choice highlighted before it")
	}
}

// Enter with nothing highlighted is not an answer, whatever was typed.
func TestEnterAloneDoesNotAnswer(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	m, cmd := press(t, m, tea.KeyEnter)
	if cmd != nil {
		t.Error("Enter answered a prompt with nothing highlighted")
	}
	promptStillWaiting(t, m)
}

// Esc still denies, with or without a draft.
func TestEscDeniesOverADraft(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = typeInto(t, m, "draft")
	m = feed(t, m, gatedShellCall())

	_, cmd := press(t, m, tea.KeyEsc)
	if cmd == nil {
		t.Fatal("Esc did not deny")
	}
	if decided := cmd().(components.ConfirmationDecidedMsg); decided.Approved {
		t.Errorf("Esc decided %#v", decided)
	}
}

// A paste over a prompt is a message, never an answer.
func TestPasteOverAPromptGoesToTheComposer(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = feed(t, m, gatedShellCall())

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("y"), Paste: true})
	m = updated.(ChatModel)
	if cmd != nil {
		t.Error("a pasted y answered the prompt")
	}
	promptStillWaiting(t, m)
	if m.input.Value() != "y" {
		t.Errorf("composer = %q", m.input.Value())
	}
}
