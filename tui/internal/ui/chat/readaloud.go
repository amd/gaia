// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/speech"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// Read aloud: /read-aloud speaks the last reply through Lemonade's Kokoro
// voices, /voice picks the voice, speed and auto-read. The settings live in
// ~/.gaia/config.json, shared with the Agent UI.

const (
	readAloudCommand = "/read-aloud"
	voiceCommand     = "/voice"
)

const voiceUsage = "/voice <id> picks a voice · /voice speed <50-200> sets the speed in percent · " +
	"/voice auto on|off reads every finished reply · /read-aloud reads the last reply"

// readAloudStopWait bounds how long quitting waits for the player to die.
const readAloudStopWait = 2 * time.Second

func isReadAloudCommand(query string) bool {
	return commandWithArg(query, readAloudCommand)
}

func isVoiceCommand(query string) bool {
	return commandWithArg(query, voiceCommand)
}

func commandWithArg(query, name string) bool {
	t := strings.TrimSpace(query)
	return t == name || strings.HasPrefix(t, name+" ")
}

// commandArg is everything after the command, with runs of spaces collapsed.
func commandArg(query, name string) string {
	return strings.Join(strings.Fields(strings.TrimPrefix(strings.TrimSpace(query), name)), " ")
}

// readAloudDeps is what a reading needs from the machine. A variable so tests
// stand in a fake player and settings.
var readAloudDeps = struct {
	settings   func() (preflight.ReadAloud, error)
	findPlayer func() (speech.PlayFunc, error)
}{
	settings: preflight.ReadReadAloud,
	findPlayer: func() (speech.PlayFunc, error) {
		p, err := speech.FindPlayer()
		if err != nil {
			return nil, err
		}
		return p.Play, nil
	},
}

// readAloudRun is one reading in progress. Shared by pointer, so every copy
// of the model stops the same one.
type readAloudRun struct {
	id     int
	cancel context.CancelFunc
	events chan tea.Msg
	done   chan struct{}
}

// readAloudNoticeMsg is a line the reading wants in the transcript.
type readAloudNoticeMsg struct {
	id   int
	text string
}

// readAloudDoneMsg ends a reading.
type readAloudDoneMsg struct {
	id  int
	err error
}

// readAloudSettingsMsg answers /voice with the saved settings.
type readAloudSettingsMsg struct {
	cfg preflight.ReadAloud
	err error
}

// readAloudSavedMsg confirms a /voice change was written.
type readAloudSavedMsg struct {
	text string
	auto *bool
	err  error
}

// readAloudAutoMsg is the auto-read setting loaded at launch.
type readAloudAutoMsg struct {
	auto bool
	err  error
}

func loadReadAloudAutoCmd() tea.Msg {
	cfg, err := readAloudDeps.settings()
	return readAloudAutoMsg{auto: cfg.Auto, err: err}
}

func waitReadAloud(ch <-chan tea.Msg) tea.Cmd {
	return func() tea.Msg {
		msg, ok := <-ch
		if !ok {
			return nil
		}
		return msg
	}
}

// runReadAloudCommand handles /read-aloud and /read-aloud stop.
func (m ChatModel) runReadAloudCommand(arg string) (tea.Model, tea.Cmd) {
	switch arg {
	case "stop":
		if m.readAloud == nil {
			return m.statusNote("Nothing is being read aloud."), nil
		}
		m.stopReadAloud()
		return m.statusNote("Stopped reading aloud."), nil
	case "":
		if m.readAloud != nil {
			m.stopReadAloud()
			return m.statusNote("Stopped reading aloud."), nil
		}
		reply := m.lastAnswer()
		if strings.TrimSpace(reply) == "" {
			return m.statusNote("There is no reply to read aloud yet."), nil
		}
		pieces := speech.Pieces(reply, speech.MaxPieceChars)
		if len(pieces) == 0 {
			return m.statusNote("The last reply is all code, so there is nothing to read aloud."), nil
		}
		m = m.statusNote("Reading the last reply aloud. /read-aloud again stops it.")
		return m.startReadAloud(pieces)
	}
	return m.errorNote(fmt.Sprintf("%q is not a /read-aloud option. /read-aloud reads the last reply "+
		"(or stops one being read); /read-aloud stop stops it.", arg)), nil
}

// startReadAloud begins reading pieces, stopping any reading already going.
func (m ChatModel) startReadAloud(pieces []string) (ChatModel, tea.Cmd) {
	m.stopReadAloud()
	m.readAloudSeq++
	ctx, cancel := context.WithCancel(context.Background())
	run := &readAloudRun{
		id:     m.readAloudSeq,
		cancel: cancel,
		events: make(chan tea.Msg, 4),
		done:   make(chan struct{}),
	}
	m.readAloud = run
	go readAloudWork(ctx, run, m.lemonadeBaseURL, pieces)
	return m, waitReadAloud(run.events)
}

// readAloudWork does one reading off the UI goroutine: settings, player,
// the voice model (pulled if it is not downloaded yet), then the pieces.
func readAloudWork(ctx context.Context, run *readAloudRun, base string, pieces []string) {
	defer close(run.done)
	defer close(run.events)
	err := func() error {
		cfg, err := readAloudDeps.settings()
		if err != nil {
			return err
		}
		play, err := readAloudDeps.findPlayer()
		if err != nil {
			return err
		}
		client := lemonade.New(base)
		st, err := client.SpeechStatus(ctx)
		if err != nil {
			return err
		}
		if !st.Downloaded {
			size := ""
			if st.SizeGB > 0 {
				size = fmt.Sprintf(" (~%.2f GB)", st.SizeGB)
			}
			run.events <- readAloudNoticeMsg{id: run.id, text: "Downloading the " + lemonade.SpeechModel +
				" voice model" + size + " through Lemonade. Reading starts when it finishes."}
			if err := client.PullSpeechModel(ctx); err != nil {
				return fmt.Errorf("could not download %s: %w", lemonade.SpeechModel, err)
			}
		}
		return speech.Speak(ctx, pieces, speech.LemonadeSynthesizer(client, cfg.Settings()), play)
	}()
	run.events <- readAloudDoneMsg{id: run.id, err: err}
}

// stopReadAloud cancels the reading in progress: the requests in flight and
// the player process both stop.
func (m *ChatModel) stopReadAloud() {
	if m.readAloud == nil {
		return
	}
	m.readAloud.cancel()
	m.readAloud = nil
}

// StopReadAloud stops any reading and waits briefly for the player to be
// killed, so quitting never leaves audio playing.
func (m *ChatModel) StopReadAloud() {
	run := m.readAloud
	if run == nil {
		return
	}
	m.stopReadAloud()
	select {
	case <-run.done:
	case <-time.After(readAloudStopWait):
	}
}

// autoReadAloud reads a reply that has just finished, when auto-read is on.
func (m ChatModel) autoReadAloud(reply string) (ChatModel, tea.Cmd) {
	if !m.readAloudAuto {
		return m, nil
	}
	pieces := speech.Pieces(reply, speech.MaxPieceChars)
	if len(pieces) == 0 {
		return m, nil
	}
	return m.startReadAloud(pieces)
}

func (m ChatModel) handleReadAloudMsg(msg tea.Msg) (tea.Model, tea.Cmd, bool) {
	switch msg := msg.(type) {
	case readAloudNoticeMsg:
		if m.readAloud == nil || m.readAloud.id != msg.id {
			return m, nil, true
		}
		return m.statusNote(msg.text), waitReadAloud(m.readAloud.events), true
	case readAloudDoneMsg:
		if m.readAloud == nil || m.readAloud.id != msg.id {
			// Stopped by the user; the stop already said so.
			return m, nil, true
		}
		m.readAloud = nil
		if msg.err == nil || errors.Is(msg.err, context.Canceled) {
			return m, nil, true
		}
		return m.errorNote("Could not read the reply aloud: " + msg.err.Error()), nil, true
	case readAloudSettingsMsg:
		if msg.err != nil {
			return m.errorNote("Could not read the read-aloud settings: " + msg.err.Error()), nil, true
		}
		m.readAloudAuto = msg.cfg.Auto
		return m.statusNote(describeReadAloud(msg.cfg)), nil, true
	case readAloudSavedMsg:
		if msg.err != nil {
			return m.errorNote("Could not save the read-aloud setting: " + msg.err.Error()), nil, true
		}
		if msg.auto != nil {
			m.readAloudAuto = *msg.auto
		}
		return m.statusNote(msg.text), nil, true
	case readAloudAutoMsg:
		if msg.err != nil {
			return m.errorNote("Could not read the read-aloud settings: " + msg.err.Error()), nil, true
		}
		m.readAloudAuto = msg.auto
		return m, nil, true
	}
	return m, nil, false
}

// runVoiceCommand handles /voice and its settings.
func (m ChatModel) runVoiceCommand(arg string) (tea.Model, tea.Cmd) {
	fields := strings.Fields(arg)
	switch {
	case len(fields) == 0:
		return m, func() tea.Msg {
			cfg, err := readAloudDeps.settings()
			return readAloudSettingsMsg{cfg: cfg, err: err}
		}
	case fields[0] == "speed":
		speed, err := parseVoiceSpeed(fields[1:])
		if err != nil {
			return m.errorNote(err.Error()), nil
		}
		return m, saveReadAloudCmd(nil, func() (string, error) { return preflight.WriteReadAloudSpeed(speed) },
			fmt.Sprintf("Read-aloud speed set to %d%%.", speed))
	case fields[0] == "auto":
		if len(fields) != 2 || (fields[1] != "on" && fields[1] != "off") {
			return m.errorNote("Use /voice auto on or /voice auto off."), nil
		}
		on := fields[1] == "on"
		text := "Auto-read is off: /read-aloud reads a reply when you ask."
		if on {
			text = "Auto-read is on: every finished reply is read aloud. /read-aloud stops one; /voice auto off turns this off."
		}
		return m, saveReadAloudCmd(&on, func() (string, error) { return preflight.WriteReadAloudAuto(on) }, text)
	case len(fields) == 1:
		id := strings.ToLower(fields[0])
		v, ok := speech.LookupVoice(id)
		if !ok {
			return m.errorNote(fmt.Sprintf("%q is not a voice. Use one of: %s. %s",
				fields[0], strings.Join(voiceIDs(), ", "), voiceUsage)), nil
		}
		return m, saveReadAloudCmd(nil, func() (string, error) { return preflight.WriteReadAloudVoice(v.ID) },
			"Voice set to "+voiceLabel(v)+".")
	}
	return m.errorNote(fmt.Sprintf("%q is not a /voice option. %s", arg, voiceUsage)), nil
}

func parseVoiceSpeed(args []string) (int, error) {
	bad := fmt.Errorf("Speed is a percentage of normal from %d to %d, e.g. /voice speed 120.", speech.MinSpeed, speech.MaxSpeed)
	if len(args) != 1 {
		return 0, bad
	}
	speed, err := strconv.Atoi(strings.TrimSuffix(args[0], "%"))
	if err != nil || preflight.CheckReadAloudSpeed(speed) != nil {
		return 0, fmt.Errorf("%q is not a speed. %s", args[0], bad.Error())
	}
	return speed, nil
}

func saveReadAloudCmd(auto *bool, save func() (string, error), text string) tea.Cmd {
	return func() tea.Msg {
		if _, err := save(); err != nil {
			return readAloudSavedMsg{err: err}
		}
		return readAloudSavedMsg{text: text, auto: auto}
	}
}

func voiceIDs() []string {
	var ids []string
	for _, v := range speech.Voices() {
		ids = append(ids, v.ID)
	}
	return ids
}

// voiceLabel is "Bella (af_bella, American English, female)".
func voiceLabel(v speech.Voice) string {
	for _, g := range speech.VoiceGroups() {
		for _, gv := range g.Voices {
			if gv.ID == v.ID {
				return fmt.Sprintf("%s (%s, %s)", v.Short(), v.ID, g.Label)
			}
		}
	}
	return v.ID
}

// describeReadAloud is /voice's answer: the settings, then every voice.
func describeReadAloud(cfg preflight.ReadAloud) string {
	auto := "off"
	if cfg.Auto {
		auto = "on"
	}
	current := cfg.Voice
	if v, ok := speech.LookupVoice(cfg.Voice); ok {
		current = voiceLabel(v)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "Read aloud: voice %s · speed %d%% · auto-read %s\n", current, cfg.Speed, auto)
	b.WriteString("Voices:")
	for _, g := range speech.VoiceGroups() {
		ids := make([]string, 0, len(g.Voices))
		for _, v := range g.Voices {
			id := v.ID
			if v.ID == cfg.Voice {
				id += " (current)"
			}
			ids = append(ids, id)
		}
		fmt.Fprintf(&b, "\n  %s: %s", g.Label, strings.Join(ids, ", "))
	}
	b.WriteString("\n" + voiceUsage)
	return b.String()
}
