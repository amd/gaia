// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"math/rand"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/setupapi"
	f "github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

// every screen the flow can be on, reached through real keys.
func everyScreen(t *testing.T) map[string]*harness {
	out := map[string]*harness{}
	h := newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: gpuShared()})
	out["models"] = h

	h = newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: gpuShared()})
	h.key("enter")
	out["gpu"] = h

	g := gpuShared()
	g.Method = setupapi.MethodGuided
	h = newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: g})
	h.key("enter", "enter")
	out["guide"] = h

	h = startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 5e9, BytesTotal: total(qwen), SpeedBPS: 30e6})
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateFailed, ErrorKind: strp("disk_full"), BytesDone: 1e9, BytesTotal: total(gemma)})
	h.poll()
	out["download"] = h

	out["ready"] = newHarness(t, ModeFirstRun, &f.Fake{P: warmPlan(), G: discreteGPU()})

	h = newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: discreteGPU()})
	h.key("c")
	out["chat-pick"] = h

	out["load-error"] = newHarness(t, ModeFirstRun, &f.Fake{PlanErr: errDropped})
	return out
}

func lastLine(s string) string {
	lines := strings.Split(s, "\n")
	return lines[len(lines)-1]
}

// At 80x24 every screen fits: no line wider than 80, no more than 24 lines,
// and the footer — with esc — is the last line.
func TestEveryScreenFits80x24WithEscOnTheFooter(t *testing.T) {
	for name, h := range everyScreen(t) {
		h.send(tea.WindowSizeMsg{Width: 80, Height: 24})
		s := h.m.View()
		lines := strings.Split(s, "\n")
		if len(lines) > 24 {
			t.Errorf("%s: %d lines at 80x24:\n%s", name, len(lines), ansi.Strip(s))
		}
		for i, l := range lines {
			if w := ansi.StringWidth(l); w > 80 {
				t.Errorf("%s: line %d is %d wide", name, i, w)
			}
		}
		if !strings.Contains(ansi.Strip(lastLine(s)), "esc") {
			t.Errorf("%s: the footer lost esc: %q", name, ansi.Strip(lastLine(s)))
		}
	}
}

// Smaller terminals degrade but never corrupt the session: nothing wider or
// taller than the window, esc still offered, and a too-short one says so.
func TestSmallTerminalsDegradeCleanly(t *testing.T) {
	sizes := [][2]int{{59, 24}, {50, 20}, {40, 12}, {30, 24}, {20, 10}, {80, 10}, {120, 9}, {10, 3}, {1, 1}}
	for name, h := range everyScreen(t) {
		for _, sz := range sizes {
			h.send(tea.WindowSizeMsg{Width: sz[0], Height: sz[1]})
			s := h.m.View()
			lines := strings.Split(s, "\n")
			if len(lines) > sz[1] {
				t.Errorf("%s at %dx%d: %d lines", name, sz[0], sz[1], len(lines))
			}
			for i, l := range lines {
				if w := ansi.StringWidth(l); w > sz[0] {
					t.Errorf("%s at %dx%d: line %d is %d wide", name, sz[0], sz[1], i, w)
				}
			}
			plain := ansi.Strip(s)
			if sz[1] < minH {
				if !strings.Contains(plain, "Window too small") && sz[0] >= 20 {
					t.Errorf("%s at %dx%d: no too-small notice:\n%s", name, sz[0], sz[1], plain)
				}
				continue
			}
			if sz[0] >= 30 && !strings.Contains(ansi.Strip(lastLine(s)), "esc") {
				t.Errorf("%s at %dx%d: footer lost esc: %q", name, sz[0], sz[1], ansi.Strip(lastLine(s)))
			}
		}
	}
}

// Below 60 columns the purpose column becomes letters with a legend.
func TestNarrowWindowShortensThePurposeColumn(t *testing.T) {
	h := newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: discreteGPU()})
	h.send(tea.WindowSizeMsg{Width: 56, Height: 24})
	h.expect("C chat · V vision · D documents")
	h.expectNot("Documents  ")
}

// Too short: one line, and only esc does anything.
func TestTooShortWindowIgnoresEverythingButEsc(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.send(tea.WindowSizeMsg{Width: 80, Height: 6})
	h.expect(tooSmall)
	h.key("enter", "space", "a")
	if api.Called("run") || api.Called("skip") {
		t.Fatal("keys acted behind a window too small to show them")
	}
	h.send(tea.WindowSizeMsg{Width: 80, Height: 24})
	h.expect("Choose your models")
}

// Resizing mid-download keeps the download and redraws to the new size.
func TestResizeMidDownloadKeepsGoing(t *testing.T) {
	h := startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 5e9, BytesTotal: total(qwen)})
	h.poll()
	for _, w := range []int{200, 45, 80, 33, 120} {
		h.send(tea.WindowSizeMsg{Width: w, Height: 30})
		for _, l := range strings.Split(h.m.View(), "\n") {
			if ansi.StringWidth(l) > w {
				t.Fatalf("at width %d a line is %d wide", w, ansi.StringWidth(l))
			}
		}
		h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 6e9, BytesTotal: total(qwen)})
		h.poll()
	}
	if !h.m.dl.active || h.m.Screen() != "download" {
		t.Fatal("a resize disturbed the download")
	}
	h.send(tea.WindowSizeMsg{Width: 80, Height: 24})
	h.expect("6.0/23.3 GB")
}

// Random keys on every screen: no panic, no download nobody asked for from
// an unrelated key, and the frame still fits.
func TestRandomKeysNeverPanic(t *testing.T) {
	keys := []string{"up", "down", "left", "right", "space", "enter", "esc", "a", "c", "l", "t", "x", "d",
		"h", "s", "k", "u", "r", "R", "m", "y", "n", "q", "z", "1", "\x00", "ctrl+z", "~"}
	rng := rand.New(rand.NewSource(6226))
	for name, h := range everyScreen(t) {
		for i := 0; i < 300; i++ {
			k := keys[rng.Intn(len(keys))]
			func() {
				defer func() {
					if r := recover(); r != nil {
						t.Fatalf("%s: key %q panicked: %v", name, k, r)
					}
				}()
				h.key(k)
				if i%17 == 0 {
					h.tick(time.Second)
				}
			}()
			for _, l := range strings.Split(h.m.View(), "\n") {
				if ansi.StringWidth(l) > 80 {
					t.Fatalf("%s: after %q a line is %d wide", name, k, ansi.StringWidth(l))
				}
			}
		}
	}
}

// Bad data from the backend: empty plan, missing sizes, nil everything.
func TestOddPlansStillRender(t *testing.T) {
	plans := []setupapi.Plan{
		{Lemonade: setupapi.LemonadeInfo{Reachable: true}},
		{Lemonade: setupapi.LemonadeInfo{Reachable: true}, Catalog: []setupapi.CatalogEntry{{ID: "x"}}},
		func() setupapi.Plan {
			p := coldPlan()
			for i := range p.Catalog {
				p.Catalog[i].SizeGB, p.Catalog[i].FullSizeGB, p.Catalog[i].Downloaded = nil, nil, nil
			}
			p.Disk = setupapi.Disk{}
			return p
		}(),
	}
	for i, p := range plans {
		h := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()})
		if h.m.View() == "" {
			t.Errorf("plan %d rendered nothing", i)
		}
		h.key("enter", "space", "down", "enter")
	}
}

// Reduced motion: no animated frames, a static [..] and 1 Hz updates.
func TestReducedMotionHasNoAnimatedFrames(t *testing.T) {
	h := newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: discreteGPU()},
		func(o *Options) { o.Motion = Motion{Reduced: true} })
	if got := h.m.opts.Motion.Tick(); got != time.Second {
		t.Fatalf("reduced motion ticks every %v, want 1s", got)
	}
	h.key("enter")
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 5e9, BytesTotal: total(qwen)})
	h.poll()
	if s := h.screen(); spinnerGlyph.MatchString(s) {
		t.Fatalf("a spinner frame was drawn under reduced motion:\n%s", s)
	}
	h.expect("[..]")
	for _, id := range []string{qwen, emb} {
		h.api.Progress(setupapi.ModelProgress{ID: id, State: setupapi.StateReady, BytesDone: total(id), BytesTotal: total(id)})
	}
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateVerifying, BytesDone: total(gemma), BytesTotal: total(gemma)})
	h.poll()
	first := h.screen()
	if spinnerGlyph.MatchString(first) {
		t.Fatalf("a spinner frame was drawn under reduced motion:\n%s", first)
	}
	h.expect(verifyingLine)
	// The verifying bar is static stripes, the same frame after frame.
	h.tick(time.Second)
	h.tick(time.Second)
	if a, b := stripes(first), stripes(h.screen()); a != b {
		t.Fatalf("the verifying bar moved under reduced motion: %q vs %q", a, b)
	}
	// An elapsed counter says the screen is alive.
	h.expect("0:0")
}

func stripes(s string) string {
	for _, l := range strings.Split(s, "\n") {
		if strings.Contains(l, verifyingLine) {
			return l
		}
	}
	return ""
}

func TestDetectMotion(t *testing.T) {
	env := func(kv map[string]string) func(string) string {
		return func(k string) string { return kv[k] }
	}
	cases := []struct {
		name    string
		env     map[string]string
		tty     bool
		reduced bool
		ascii   bool
	}{
		{"plain terminal", map[string]string{"TERM": "xterm-256color", "LANG": "en_US.UTF-8"}, true, false, false},
		{"NO_MOTION", map[string]string{"NO_MOTION": "1"}, true, true, false},
		{"GAIA_REDUCED_MOTION", map[string]string{"GAIA_REDUCED_MOTION": "true"}, true, true, false},
		{"explicit off", map[string]string{"NO_MOTION": "0"}, true, false, false},
		{"dumb terminal", map[string]string{"TERM": "dumb"}, true, true, true},
		{"not a tty", map[string]string{}, false, true, false},
		{"latin-1 locale", map[string]string{"LANG": "en_US.ISO-8859-1"}, true, false, true},
		{"C locale", map[string]string{"LC_ALL": "C", "LANG": "en_US.UTF-8"}, true, false, true},
		{"no locale (Windows)", map[string]string{}, true, false, false},
	}
	for _, c := range cases {
		got := DetectMotion(env(c.env), c.tty)
		if got.Reduced != c.reduced || got.ASCII != c.ascii {
			t.Errorf("%s: got %+v, want reduced=%v ascii=%v", c.name, got, c.reduced, c.ascii)
		}
	}
}

// Every state reads on a monochrome terminal: markers and words, never only
// colour.
func TestEveryStateReadsWithoutColour(t *testing.T) {
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.Ascii)
	defer lipgloss.SetColorProfile(prev)

	h := startedDownload(t)
	states := map[string]setupapi.ModelProgress{
		"queued":      {ID: emb, State: setupapi.StateQueued, BytesTotal: total(emb)},
		"downloading": {ID: qwen, State: setupapi.StateDownloading, BytesDone: 5e9, BytesTotal: total(qwen)},
		"verifying":   {ID: gemma, State: setupapi.StateVerifying, BytesDone: total(gemma), BytesTotal: total(gemma)},
	}
	for _, p := range states {
		h.api.Progress(p)
	}
	h.poll()
	s := h.m.View()
	for _, want := range []string{"[ ]", "queued", "[..]", "5.0/23.3 GB", verifyWaiting} {
		if !strings.Contains(s, want) {
			t.Errorf("monochrome frame lacks %q:\n%s", want, s)
		}
	}
	for _, p := range []setupapi.ModelProgress{
		{ID: emb, State: setupapi.StateReady, BytesDone: total(emb), BytesTotal: total(emb)},
		{ID: qwen, State: setupapi.StateFailed, ErrorKind: strp("network"), BytesTotal: total(qwen)},
		{ID: gemma, State: setupapi.StateCancelled, BytesDone: 1e9, BytesTotal: total(gemma)},
	} {
		h.api.Progress(p)
	}
	h.api.SetRunState(setupapi.RunFailed)
	h.poll()
	s = h.m.View()
	for _, want := range []string{"[ok]", "ready", "[!]", "failed", "cancelled"} {
		if !strings.Contains(s, want) {
			t.Errorf("monochrome frame lacks %q:\n%s", want, s)
		}
	}
	if strings.Contains(s, "\x1b[3") {
		t.Error("the ASCII profile still emitted colour")
	}
}

// --- bars and numbers ----------------------------------------------------------

func TestBarGrowsInEighthsAndKeepsItsWidth(t *testing.T) {
	for _, w := range []int{1, 6, 14, 30} {
		for i := 0; i <= 80; i++ {
			fr := float64(i) / 80
			filled, track := bar(fr, w, false)
			if got := lipgloss.Width(filled + track); got != w {
				t.Fatalf("bar(%.3f, %d) is %d wide", fr, w, got)
			}
			a, b := bar(fr, w, true)
			if got := len(a + b); got != w {
				t.Fatalf("ascii bar(%.3f, %d) is %d wide", fr, w, got)
			}
		}
	}
	// 1/8 of one cell is visible.
	if f, _ := bar(1.0/80, 10, false); f != "▏" {
		t.Errorf("an eighth of a cell drew %q", f)
	}
	if f, _ := bar(0.5, 10, false); f != "█████" {
		t.Errorf("half of 10 drew %q", f)
	}
	for _, odd := range []float64{-1, 2, 0} {
		f, tr := bar(odd, 8, false)
		if lipgloss.Width(f+tr) != 8 {
			t.Errorf("bar(%v) broke its width", odd)
		}
	}
}

func TestNumbersReadAsPeopleSayThem(t *testing.T) {
	cases := map[string]string{
		formatGB(23.3):                             "23.3 GB",
		formatGB(0.33):                             "330 MB",
		formatGB(0.0001):                           "1 MB",
		formatGBShort(27.1):                        "27 GB",
		formatGBShort(8.0):                         "8 GB",
		formatGBShort(5.5):                         "5.5 GB",
		formatSpeed(18e6):                          "18 MB/s",
		formatETA(45 * time.Second):                "about 45 sec",
		formatETA(13 * time.Minute):                "about 13 min",
		formatETA(3 * time.Hour):                   "over 2 hours",
		shortName("user.embeddinggemma-300m-GGUF"): "embeddinggemma-300m",
		driveOf(strp(`d:\models\hub`)):             "D:",
		driveOf(strp("/home/me/.cache")):           "disk",
		driveOf(nil):                               "the model drive",
	}
	for got, want := range cases {
		if got != want {
			t.Errorf("got %q, want %q", got, want)
		}
	}
}

func TestRateIgnoresAResetAndHoldsUntilTenSeconds(t *testing.T) {
	var r rate
	t0 := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	r.observe(t0, 0, 100e9)
	for i := 1; i <= 9; i++ {
		r.observe(t0.Add(time.Duration(i)*time.Second), int64(i)*50e6, 100e9)
		if r.showETA(t0.Add(time.Duration(i) * time.Second)) {
			t.Fatalf("ETA shown after %ds", i)
		}
	}
	r.observe(t0.Add(11*time.Second), 11*50e6, 100e9)
	if !r.showETA(t0.Add(11 * time.Second)) {
		t.Fatal("ETA still hidden after 11 s of data")
	}
	// A resumed pull reporting fewer bytes starts the average over.
	r.observe(t0.Add(12*time.Second), 1e6, 100e9)
	if r.showETA(t0.Add(12 * time.Second)) {
		t.Fatal("a restarted pull kept the old ETA")
	}
}

// The ETA follows the measured speed both ways, smoothed but never pinned to
// an early guess: one slow first second can't leave "37 min" on screen while
// the download is minutes from done.
func TestRateETATracksTheMeasuredSpeed(t *testing.T) {
	t0 := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	const total = int64(30e9)
	type step struct {
		secs int
		bps  int64
	}
	for _, tc := range []struct {
		name  string
		start int64 // bytes the first read reports (a resume)
		jump  int64 // bytes that appear in one read before the speed (on disk)
		steps []step
		at    int // seconds after the start to check
		bps   int64
	}{
		{name: "slow first second then fast", steps: []step{{1, 1e6}, {40, 150e6}}, at: 12, bps: 150e6},
		{name: "fast then slow", steps: []step{{20, 150e6}, {60, 20e6}}, at: 60, bps: 20e6},
		{name: "slow then fast", steps: []step{{30, 10e6}, {40, 150e6}}, at: 55, bps: 150e6},
		{name: "resume after cancel", jump: 13_700_000_000, steps: []step{{30, 150e6}}, at: 12, bps: 150e6},
		{name: "resume that reports its bytes at once", start: 13_700_000_000, steps: []step{{30, 150e6}}, at: 12, bps: 150e6},
		{name: "retry after failure", start: 18e9, jump: -4_300_000_000, steps: []step{{30, 150e6}}, at: 13, bps: 150e6},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var r rate
			now := t0
			done := tc.start
			r.observe(now, done, total)
			if tc.jump != 0 {
				now = now.Add(time.Second)
				done += tc.jump
				r.observe(now, done, total)
			}
			sec := 0
			for _, st := range tc.steps {
				for i := 0; i < st.secs && sec < tc.at; i++ {
					now = now.Add(time.Second)
					done += st.bps
					sec++
					r.observe(now, done, total)
					if r.speed > 2*150e6 {
						t.Fatalf("speed %.0f MB/s at %ds: bytes already on disk counted as speed", r.speed/1e6, sec)
					}
				}
			}
			if !r.showETA(now) {
				t.Fatalf("no ETA at %ds", sec)
			}
			want := time.Duration(float64(total-done) / float64(tc.bps) * float64(time.Second))
			if r.eta < want*7/10 || r.eta > want*13/10 {
				t.Fatalf("ETA %v at %ds, want about %v", r.eta.Round(time.Second), sec, want.Round(time.Second))
			}
		})
	}
}
