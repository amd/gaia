// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// forceTrueColor renders styles with colour for one test, whatever stdout is.
func forceTrueColor(t *testing.T) {
	t.Helper()
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
}

func probe(env map[string]string, tty bool, prof termenv.Profile, config string) motionProbe {
	return motionProbe{
		getenv:  func(k string) string { return env[k] },
		isTTY:   func() bool { return tty },
		profile: func() termenv.Profile { return prof },
		config:  func() []byte { return []byte(config) },
	}
}

// The user's wish and the terminal's limits each turn motion off; a capable
// terminal with nobody asking otherwise gets the gradient.
func TestMotionRespectsTheUserAndTheTerminal(t *testing.T) {
	for _, tc := range []struct {
		name         string
		p            motionProbe
		still, grade bool
	}{
		{"truecolor tty", probe(nil, true, termenv.TrueColor, ""), false, true},
		{"256-colour tty", probe(nil, true, termenv.ANSI256, ""), false, true},
		{"16-colour tty: no gradient, so no flicker", probe(nil, true, termenv.ANSI, ""), false, false},
		{"reduced-motion env", probe(map[string]string{EnvReducedMotion: "1"}, true, termenv.TrueColor, ""), true, false},
		{"reduced-motion env, word", probe(map[string]string{EnvReducedMotion: "true"}, true, termenv.TrueColor, ""), true, false},
		{"NO_COLOR", probe(map[string]string{"NO_COLOR": "1"}, true, termenv.TrueColor, ""), true, false},
		{"not a tty", probe(nil, false, termenv.TrueColor, ""), true, false},
		{"config reduced_motion", probe(nil, true, termenv.TrueColor, `{"reduced_motion": true}`), true, false},
		{"config says false", probe(nil, true, termenv.TrueColor, `{"reduced_motion": false}`), false, true},
		{"config corrupt", probe(nil, true, termenv.TrueColor, `{reduced_motion`), false, true},
		{"config wrong type", probe(nil, true, termenv.TrueColor, `{"reduced_motion": "yes"}`), false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := motionFrom(tc.p)
			if got.still != tc.still || got.gradient != tc.grade {
				t.Errorf("still=%v gradient=%v, want still=%v gradient=%v", got.still, got.gradient, tc.still, tc.grade)
			}
			if got.still && len(got.spinnerFor().Frames) != 1 {
				t.Error("reduced motion still spins the spinner")
			}
		})
	}
}

// The shimmer is a pure function of time: the same tick draws the same frame,
// the band moves left to right, and the text is never changed — only coloured.
func TestShimmerFramesAreDeterministic(t *testing.T) {
	forceTrueColor(t)
	s := motionState{gradient: true}
	const text = "Reading the 8 contracts"

	at := func(d time.Duration) string {
		return s.shimmer(text, theme.Success, theme.Text, d, shimmerPeriod, false)
	}
	if at(500*time.Millisecond) != at(500*time.Millisecond) {
		t.Fatal("the same tick drew two different frames")
	}
	if at(500*time.Millisecond) == at(1200*time.Millisecond) {
		t.Error("the band did not move between ticks")
	}
	if at(300*time.Millisecond) != at(300*time.Millisecond+shimmerPeriod) {
		t.Error("the sweep does not loop on its period")
	}
	for _, d := range []time.Duration{0, 400 * time.Millisecond, 1100 * time.Millisecond, 1900 * time.Millisecond} {
		if got := ansi.Strip(at(d)); got != text {
			t.Errorf("at %s the shimmer changed the words: %q", d, got)
		}
	}

	// Left to right: the brightest cell is further right later in the period.
	if early, late := brightestCell(t, at(200*time.Millisecond)), brightestCell(t, at(1400*time.Millisecond)); late <= early {
		t.Errorf("the band moved right to left (cell %d, then %d)", early, late)
	}
}

// brightestCell is the index of the first cell drawn in the band's peak colour.
func brightestCell(t *testing.T, frame string) int {
	t.Helper()
	peak := lipgloss.NewStyle().Foreground(theme.Blend(theme.Success, theme.Text, 1)).Render("x")
	seq := strings.TrimSuffix(peak, "x"+strings.SplitN(peak, "x", 2)[1])
	cell := 0
	for i := 0; i < len(frame); {
		if strings.HasPrefix(frame[i:], seq) {
			return cell
		}
		if frame[i] == 0x1b {
			for i < len(frame) && frame[i] != 'm' {
				i++
			}
			i++
			continue
		}
		cell++
		i++
	}
	return -1
}

// No gradient, no motion: the text is drawn in its one colour, at every tick.
func TestStillAndFlatTerminalsNeverShimmer(t *testing.T) {
	forceTrueColor(t)
	for name, s := range map[string]motionState{
		"reduced motion": {still: true},
		"16 colours":     {},
	} {
		a := s.shimmer("Working", theme.Success, theme.Text, 100*time.Millisecond, shimmerPeriod, false)
		b := s.shimmer("Working", theme.Success, theme.Text, 900*time.Millisecond, shimmerPeriod, false)
		if a != b {
			t.Errorf("%s: the line moved between ticks", name)
		}
		if p := s.pulse(600 * time.Millisecond); p != 0 {
			t.Errorf("%s: a prompt pulsed (%v)", name, p)
		}
	}
}

// The pulse breathes between still and its peak, never past it.
func TestThePromptPulseStaysGentle(t *testing.T) {
	s := motionState{gradient: true}
	for d := time.Duration(0); d < pulsePeriod; d += 50 * time.Millisecond {
		if p := s.pulse(d); p < 0 || p > 0.6 {
			t.Fatalf("pulse at %s = %v, outside 0..0.6", d, p)
		}
	}
	if s.pulse(0) != 0 || s.pulse(pulsePeriod/2) < 0.59 {
		t.Errorf("the pulse does not breathe from still to its peak")
	}
}

// One tick redraws the status line and nothing else: the transcript is not
// rebuilt, so the frame differs from the last only on the animated row.
func TestATickRedrawsOnlyTheStatusLine(t *testing.T) {
	forceTrueColor(t)
	m, now := frameChat(t, 100, 30)
	m.motion = motionState{gradient: true}
	startTurn(m, *now, "Review the contracts")
	at(t, m, now, time.Second, call("read_file", `{"file_path":"a.docx"}`))
	*m = repaintTick(t, *m)
	before := strings.Split(m.View(), "\n")
	content := m.vpContent

	*now = now.Add(250 * time.Millisecond)
	*m = repaintTick(t, *m)
	after := strings.Split(m.View(), "\n")

	if m.vpContent != content {
		t.Error("a tick rebuilt the transcript")
	}
	var changed []int
	for i := range after {
		if i >= len(before) || before[i] != after[i] {
			changed = append(changed, i)
		}
	}
	statusRow := -1
	for i, r := range after {
		if strings.Contains(ansi.Strip(r), "Read a.docx") {
			statusRow = i
		}
	}
	if len(changed) != 1 || changed[0] != statusRow {
		t.Errorf("a tick changed rows %v; only the status line (row %d) may move", changed, statusRow)
	}
}

// A long turn ticks for its whole length; the per-tick cost has to stay flat
// as the transcript grows, which it does only if a tick never rebuilds it.
func TestTickCostDoesNotGrowWithTheTranscript(t *testing.T) {
	m, now := frameChat(t, 120, 40)
	m.motion = motionState{gradient: true}
	for i := 0; i < 200; i++ {
		m.messages = append(m.messages,
			Message{Role: RoleUser, Content: "question"},
			Message{Role: RoleAssistant, Content: strings.Repeat("an answer with **markdown** ", 20)})
	}
	startTurn(m, *now, "one more")
	at(t, m, now, time.Second, call("read_file", `{"file_path":"a.docx"}`))
	renders := m.lastRender

	const ticks = 200
	start := time.Now()
	for i := 0; i < ticks; i++ {
		*now = now.Add(50 * time.Millisecond)
		*m = repaintTick(t, *m)
		_ = m.View()
	}
	per := time.Since(start) / ticks
	t.Logf("one frame (tick + View) with %d messages: %s", len(m.messages), per)
	if m.lastRender != renders {
		t.Error("ticks rebuilt the transcript")
	}
	// Generous for a loaded CI box: 20 frames a second must stay a sliver of
	// one core. A full transcript rebuild per frame costs well over this.
	if per > 15*time.Millisecond {
		t.Errorf("one animation frame costs %s; at %d fps that is not negligible", per, animFPS)
	}
}

// repaintTick delivers one spinner tick the way the program loop does.
func repaintTick(t *testing.T, m ChatModel) ChatModel {
	t.Helper()
	updated, _ := m.Update(spinner.TickMsg{Time: time.Now(), ID: m.spinner.ID()})
	return updated.(ChatModel)
}

// The recap sweeps once when the work lands, then stays still for good.
func TestTheRecapSweepsOnceThenStops(t *testing.T) {
	forceTrueColor(t)
	m, now := frameChat(t, 100, 30)
	m.motion = motionState{gradient: true}
	startTurn(m, *now, "Install formulas")
	at(t, m, now, time.Second, call("run_shell_command", `{"command":"uv pip install formulas"}`))
	at(t, m, now, 3*time.Second, result("run_shell_command", `{"success":true}`))
	at(t, m, now, time.Second, finalAnswer("Done."))

	last := &m.messages[len(m.messages)-1]
	mid := m.renderRecap(last, last.FinishedAt.Add(finishSweep/2))
	done := m.renderRecap(last, last.FinishedAt.Add(2*finishSweep))
	later := m.renderRecap(last, last.FinishedAt.Add(time.Hour))
	if mid == done {
		t.Error("the recap did not sweep when the work landed")
	}
	if done != later {
		t.Error("the recap kept moving after its one sweep")
	}
	if ansi.Strip(mid) != ansi.Strip(done) {
		t.Error("the sweep changed the recap's words")
	}
	if !m.motion.sweeping(last.FinishedAt.Add(finishSweep/2)) || m.motion.sweeping(last.FinishedAt.Add(2*finishSweep)) {
		t.Error("the sweep window is wrong")
	}
}

func finalAnswer(text string) event.CanonicalFinalEvent {
	return event.CanonicalFinalEvent{Type: "final", Answer: text}
}
