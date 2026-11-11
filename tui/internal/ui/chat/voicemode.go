// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"math"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/theme"
	"github.com/amd/gaia/tui/internal/voice"
)

// Voice mode: /talk opens a full-screen view on this same conversation. The
// daemon listens and speaks; each turn is an ordinary chat turn sent with
// reply_style "voice", and its <spoken> summary is handed back to the daemon
// to say. See docs/plans/voice.mdx › Voice mode › TUI screen.

// voiceTickEvery paces the shimmer and the meter's decay.
const voiceTickEvery = 90 * time.Millisecond

// voiceOpenTimeout bounds the agent-version check and the status read.
const voiceOpenTimeout = 90 * time.Second

type voicePhase int

const (
	voiceWarming voicePhase = iota
	voiceListening
	voicePaused
	voiceFailed
)

// Spoken while the turn is parked on the user; the card on screen takes the answer.
const (
	voiceConfirmLine  = "I need your OK before the next step, it's on screen."
	voiceQuestionLine = "I have a question for you, it's on screen."
)

type voiceMode struct {
	id     int
	conn   *voice.Conn
	status voice.Status
	phase  voicePhase
	// speaking is set from the daemon's speaking events until speaking_done
	// or an interrupt.
	speaking bool
	// turn is a voice turn in flight; workFrom is len(messages) when it began,
	// so the work log can find that turn's record once it is committed.
	turn     bool
	workFrom int
	splitter voice.SpokenSplitter
	// fed: the splitter has seen this answer's tokens (else final's answer
	// is fed whole).
	fed bool
	// speech is false when the daemon can't speak (a non-English language).
	speech   bool
	captions bool
	// captionsChosen: the user toggled captions, so the setting no longer applies.
	captionsChosen bool
	// talking is push-to-talk held down (Space toggles it).
	talking bool
	level   float64
	frame   int
	heard   string // the user's words, partial or final
	said    []string
	notice  string
	// errText/errCode describe voiceFailed; missing lists the models a
	// model_missing failure can download with one key.
	errText     string
	errCode     string
	missing     []string
	downloading bool
}

type voiceOpenedMsg struct {
	id     int
	status voice.Status
	// refused means the agent can't give spoken replies: voice mode closes.
	refused error
	err     error
}

type voiceTickMsg struct{ id int }

func voiceTick(id int) tea.Cmd {
	return tea.Tick(voiceTickEvery, func(time.Time) tea.Msg { return voiceTickMsg{id: id} })
}

// openVoiceMode is /talk.
func (m ChatModel) openVoiceMode() (tea.Model, tea.Cmd) {
	if m.voice != nil {
		return m, nil
	}
	replier, ok := m.client.(client.VoiceReplier)
	if !ok {
		return m.errorNote("Voice mode needs the GAIA agent running through the GAIA " +
			"background service; this session talks to " + m.agentName + " directly."), nil
	}
	if m.dictation != nil {
		m = m.endDictation("")
	}
	m.palette.open = false
	m.voiceSeq++
	m.voice = &voiceMode{id: m.voiceSeq, phase: voiceWarming, captions: true, speech: true}
	id := m.voiceSeq
	return m, tea.Batch(voiceTick(id), func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), voiceOpenTimeout)
		defer cancel()
		if err := replier.CheckVoiceReplies(ctx); err != nil {
			return voiceOpenedMsg{id: id, refused: err}
		}
		st, err := voice.FetchStatus(ctx)
		return voiceOpenedMsg{id: id, status: st, err: err}
	})
}

// closeVoiceMode leaves the screen. A turn still running carries on in chat.
func (m ChatModel) closeVoiceMode() ChatModel {
	if m.voice != nil && m.voice.conn != nil {
		m.voice.conn.Close()
	}
	m.voice = nil
	m.updateViewport()
	return m
}

func (m ChatModel) voiceFail(code, text string) ChatModel {
	v := m.voice
	if v.conn != nil {
		v.conn.Close()
		v.conn = nil
	}
	v.phase = voiceFailed
	v.speaking, v.talking = false, false
	v.errCode, v.errText = code, text
	return m
}

func (m ChatModel) handleVoiceModeMsg(msg tea.Msg) (tea.Model, tea.Cmd) {
	v := m.voice
	switch msg := msg.(type) {
	case voiceOpenedMsg:
		if msg.refused != nil {
			m = m.closeVoiceMode()
			return m.errorNote(msg.refused.Error()), nil
		}
		if msg.err != nil {
			return m.voiceFail("session_lost", "Couldn't check whether voice can start: "+msg.err.Error()), nil
		}
		v.status = msg.status
		if !v.captionsChosen {
			v.captions = msg.status.Captions || !msg.status.Speaks
		}
		if !msg.status.Ready {
			m = m.voiceFail(msg.status.ErrorCode, voice.ErrorText(msg.status.ErrorCode, msg.status.Error))
			if msg.status.ErrorCode == "model_missing" {
				for _, mm := range msg.status.Missing() {
					v.missing = append(v.missing, mm.ID)
				}
			}
			return m, nil
		}
		return m, dialVoice(v.id, voice.PurposeVoice)

	case voiceConnMsg:
		if msg.err != nil {
			return m.voiceFail("session_lost", "Voice couldn't start: "+msg.err.Error()), nil
		}
		v.conn = msg.conn
		return m, waitVoice(v.id, v.conn)

	case voiceEventMsg:
		if !msg.ok {
			if v.phase != voicePaused && v.phase != voiceFailed {
				return m.voiceFail("session_lost", voice.ErrorText("session_lost", "")), nil
			}
			return m, nil
		}
		next, cmd := m.voiceEvent(msg.ev)
		if next.voice == nil || next.voice.conn == nil {
			return next, cmd
		}
		return next, tea.Batch(cmd, waitVoice(v.id, next.voice.conn))

	case voiceTickMsg:
		if v.phase == voicePaused || v.phase == voiceFailed {
			return m, nil
		}
		v.frame++
		v.level *= 0.75
		return m, voiceTick(v.id)

	case voiceDownloadedMsg:
		v.downloading = false
		if msg.err != nil {
			return m.voiceFail("model_missing", "Couldn't download the speech models: "+msg.err.Error()), nil
		}
		return m.reopenVoice()
	}
	return m, nil
}

// reopenVoice starts the screen over on the same session id's successor.
func (m ChatModel) reopenVoice() (tea.Model, tea.Cmd) {
	captions, chosen := m.voice.captions, m.voice.captionsChosen
	m = m.closeVoiceMode()
	next, cmd := m.openVoiceMode()
	if nm, ok := next.(ChatModel); ok && nm.voice != nil {
		nm.voice.captions, nm.voice.captionsChosen = captions, chosen
		return nm, cmd
	}
	return next, cmd
}

func (m ChatModel) voiceEvent(ev voice.Event) (ChatModel, tea.Cmd) {
	v := m.voice
	switch ev.Type {
	case "ready":
		v.phase = voiceListening
		v.speech = ev.Speech
		if !ev.Speech {
			v.notice = "Spoken replies are English-only for now. GAIA will answer on screen."
		}
	case "partial":
		v.heard = ev.Text
	case "speech_started":
		v.notice = ""
	case "final":
		text := strings.TrimSpace(ev.Text)
		if text == "" {
			break
		}
		v.heard = text
		v.talking = false
		if m.streaming {
			v.notice = "Heard you, but GAIA is still on the last request. Say it again once it's listening."
			break
		}
		return m.startVoiceTurn(text)
	case "level":
		v.level = math.Max(v.level, ev.Value)
	case "speaking":
		v.speaking = true
	case "spoken_piece":
		v.said = append(v.said, ev.Text)
	case "speaking_done", "speech_stopped_by_user":
		v.speaking = false
	case "limit":
		v.notice = "That was a long one, so GAIA stopped listening to it."
	case "error":
		if ev.Code == "tts_failed" {
			v.speaking = false
			v.notice = voice.ErrorText(ev.Code, ev.Message)
			break
		}
		m = m.voiceFail(ev.Code, voice.ErrorText(ev.Code, ev.Message))
		if ev.Code == "model_missing" && ev.Detail != "" {
			m.voice.missing = []string{ev.Detail}
		}
	case "closed":
		if v.phase != voicePaused && v.phase != voiceFailed {
			m = m.voiceFail("session_lost", voice.ErrorText("session_lost", ""))
		}
	}
	return m, nil
}

// startVoiceTurn sends what was heard as an ordinary chat turn, asked to
// answer aloud.
func (m ChatModel) startVoiceTurn(text string) (ChatModel, tea.Cmd) {
	v := m.voice
	v.turn = true
	v.workFrom = len(m.messages)
	v.splitter.Reset()
	v.fed = false
	v.said = nil
	v.notice = ""
	m.nextReplyStyle = client.ReplyStyleVoice
	next, cmd := m.sendQuery(text)
	return next.(ChatModel), cmd
}

// observeVoiceTurn reads the running voice turn's events on their way to the
// chat: spoken text goes to the daemon to be said as it streams.
func (m *ChatModel) observeVoiceTurn(evt interface{}) {
	v := m.voice
	if v == nil || !v.turn || v.conn == nil || !v.speech {
		return
	}
	switch e := evt.(type) {
	case event.CanonicalTokenEvent:
		v.fed = true
		m.speakPieces(v.splitter.Feed(e.Delta))
	case event.CanonicalToolCallEvent:
		// What streamed before a tool call was narration, not the answer.
		v.splitter.Reset()
		v.fed = false
		if v.status.StatusUpdates && strings.TrimSpace(e.Narration) != "" {
			_ = v.conn.Speak(e.Narration, voice.KindStatus)
		}
	case event.CanonicalNeedsConfirmationEvent:
		_ = v.conn.Speak(voiceConfirmLine, voice.KindStatus)
	case event.CanonicalNeedsInputEvent:
		_ = v.conn.Speak(voiceQuestionLine, voice.KindStatus)
	case event.CanonicalFinalEvent:
		if !v.fed {
			m.speakPieces(v.splitter.Feed(e.Answer))
			v.fed = true
		}
	}
}

func (m *ChatModel) speakPieces(pieces []voice.Piece) {
	for _, p := range pieces {
		if p.Channel == voice.Spoken {
			if err := m.voice.conn.Speak(p.Text, voice.KindSpoken); err != nil {
				*m = m.voiceFail("session_lost", "Lost the connection to speech: "+err.Error())
				return
			}
		}
	}
}

// settleVoiceTurn runs once the voice turn stopped streaming, whichever way
// it ended: the rest of the summary is said, or the fixed line when the
// answer had none.
func (m *ChatModel) settleVoiceTurn() {
	v := m.voice
	if v == nil || !v.turn || m.streaming {
		return
	}
	v.turn = false
	if v.conn == nil || !v.speech {
		return
	}
	m.speakPieces(v.splitter.Finish())
	if m.voice.conn == nil {
		return
	}
	failed := false
	for _, msg := range m.messages[min(v.workFrom, len(m.messages)):] {
		if msg.Role == RoleError {
			failed = true
			v.notice = clean(msg.Content)
		}
	}
	if !failed && v.splitter.SpokenText() == "" {
		_ = v.conn.Speak(voice.MissingSpokenLine, voice.KindSpoken)
	}
	_ = v.conn.SpeakEnd()
}

func (m ChatModel) handleVoiceKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	v := m.voice
	if msg.Type == tea.KeyEsc {
		return m.closeVoiceMode(), nil
	}
	if v.speaking && v.conn != nil {
		// Any key is barge-in: the daemon's mic is held while it speaks.
		_ = v.conn.Interrupt()
		v.speaking = false
		return m, nil
	}
	switch v.phase {
	case voiceFailed:
		switch {
		case msg.String() == "d" && len(v.missing) > 0 && !v.downloading:
			v.downloading = true
			v.errText = "Downloading " + strings.Join(v.missing, ", ") + "…"
			return m, downloadVoiceModels(v.id, v.missing)
		case msg.Type == tea.KeyEnter || msg.String() == "r":
			return m.reopenVoice()
		}
		return m, nil
	case voicePaused:
		if msg.String() == "p" || msg.Type == tea.KeyEnter {
			return m.reopenVoice()
		}
		return m, nil
	}
	switch msg.String() {
	case "p":
		if v.conn != nil {
			v.conn.Close()
			v.conn = nil
		}
		v.phase = voicePaused
		v.speaking, v.talking = false, false
		return m, nil
	case "c":
		v.captions = !v.captions
		v.captionsChosen = true
		return m, nil
	case " ":
		if v.status.HandsFree() || v.conn == nil || v.phase != voiceListening {
			return m, nil
		}
		v.talking = !v.talking
		if v.talking {
			_ = v.conn.TalkDown()
		} else {
			_ = v.conn.TalkUp()
		}
		return m, nil
	}
	return m, nil
}

// voiceStateWord is what the state line says, and the colour it says it in.
func (m ChatModel) voiceStateWord() (string, lipgloss.TerminalColor, bool) {
	v := m.voice
	switch {
	case v.phase == voiceFailed:
		return "Stopped", theme.Danger, false
	case v.phase == voicePaused:
		return "Paused", theme.Dim, false
	case v.phase == voiceWarming || v.conn == nil:
		return "Warming up", theme.Dim, true
	case v.speaking:
		return "Speaking", theme.Warning, true
	case m.streaming:
		return "Thinking", theme.Warning, true
	case !v.status.HandsFree() && !v.talking:
		return "Ready — Space to talk", theme.Info, false
	}
	return "Listening", theme.Info, true
}

// shimmer sweeps a highlight across word, one rune per frame.
func shimmer(word string, color lipgloss.TerminalColor, frame int, moving bool) string {
	base := lipgloss.NewStyle().Foreground(color).Bold(true)
	if !moving {
		return base.Render(word)
	}
	runes := []rune(word)
	at := frame % (len(runes) + 6)
	hi := lipgloss.NewStyle().Foreground(theme.Text).Bold(true)
	near := lipgloss.NewStyle().Foreground(theme.AccentBright).Bold(true)
	var b strings.Builder
	for i, r := range runes {
		switch d := i - at; {
		case d == 0:
			b.WriteString(hi.Render(string(r)))
		case d == -1 || d == 1:
			b.WriteString(near.Render(string(r)))
		default:
			b.WriteString(base.Render(string(r)))
		}
	}
	return b.String()
}

const voiceMeterCells = 16

var meterBlocks = []rune(" ▁▂▃▄▅▆▇█")

// voiceMeter is the level as a row of block characters with a little motion.
func voiceMeter(level float64, frame int, color lipgloss.TerminalColor) string {
	var b strings.Builder
	for i := 0; i < voiceMeterCells; i++ {
		wave := 0.65 + 0.35*math.Sin(float64(frame)*0.8+float64(i)*0.9)
		h := level * wave
		idx := int(math.Round(h * float64(len(meterBlocks)-1)))
		idx = max(1, min(idx, len(meterBlocks)-1))
		b.WriteRune(meterBlocks[idx])
	}
	return lipgloss.NewStyle().Foreground(color).Render(b.String())
}

var (
	voiceLabelStyle  = lipgloss.NewStyle().Foreground(theme.Dim).Bold(true)
	voiceSaidStyle   = lipgloss.NewStyle().Foreground(theme.Text)
	voiceHeardStyle  = lipgloss.NewStyle().Foreground(theme.Dim)
	voiceNoticeStyle = lipgloss.NewStyle().Foreground(theme.Warning)
	voiceErrorStyle  = lipgloss.NewStyle().Foreground(theme.Danger)
)

// voiceView draws the whole screen, exactly m.height rows.
func (m ChatModel) voiceView() string {
	v := m.voice
	width := max(m.width, 20)
	textWidth := max(width-4, 10)
	word, color, moving := m.voiceStateWord()
	state := "  " + shimmer(word, color, v.frame, moving)
	if v.phase == voiceListening || v.phase == voiceWarming {
		state += "   " + voiceMeter(v.level, v.frame, color)
	}
	if v.status.Microphone.Name != "" && v.phase != voiceFailed {
		state += "  " + activityStyle.Render(truncateRunes(v.status.Microphone.Name, max(textWidth-30, 8)))
	}

	top := []string{headerStyle.Render("GAIA voice"), state, ""}
	if v.phase == voiceFailed {
		for _, line := range strings.Split(components.WrapText(v.errText, textWidth), "\n") {
			top = append(top, "  "+voiceErrorStyle.Render(line))
		}
		top = append(top, "")
	}
	if v.captions {
		if v.heard != "" {
			top = append(top, captionRows("You", v.heard, textWidth, voiceHeardStyle)...)
		}
		if said := strings.TrimSpace(strings.Join(v.said, " ")); said != "" {
			top = append(top, captionRows("GAIA", said, textWidth, voiceSaidStyle)...)
		}
	}
	if v.notice != "" {
		top = append(top, "  "+voiceNoticeStyle.Render(truncateRunes(v.notice, textWidth)))
	}
	top = append(top, dividerStyle.Render(strings.Repeat("─", width)))

	var bottom []string
	if m.confirmation != nil && m.confirmation.Pending() {
		bottom = append(bottom, m.confirmation.View())
	} else if m.question != nil {
		bottom = append(bottom, m.question.View())
	}
	bottom = append(bottom, dividerStyle.Render(strings.Repeat("─", width)), m.voiceHints(width))
	bottomRows := strings.Count(strings.Join(bottom, "\n"), "\n") + 1

	logRows := max(m.height-len(top)-bottomRows, 0)
	work := m.voiceWorkLog(logRows)
	for len(work) < logRows {
		work = append(work, "")
	}
	rows := append(append(top, work...), bottom...)
	out := strings.Join(rows, "\n")
	if lines := strings.Split(out, "\n"); m.height > 0 && len(lines) > m.height {
		out = strings.Join(lines[len(lines)-m.height:], "\n")
	}
	return out
}

func captionRows(label, text string, width int, style lipgloss.Style) []string {
	lead := "  " + voiceLabelStyle.Render(label) + strings.Repeat(" ", max(6-len(label), 1))
	indent := strings.Repeat(" ", lipgloss.Width(lead))
	lines := strings.Split(components.WrapText(text, max(width-lipgloss.Width(lead)+2, 8)), "\n")
	// The newest words matter; a long caption keeps its last three rows.
	if len(lines) > 3 {
		lines = lines[len(lines)-3:]
	}
	out := make([]string, 0, len(lines))
	for i, line := range lines {
		prefix := indent
		if i == 0 {
			prefix = lead
		}
		out = append(out, prefix+style.Render(line))
	}
	return out
}

func (m ChatModel) voiceHints(width int) string {
	v := m.voice
	var hints []string
	switch {
	case v.speaking:
		hints = append(hints, "any key stops GAIA")
	case v.phase == voiceFailed && len(v.missing) > 0 && !v.downloading:
		hints = append(hints, "d download", "Enter retry")
	case v.phase == voiceFailed:
		hints = append(hints, "Enter retry")
	case v.phase == voicePaused:
		hints = append(hints, "p resume")
	default:
		if !v.status.HandsFree() {
			hints = append(hints, "Space talk / send")
		}
		hints = append(hints, "p pause")
	}
	hints = append(hints, "c captions", "Esc back to chat")
	return "  " + activityStyle.Render(truncateRunes(strings.Join(hints, " · "), max(width-3, 1)))
}

// voiceWorkLog is the voice turn's steps in full: the live log while it
// runs, then the record it left in the transcript.
func (m ChatModel) voiceWorkLog(rows int) []string {
	if rows <= 0 {
		return nil
	}
	items, live := m.activity, m.streaming
	if !live {
		items = nil
		for _, msg := range m.messages[min(m.voice.workFrom, len(m.messages)):] {
			if msg.Role == RoleWork {
				items = msg.Work
			}
		}
	}
	log := collapseActivity(settleFailures(items, !live))
	elapsed := m.turnElapsed(time.Now())
	open := -1
	if n := len(log); live && n > 0 && !log[n-1].Done {
		open = n - 1
	}
	var lines []string
	for i, item := range log {
		lines = append(lines, m.renderActivityItem(item, i == open, elapsed, 0)...)
	}
	if live && open < 0 {
		lines = append(lines, m.renderLiveLine(m.idlePhrase(len(log)), elapsed))
	}
	if len(lines) > rows {
		lines = lines[len(lines)-rows:]
	}
	return lines
}
