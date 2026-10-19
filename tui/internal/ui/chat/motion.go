// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"math"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
	xterm "golang.org/x/term"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Motion with meaning only. Three things move, never more than two at once:
//
//   - the live status line: the spinner, and a soft highlight sweeping across
//     what the agent is doing right now;
//   - a pending permission prompt: its question gently pulses;
//   - a finished turn's recap line: one sweep, once, when the work lands.
//
// Everything else is still. All of it rides the spinner's tick, and none of it
// rebuilds the transcript: the status line and the prompt are drawn outside the
// viewport, so a frame rewrites only the rows that moved. The recap's one sweep
// is the exception, bounded to finishSweep.

// animFPS is the shared tick. 20 frames a second is smooth for a 3-cell band
// and costs one View() per frame.
const animFPS = 20

// animSpinner is GAIA's braille spinner at the shared tick. Each frame is held
// for two ticks, so it turns at the same 10 frames a second as before.
var animSpinner = func() spinner.Spinner {
	frames := make([]string, 0, 2*len(spinner.Dot.Frames))
	for _, f := range spinner.Dot.Frames {
		frames = append(frames, f, f)
	}
	return spinner.Spinner{Frames: frames, FPS: time.Second / animFPS}
}()

// stillSpinner is the spinner under reduced motion: one glyph, and the clock
// beside it is what shows the turn is alive. It still ticks at 10 fps: the
// tick is also what flushes streamed text to the screen.
var stillSpinner = spinner.Spinner{Frames: []string{"⣿"}, FPS: time.Second / 10}

const (
	// shimmerPeriod is one sweep across the live line: calm, not busy.
	shimmerPeriod = 2 * time.Second
	// shimmerBand is the half-width of the highlight, in cells.
	shimmerBand = 3.0
	// finishSweep is the recap line's one-time sweep.
	finishSweep = 1200 * time.Millisecond
	// pulsePeriod is a full breath of a pending prompt's question.
	pulsePeriod = 2400 * time.Millisecond
)

// EnvReducedMotion turns every animation off. The config key reduced_motion
// (`gaia config set reduced_motion true`) does the same, for good.
const EnvReducedMotion = "GAIA_TUI_REDUCED_MOTION"

// motionState is what this session may animate.
type motionState struct {
	// still: no shimmer, no pulse, no sweep, and a spinner that does not spin.
	still bool
	// gradient: the terminal can show a blend (256 colours or more). A 16-colour
	// terminal would turn a gradient into flicker between its own palette
	// entries, so there the text stays one colour.
	gradient bool
	// finishAt starts the recap line's one sweep; zero when none is due.
	finishAt time.Time
	// sweepUnpainted holds the tick for one frame after the sweep ends, so the
	// recap is redrawn still rather than frozen mid-highlight.
	sweepUnpainted bool
}

// motionProbe is what decides motion, swappable for tests.
type motionProbe struct {
	getenv  func(string) string
	isTTY   func() bool
	profile func() termenv.Profile
	config  func() []byte
}

var realMotionProbe = motionProbe{
	getenv:  os.Getenv,
	isTTY:   func() bool { return xterm.IsTerminal(int(os.Stdout.Fd())) },
	profile: lipgloss.ColorProfile,
	config: func() []byte {
		path := lemonade.ConfigPathFrom(os.Getenv, os.UserHomeDir)
		if path == "" {
			return nil
		}
		raw, err := os.ReadFile(path) // #nosec G304 -- the user's own ~/.gaia/config.json
		if err != nil {
			return nil
		}
		return raw
	},
}

func newMotionState() motionState { return motionFrom(realMotionProbe) }

// motionFrom decides motion. The user's wish wins, then the terminal's limits.
func motionFrom(p motionProbe) motionState {
	still := truthy(p.getenv(EnvReducedMotion)) ||
		p.getenv("NO_COLOR") != "" ||
		!p.isTTY() ||
		configReducedMotion(p.config())
	if still {
		return motionState{still: true}
	}
	prof := p.profile()
	return motionState{gradient: prof == termenv.TrueColor || prof == termenv.ANSI256}
}

func truthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// configReducedMotion reads reduced_motion from config.json. Absent, unreadable
// or not a boolean all mean "not asked for".
func configReducedMotion(raw []byte) bool {
	if len(raw) == 0 {
		return false
	}
	var cfg struct {
		ReducedMotion *bool `json:"reduced_motion"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil || cfg.ReducedMotion == nil {
		return false
	}
	return *cfg.ReducedMotion
}

// spinnerFor is the spinner this session runs.
func (s motionState) spinnerFor() spinner.Spinner {
	if s.still {
		return stillSpinner
	}
	return animSpinner
}

// sweeping reports whether the recap's one-time sweep is still running.
func (s motionState) sweeping(now time.Time) bool {
	return !s.still && !s.finishAt.IsZero() && now.Sub(s.finishAt) < finishSweep
}

// shimmer draws text in base with a soft highlight centred at a position that
// moves with t: a band of a few cells, blended toward hi, sweeping left to right
// once per period. Pure in t, so a frame test can pin it.
func (s motionState) shimmer(text string, base, hi lipgloss.AdaptiveColor, t, period time.Duration, bold bool) string {
	plain := lipgloss.NewStyle().Foreground(base).Bold(bold)
	if s.still || !s.gradient || text == "" || period <= 0 {
		return plain.Render(text)
	}
	runes := []rune(text)
	span := float64(len(runes)) + 2*shimmerBand
	pos := float64(t%period)/float64(period)*span - shimmerBand
	return paintBand(runes, pos, base, hi, bold)
}

// sweepOnce is shimmer run a single time over d, then still.
func (s motionState) sweepOnce(text string, base, hi lipgloss.AdaptiveColor, since, d time.Duration) string {
	if s.still || !s.gradient || since < 0 || since >= d {
		return lipgloss.NewStyle().Foreground(base).Render(text)
	}
	runes := []rune(text)
	span := float64(len(runes)) + 2*shimmerBand
	pos := float64(since)/float64(d)*span - shimmerBand
	return paintBand(runes, pos, base, hi, false)
}

// paintBand colours each rune by its distance from pos, grouping runs of the
// same colour into one styled span so a frame carries few escapes.
func paintBand(runes []rune, pos float64, base, hi lipgloss.AdaptiveColor, bold bool) string {
	var b strings.Builder
	runStart, runLevel := 0, -1
	flush := func(end int) {
		if runLevel < 0 || end <= runStart {
			return
		}
		col := base
		if runLevel > 0 {
			col = theme.Blend(base, hi, float64(runLevel)/shimmerLevels)
		}
		b.WriteString(lipgloss.NewStyle().Foreground(col).Bold(bold).Render(string(runes[runStart:end])))
	}
	for i := range runes {
		lvl := 0
		if d := math.Abs(float64(i) - pos); d < shimmerBand {
			// Smooth falloff, quantised so neighbouring cells share a colour.
			f := 0.5 * (1 + math.Cos(math.Pi*d/shimmerBand))
			lvl = int(math.Round(f * shimmerLevels))
		}
		if lvl != runLevel {
			flush(i)
			runStart, runLevel = i, lvl
		}
	}
	flush(len(runes))
	return b.String()
}

// shimmerLevels quantises the band. Four steps reads as a smooth gradient and
// keeps the colour count per frame small.
const shimmerLevels = 4.0

// pulse is how far a pending prompt's question has moved toward its highlight,
// 0..maxPulse, breathing once per period. Zero when motion is off.
func (s motionState) pulse(t time.Duration) float64 {
	if s.still || !s.gradient {
		return 0
	}
	const maxPulse = 0.6
	phase := float64(t%pulsePeriod) / float64(pulsePeriod)
	return maxPulse * 0.5 * (1 - math.Cos(2*math.Pi*phase))
}
