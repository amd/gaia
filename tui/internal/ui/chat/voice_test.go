// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/voice"
	"github.com/amd/gaia/tui/internal/voice/voicetest"
)

// teaLoop is a small Bubble Tea loop: commands run on goroutines and their
// messages come back through Update, so a test can wait on state instead of
// sleeping.
type teaLoop struct {
	t    *testing.T
	m    ChatModel
	msgs chan tea.Msg
}

func newRuntime(t *testing.T, m ChatModel) *teaLoop {
	return &teaLoop{t: t, m: m, msgs: make(chan tea.Msg, 256)}
}

func (r *teaLoop) run(cmd tea.Cmd) {
	if cmd == nil {
		return
	}
	go func() {
		if msg := cmd(); msg != nil {
			r.msgs <- msg
		}
	}()
}

func (r *teaLoop) send(msg tea.Msg) {
	next, cmd := r.m.Update(msg)
	r.m = next.(ChatModel)
	r.run(cmd)
}

func (r *teaLoop) until(what string, cond func(ChatModel) bool) {
	r.t.Helper()
	deadline := time.After(10 * time.Second)
	for !cond(r.m) {
		select {
		case msg := <-r.msgs:
			if batch, ok := msg.(tea.BatchMsg); ok {
				for _, c := range batch {
					r.run(c)
				}
				continue
			}
			r.send(msg)
		case <-deadline:
			r.t.Fatalf("timed out waiting for %s", what)
		}
	}
}

func vkey(s string) tea.KeyMsg {
	switch s {
	case "esc":
		return tea.KeyMsg{Type: tea.KeyEsc}
	case "ctrl+r":
		return tea.KeyMsg{Type: tea.KeyCtrlR}
	case "enter":
		return tea.KeyMsg{Type: tea.KeyEnter}
	case " ":
		return tea.KeyMsg{Type: tea.KeySpace, Runes: []rune(" ")}
	}
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

func useVoiceServer(t *testing.T) *voicetest.Server {
	t.Helper()
	srv := voicetest.New(t)
	prev := voice.Dialer
	voice.Dialer = srv.Dialer()
	t.Cleanup(func() { voice.Dialer = prev })
	return srv
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// ---------------------------------------------------------------- dictation

func TestDictationInsertsTheTranscriptAtTheCursor(t *testing.T) {
	srv := useVoiceServer(t)
	srv.CommitText = "and the second part"
	m, _ := newTestModel(t)
	m.input.SetValue("Note:")
	r := newRuntime(t, m)

	r.send(vkey("ctrl+r"))
	r.until("dictation to be listening", func(m ChatModel) bool { return m.dictation != nil && m.dictation.ready })
	srv.Push(voice.Event{Type: "partial", Text: "first words"})
	r.until("the partial", func(m ChatModel) bool { return m.dictation.partial == "first words" })

	view := ansi.Strip(r.m.View())
	if !strings.Contains(view, "● Listening — Ctrl+R or Esc to stop") || !strings.Contains(view, "first words") {
		t.Fatalf("the composer border doesn't show dictation:\n%s", view)
	}

	// A pause ends a segment, not the dictation.
	srv.Push(voice.Event{Type: "final", Text: "first words"})
	r.until("the segment", func(m ChatModel) bool { return len(m.dictation.heard) == 1 })

	r.send(vkey("ctrl+r"))
	r.until("dictation to end", func(m ChatModel) bool { return m.dictation == nil })
	if got := r.m.input.Value(); got != "Note: first words and the second part" {
		t.Fatalf("composer = %q", got)
	}
	waitFor(t, "the session to stop", func() bool { return srv.Saw("commit") && srv.Saw("stop") })
	if view := ansi.Strip(r.m.View()); strings.Contains(view, "Listening") {
		t.Fatalf("the border still says Listening after stop:\n%s", view)
	}
}

func TestEscStopsDictationWithoutClearingTheComposer(t *testing.T) {
	srv := useVoiceServer(t)
	srv.CommitText = "dictated"
	m, _ := newTestModel(t)
	m.input.SetValue("typed")
	r := newRuntime(t, m)
	r.send(vkey("ctrl+r"))
	r.until("ready", func(m ChatModel) bool { return m.dictation != nil && m.dictation.ready })
	r.send(vkey("esc"))
	r.until("dictation to end", func(m ChatModel) bool { return m.dictation == nil })
	if got := r.m.input.Value(); got != "typed dictated" {
		t.Fatalf("composer = %q", got)
	}
}

func TestDictateCommandTogglesLikeCtrlR(t *testing.T) {
	useVoiceServer(t)
	m, _ := newTestModel(t)
	r := newRuntime(t, m)
	next, cmd := r.m.submit("/dictate")
	r.m = next.(ChatModel)
	r.run(cmd)
	r.until("ready", func(m ChatModel) bool { return m.dictation != nil && m.dictation.ready })
}

func TestDictationMissingModelOffersADownload(t *testing.T) {
	srv := useVoiceServer(t)
	var pulled []string
	prev := voice.DownloadModel
	voice.DownloadModel = func(_ context.Context, model string) error {
		pulled = append(pulled, model)
		return nil
	}
	t.Cleanup(func() { voice.DownloadModel = prev })

	m, _ := newTestModel(t)
	r := newRuntime(t, m)
	r.send(vkey("ctrl+r"))
	r.until("ready", func(m ChatModel) bool { return m.dictation != nil && m.dictation.ready })
	srv.Push(voice.Event{Type: "error", Code: "model_missing", Message: "Voice needs the speech model.", Detail: "Moonshine-Tiny-Streaming"})
	r.until("the offer", func(m ChatModel) bool { return m.dictation != nil && m.dictation.missing != "" })
	if view := ansi.Strip(r.m.View()); !strings.Contains(view, "Ctrl+R downloads it") {
		t.Fatalf("no download offer on the border:\n%s", view)
	}
	r.send(vkey("ctrl+r"))
	r.until("dictation to restart", func(m ChatModel) bool {
		return m.dictation != nil && m.dictation.missing == "" && m.dictation.ready
	})
	if len(pulled) != 1 || pulled[0] != "Moonshine-Tiny-Streaming" {
		t.Fatalf("pulled %v", pulled)
	}
}

// ---------------------------------------------------------------- voice mode

// voiceClient is a flagship that can answer aloud and replies with script.
type voiceClient struct {
	mu      sync.Mutex
	script  []interface{}
	styles  []string
	queries []string
	refuse  error
}

func (c *voiceClient) Send(ctx context.Context, q string) (<-chan interface{}, error) {
	c.mu.Lock()
	c.styles = append(c.styles, client.ReplyStyleFrom(ctx))
	c.queries = append(c.queries, q)
	script := c.script
	c.mu.Unlock()
	ch := make(chan interface{}, len(script))
	for _, e := range script {
		ch <- e
	}
	close(ch)
	return ch, nil
}
func (c *voiceClient) Close() error                                 { return nil }
func (c *voiceClient) CheckVoiceReplies(ctx context.Context) error { return c.refuse }

func readyStatus() voice.Status {
	return voice.Status{Ready: true, Speaks: true, Captions: true, StatusUpdates: true,
		TurnTaking: "hands_free", Microphone: voice.Microphone{Available: true, Name: "Desk mic"}}
}

func useStatus(t *testing.T, st voice.Status) {
	prev := voice.FetchStatus
	voice.FetchStatus = func(context.Context) (voice.Status, error) { return st, nil }
	t.Cleanup(func() { voice.FetchStatus = prev })
}

func voiceModel(t *testing.T, c *voiceClient) *teaLoop {
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30
	r := newRuntime(t, m)
	next, cmd := r.m.submit("/talk")
	r.m = next.(ChatModel)
	r.run(cmd)
	return r
}

func listening(m ChatModel) bool { return m.voice != nil && m.voice.phase == voiceListening }

func TestVoiceTurnSpeaksTheSummaryAndStatusLines(t *testing.T) {
	srv := useVoiceServer(t)
	useStatus(t, readyStatus())
	answer := "<spoken>You have two meetings today.</spoken>\n\nThe **first** is at 9."
	c := &voiceClient{script: []interface{}{
		event.CanonicalTokenEvent{Type: "token", Delta: "Let me check your calendar."},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "calendar", Narration: "Checking your calendar for today"},
		event.CanonicalTokenEvent{Type: "token", Delta: "<spoken>You have two"},
		event.CanonicalTokenEvent{Type: "token", Delta: " meetings today.</spoken>\n\nThe **first** is at 9."},
		event.CanonicalFinalEvent{Type: "final", Answer: answer},
	}}
	r := voiceModel(t, c)
	r.until("listening", listening)
	if word, _, _ := r.m.voiceStateWord(); word != "Listening" {
		t.Fatalf("state = %q", word)
	}

	srv.Push(voice.Event{Type: "final", Text: "what's on today"})
	r.until("the turn to end", func(m ChatModel) bool { return m.voice != nil && !m.voice.turn && !m.streaming && len(c.queries) == 1 })
	waitFor(t, "speak_end", func() bool { return srv.Saw("speak_end") })

	if c.styles[0] != client.ReplyStyleVoice || c.queries[0] != "what's on today" {
		t.Fatalf("sent %q with style %q", c.queries[0], c.styles[0])
	}
	if got := srv.Spoken(voice.KindSpoken); got != "You have two meetings today." {
		t.Fatalf("spoken = %q (narration before the tool must not be said)", got)
	}
	if got := srv.Spoken(voice.KindStatus); got != "Checking your calendar for today" {
		t.Fatalf("status = %q", got)
	}

	// The chat shows the summary as a lead line, never the tags.
	r.m.voice = nil
	r.m.updateViewport()
	view := ansi.Strip(r.m.View())
	if strings.Contains(view, "<spoken>") || !strings.Contains(view, "You have two meetings today.") {
		t.Fatalf("chat history rendering:\n%s", view)
	}
	var stored string
	for _, msg := range r.m.messages {
		if msg.Role == RoleAssistant {
			stored = msg.Content
		}
	}
	if stored != answer {
		t.Fatalf("the stored answer lost its tags: %q", stored)
	}
}

func TestVoiceTurnWithoutASpokenBlockSaysTheFixedLine(t *testing.T) {
	srv := useVoiceServer(t)
	useStatus(t, readyStatus())
	c := &voiceClient{script: []interface{}{
		event.CanonicalFinalEvent{Type: "final", Answer: "Just text."},
	}}
	r := voiceModel(t, c)
	r.until("listening", listening)
	srv.Push(voice.Event{Type: "final", Text: "hi"})
	r.until("the turn to end", func(m ChatModel) bool { return len(c.queries) == 1 && !m.voice.turn && !m.streaming })
	waitFor(t, "speak_end", func() bool { return srv.Saw("speak_end") })
	if got := srv.Spoken(voice.KindSpoken); got != voice.MissingSpokenLine {
		t.Fatalf("spoken = %q", got)
	}
}

func TestAnyKeyWhileSpeakingInterrupts(t *testing.T) {
	srv := useVoiceServer(t)
	useStatus(t, readyStatus())
	r := voiceModel(t, &voiceClient{})
	r.until("listening", listening)
	srv.Push(voice.Event{Type: "speaking", Kind: "spoken"})
	r.until("speaking", func(m ChatModel) bool { return m.voice.speaking })
	if word, _, _ := r.m.voiceStateWord(); word != "Speaking" {
		t.Fatalf("state = %q", word)
	}
	r.send(vkey("x"))
	if r.m.voice.speaking {
		t.Fatal("still speaking after a key")
	}
	waitFor(t, "interrupt", func() bool { return srv.Saw("interrupt") })
	if r.m.voice == nil {
		t.Fatal("the interrupting key also left voice mode")
	}
}

func TestVoiceKeysPauseCaptionsAndLeave(t *testing.T) {
	srv := useVoiceServer(t)
	useStatus(t, readyStatus())
	r := voiceModel(t, &voiceClient{})
	r.until("listening", listening)

	r.send(vkey("c"))
	if r.m.voice.captions {
		t.Fatal("c did not hide captions")
	}
	r.send(vkey("p"))
	if r.m.voice.phase != voicePaused {
		t.Fatalf("phase = %v after p", r.m.voice.phase)
	}
	if word, _, moving := r.m.voiceStateWord(); word != "Paused" || moving {
		t.Fatalf("paused state = %q (shimmer %t)", word, moving)
	}
	waitFor(t, "the session to close on pause", func() bool { return srv.Saw("stop") })
	r.send(vkey("p"))
	r.until("listening again", listening)
	if r.m.voice.captions {
		t.Fatal("resuming reset the captions toggle")
	}
	r.send(vkey("esc"))
	if r.m.voice != nil {
		t.Fatal("Esc did not leave voice mode")
	}
}

func TestPushToTalkSpaceOpensAndSendsTheUtterance(t *testing.T) {
	srv := useVoiceServer(t)
	st := readyStatus()
	st.TurnTaking = "push_to_talk"
	useStatus(t, st)
	r := voiceModel(t, &voiceClient{})
	r.until("listening", listening)
	if word, _, _ := r.m.voiceStateWord(); !strings.Contains(word, "Space") {
		t.Fatalf("push-to-talk state = %q", word)
	}
	r.send(vkey(" "))
	r.send(vkey(" "))
	waitFor(t, "ptt", func() bool { return srv.Saw("ptt_down") && srv.Saw("ptt_up") })
}

func TestVoiceModeRefusesAnOldAgent(t *testing.T) {
	useVoiceServer(t)
	useStatus(t, readyStatus())
	r := voiceModel(t, &voiceClient{refuse: errors.New("Voice mode needs a newer GAIA agent: the installed 'gaia' agent speaks contract 2.27")})
	r.until("the refusal", func(m ChatModel) bool { return m.voice == nil })
	last := r.m.messages[len(r.m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "newer GAIA agent") {
		t.Fatalf("last message = %+v", last)
	}
}

func TestVoiceModeOffersTheMissingModels(t *testing.T) {
	useVoiceServer(t)
	st := readyStatus()
	st.Ready, st.ErrorCode, st.Error = false, "model_missing", "Voice needs speech models: kokoro-v1"
	st.Models = []voice.Model{{ID: "kokoro-v1", Purpose: "tts"}}
	useStatus(t, st)
	var pulled []string
	prev := voice.DownloadModel
	voice.DownloadModel = func(_ context.Context, model string) error {
		pulled = append(pulled, model)
		useStatus(t, readyStatus())
		return nil
	}
	t.Cleanup(func() { voice.DownloadModel = prev })

	r := voiceModel(t, &voiceClient{})
	r.until("the failure", func(m ChatModel) bool { return m.voice != nil && m.voice.phase == voiceFailed })
	if view := ansi.Strip(r.m.View()); !strings.Contains(view, "d download") || !strings.Contains(view, "kokoro-v1") {
		t.Fatalf("no download offer:\n%s", view)
	}
	r.send(vkey("d"))
	r.until("listening after the download", listening)
	if len(pulled) != 1 || pulled[0] != "kokoro-v1" {
		t.Fatalf("pulled %v", pulled)
	}
}

func TestVoiceViewFillsTheWindowExactly(t *testing.T) {
	useVoiceServer(t)
	useStatus(t, readyStatus())
	r := voiceModel(t, &voiceClient{})
	r.until("listening", listening)
	r.m.voice.heard = strings.Repeat("a long caption ", 40)
	for _, size := range [][2]int{{100, 30}, {60, 12}, {40, 8}} {
		r.m.width, r.m.height = size[0], size[1]
		view := r.m.View()
		lines := strings.Split(view, "\n")
		if len(lines) != size[1] {
			t.Errorf("%dx%d: %d rows", size[0], size[1], len(lines))
		}
		for i, line := range lines {
			if w := ansi.StringWidth(line); w > size[0] {
				t.Errorf("%dx%d: row %d is %d wide", size[0], size[1], i, w)
			}
		}
	}
}

func TestShimmerMovesOnlyWhileActive(t *testing.T) {
	a := ansi.Strip(shimmer("Listening", nil, 1, true))
	if a != "Listening" {
		t.Fatalf("shimmer changed the word: %q", a)
	}
	if shimmer("Paused", nil, 1, false) != shimmer("Paused", nil, 5, false) {
		t.Fatal("a still state shimmered")
	}
}
