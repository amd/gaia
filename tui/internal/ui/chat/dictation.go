// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
	"github.com/amd/gaia/tui/internal/voice"
)

// Dictation: Ctrl+R or /dictate records the daemon machine's microphone and
// puts what was said into the composer at the cursor. Listening only — no
// turn, no speech. See docs/plans/voice.mdx › Dictation.

// dictationStopWait bounds the wait for the last transcript after stop: a
// commit with nothing left to transcribe may produce no final at all.
const dictationStopWait = 4 * time.Second

// voiceDialTimeout bounds opening a session: a daemon start, then the upgrade.
const voiceDialTimeout = 2 * time.Minute

type dictationState struct {
	id   int
	conn *voice.Conn
	// ready is false until the recognizer is connected.
	ready bool
	// partial is the utterance being heard right now.
	partial string
	// heard are finished segments: pauses don't end dictation.
	heard []string
	// stopping is set once commit went out; the next final ends it.
	stopping bool
	// missing is the speech model a model_missing error named; Ctrl+R then
	// downloads it instead of starting.
	missing     string
	downloading bool
}

type voiceConnMsg struct {
	id   int
	conn *voice.Conn
	err  error
}

// voiceEventMsg is one event from session id; ok is false once it closed.
type voiceEventMsg struct {
	id int
	ev voice.Event
	ok bool
}

type dictationStopTimeoutMsg struct{ id int }

type voiceDownloadedMsg struct {
	id     int
	models []string
	err    error
}

func dialVoice(id int, purpose string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), voiceDialTimeout)
		defer cancel()
		conn, err := voice.Dialer(ctx, purpose)
		return voiceConnMsg{id: id, conn: conn, err: err}
	}
}

func waitVoice(id int, conn *voice.Conn) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-conn.Events()
		return voiceEventMsg{id: id, ev: ev, ok: ok}
	}
}

func downloadVoiceModels(id int, models []string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
		defer cancel()
		for _, model := range models {
			if err := voice.DownloadModel(ctx, model); err != nil {
				return voiceDownloadedMsg{id: id, models: models, err: err}
			}
		}
		return voiceDownloadedMsg{id: id, models: models}
	}
}

// toggleDictation is Ctrl+R and /dictate.
func (m ChatModel) toggleDictation() (tea.Model, tea.Cmd) {
	d := m.dictation
	switch {
	case d == nil:
		return m.startDictation()
	case d.downloading:
		return m, nil
	case d.missing != "":
		d.downloading = true
		return m, downloadVoiceModels(d.id, []string{d.missing})
	default:
		return m.stopDictation()
	}
}

func (m ChatModel) startDictation() (tea.Model, tea.Cmd) {
	if m.voice != nil {
		return m, nil
	}
	m.voiceSeq++
	m.dictation = &dictationState{id: m.voiceSeq}
	return m, dialVoice(m.voiceSeq, voice.PurposeDictation)
}

// stopDictation asks for the last transcript; the final that answers it
// (or the stop timeout) inserts the text.
func (m ChatModel) stopDictation() (tea.Model, tea.Cmd) {
	d := m.dictation
	if d.conn == nil || !d.ready {
		// Still connecting: nothing was heard, so there is nothing to wait for.
		return m.endDictation(""), nil
	}
	if d.stopping {
		return m.finishDictation(), nil
	}
	d.stopping = true
	if err := d.conn.Commit(); err != nil {
		return m.finishDictation(), nil
	}
	id := d.id
	return m, tea.Tick(dictationStopWait, func(time.Time) tea.Msg { return dictationStopTimeoutMsg{id: id} })
}

// finishDictation inserts what was heard and closes the session.
func (m ChatModel) finishDictation() ChatModel {
	d := m.dictation
	text := strings.Join(strings.Fields(strings.Join(d.heard, " ")), " ")
	m = m.endDictation("")
	if text != "" {
		m.insertDictated(text)
	}
	return m
}

func (m ChatModel) endDictation(errText string) ChatModel {
	if d := m.dictation; d != nil && d.conn != nil {
		d.conn.Close()
	}
	m.dictation = nil
	if errText != "" {
		return m.errorNote(errText)
	}
	return m
}

// insertDictated puts text at the cursor, spaced from what is already there.
func (m *ChatModel) insertDictated(text string) {
	if v := m.input.Value(); v != "" && !strings.HasSuffix(v, " ") && !strings.HasSuffix(v, "\n") {
		text = " " + text
	}
	m.input.InsertString(text)
	m.syncComposerHeight()
}

// voiceMsgID is the session a voice message belongs to, or 0 for none.
func voiceMsgID(msg tea.Msg) int {
	switch msg := msg.(type) {
	case voiceConnMsg:
		return msg.id
	case voiceEventMsg:
		return msg.id
	case dictationStopTimeoutMsg:
		return msg.id
	case voiceDownloadedMsg:
		return msg.id
	case voiceOpenedMsg:
		return msg.id
	case voiceTickMsg:
		return msg.id
	}
	return 0
}

// routeVoiceMsg hands a voice message to dictation or voice mode, whichever
// owns its session. A late message from a session already gone is dropped,
// and a connection it carries is closed rather than leaked.
func (m ChatModel) routeVoiceMsg(msg tea.Msg) (tea.Model, tea.Cmd, bool) {
	id := voiceMsgID(msg)
	if id == 0 {
		return m, nil, false
	}
	switch {
	case m.dictation != nil && m.dictation.id == id:
		next, cmd := m.handleDictationMsg(msg)
		return next, cmd, true
	case m.voice != nil && m.voice.id == id:
		next, cmd := m.handleVoiceModeMsg(msg)
		return next, cmd, true
	}
	if c, ok := msg.(voiceConnMsg); ok && c.conn != nil {
		c.conn.Close()
	}
	return m, nil, true
}

func (m ChatModel) handleDictationMsg(msg tea.Msg) (tea.Model, tea.Cmd) {
	d := m.dictation
	switch msg := msg.(type) {
	case voiceConnMsg:
		if msg.err != nil {
			return m.endDictation("Dictation couldn't start: " + msg.err.Error()), nil
		}
		d.conn = msg.conn
		return m, waitVoice(d.id, d.conn)

	case voiceEventMsg:
		if !msg.ok {
			if d.stopping || len(d.heard) > 0 {
				return m.finishDictation(), nil
			}
			return m.endDictation(""), nil
		}
		return m.dictationEvent(msg.ev)

	case dictationStopTimeoutMsg:
		return m.finishDictation(), nil

	case voiceDownloadedMsg:
		if msg.err != nil {
			return m.endDictation("Couldn't download the speech model: " + msg.err.Error()), nil
		}
		// The model is here: start the dictation it was downloaded for.
		m.dictation = nil
		return m.startDictation()
	}
	return m, nil
}

func (m ChatModel) dictationEvent(ev voice.Event) (tea.Model, tea.Cmd) {
	d := m.dictation
	wait := waitVoice(d.id, d.conn)
	switch ev.Type {
	case "ready":
		d.ready = true
	case "partial":
		d.partial = ev.Text
	case "final":
		d.partial = ""
		if t := strings.TrimSpace(ev.Text); t != "" {
			d.heard = append(d.heard, t)
		}
		if d.stopping {
			return m.finishDictation(), nil
		}
	case "limit":
		m = m.statusNote(fmt.Sprintf("Dictation stops after %d minutes; what you said is in the composer.", ev.Seconds/60))
		d.stopping = true
	case "error":
		if ev.Code == "model_missing" && ev.Detail != "" {
			// Kept open as a one-key offer: Ctrl+R downloads, Esc dismisses.
			d.conn.Close()
			d.conn = nil
			d.ready = false
			d.missing = ev.Detail
			return m, nil
		}
		heard := d.heard
		m = m.endDictation(voice.ErrorText(ev.Code, ev.Message))
		if text := strings.Join(heard, " "); text != "" {
			m.insertDictated(text)
		}
		return m, nil
	case "closed":
		if d.stopping || len(d.heard) > 0 {
			return m.finishDictation(), nil
		}
		return m.endDictation(""), nil
	}
	return m, wait
}

var (
	dictationMarkStyle = lipgloss.NewStyle().Foreground(theme.Danger).Bold(true)
	dictationTextStyle = lipgloss.NewStyle().Foreground(theme.Text)
	dictationHintStyle = lipgloss.NewStyle().Foreground(theme.Dim)
)

// renderDictationBorder replaces the composer's top border while dictating:
// the recording mark, how to stop, and the words being heard, dimmed.
func (m ChatModel) renderDictationBorder() string {
	d := m.dictation
	var label, tail string
	switch {
	case d.downloading:
		label = "↓ Downloading " + d.missing + "…"
	case d.missing != "":
		label = "● " + d.missing + " isn't downloaded — Ctrl+R downloads it, Esc cancels"
	case !d.ready:
		label = "● Starting the microphone…"
	case d.stopping:
		label = "● Finishing — the text goes in the composer"
	default:
		label = "● Listening — Ctrl+R or Esc to stop"
		heard := strings.Join(append(append([]string{}, d.heard...), d.partial), " ")
		tail = strings.TrimSpace(heard)
	}
	head := "── "
	mark, text, _ := strings.Cut(label, " ")
	text = truncateRunes(text, max(m.width-lipgloss.Width(head)-lipgloss.Width(mark)-2, 1))
	used := lipgloss.Width(head) + lipgloss.Width(mark) + 1 + lipgloss.Width(text)
	row := dividerStyle.Render(head) + dictationMarkStyle.Render(mark) + " " +
		dictationTextStyle.Render(text)
	if tail != "" && m.width-used > 6 {
		// The newest words matter; older ones fall off the left edge.
		tail = clipLeft(tail, m.width-used-3)
		row += dictationHintStyle.Render(" · " + tail)
		used += 3 + lipgloss.Width(tail)
	}
	if fill := m.width - used; fill > 0 {
		row += dividerStyle.Render(" " + strings.Repeat("─", fill-1))
	}
	return row
}

// clipLeft keeps the last width columns of s, marking the cut with "…".
func clipLeft(s string, width int) string {
	if width <= 0 {
		return ""
	}
	if lipgloss.Width(s) <= width {
		return s
	}
	r := []rune(s)
	for len(r) > 0 && lipgloss.Width(string(r))+1 > width {
		r = r[1:]
	}
	return "…" + string(r)
}
