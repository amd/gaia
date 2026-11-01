// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"errors"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/setupapi"
	f "github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

const (
	qwen  = "Qwen3.6-35B-A3B-GGUF"
	gemma = "Gemma-4-E4B-it-GGUF"
	emb   = "user.embeddinggemma-300m-GGUF"
	oss   = "gpt-oss-20b-GGUF"
	flm   = "gemma4-it-e2b-FLM"
)

var (
	chat   = []string{setupapi.RoleChat}
	vision = []string{setupapi.RoleVision}
	embed  = []string{setupapi.RoleEmbedding}
)

// coldPlan is a 96 GB Strix Halo with nothing downloaded: the recommended
// set is Qwen3.6, Gemma and the embedder.
func coldPlan() setupapi.Plan {
	path := `C:\Users\me\.cache\huggingface\hub`
	src := "lemonade"
	url := "http://127.0.0.1:13305/api/v1"
	rec := qwen
	return setupapi.Plan{
		Profile:          "gaia",
		Lemonade:         setupapi.LemonadeInfo{Mode: "gaia", URL: &url, Reachable: true},
		ChatModel:        setupapi.ChatModel{ID: qwen},
		RecommendedModel: &rec,
		CapacityGB:       f.Float(96),
		Disk:             setupapi.Disk{Path: &path, FreeGB: f.Float(412), Source: &src},
		Catalog: []setupapi.CatalogEntry{
			f.Entry(qwen, chat, 23.3, false, true, true, f.Bool(true), 27.1, 96),
			f.Entry(gemma, vision, 5.97, false, true, true, f.Bool(true), 8.4, 96),
			f.Entry(emb, embed, 0.33, false, true, true, f.Bool(true), 0.6, 96),
			f.Entry(oss, chat, 12.1, false, false, false, f.Bool(true), 14.0, 96),
		},
	}
}

func warmPlan() setupapi.Plan {
	p := coldPlan()
	for i := range p.Catalog {
		if p.Catalog[i].Planned {
			p.Catalog[i] = f.Entry(p.Catalog[i].ID, p.Catalog[i].Roles, *p.Catalog[i].FullSizeGB, true, true, true, f.Bool(true), 1, 96)
		}
	}
	return p
}

// small16 is a 16 GB PC: Gemma is the chat model and Qwen3.6 won't fit.
func small16() setupapi.Plan {
	p := coldPlan()
	rec := gemma
	p.RecommendedModel = &rec
	p.ChatModel.ID = gemma
	p.CapacityGB = f.Float(16)
	p.Catalog = []setupapi.CatalogEntry{
		f.Entry(gemma, []string{setupapi.RoleChat, setupapi.RoleVision}, 5.97, false, true, true, f.Bool(true), 8.4, 16),
		f.Entry(emb, embed, 0.33, false, true, true, f.Bool(true), 0.6, 16),
		f.Entry(qwen, chat, 23.3, false, false, false, f.Bool(false), 27.1, 16),
	}
	return p
}

// flmOnly has an FLM chat model on disk and nothing else.
func flmOnly() setupapi.Plan {
	p := small16()
	e := f.Entry(flm, chat, 2.1, true, false, false, nil, 0, 0)
	recipe := "flm"
	e.Recipe = &recipe
	p.Catalog = append(p.Catalog, e)
	return p
}

// gpuShared is a 128 GB Strix Halo whose GPU gets 24 GB now: the setting
// (16 GB) plus what the driver adds, so a setting and the GPU memory it gives
// are different numbers, as on real hardware.
func gpuShared() *setupapi.GPU {
	at := 19.0
	rec := 32.0
	guided := "30"
	return &setupapi.GPU{
		Applies: true, ShowInSetup: true, OS: "windows",
		TotalGB: 128, GPUGB: 24, CPUGB: 104,
		Setting:   setupapi.GPUSetting{Name: "Variable Graphics Memory", CurrentGB: 16, MinGB: 4, MaxGB: 96},
		ReserveGB: 16, Method: setupapi.MethodApply, NeedsAdmin: true, NeedsRestart: true,
		Presets: []setupapi.GPUPreset{
			{SettingGB: 32, GPUGB: 40, Unlocks: []string{qwen}},
			{SettingGB: 64, GPUGB: 72, Unlocks: []string{qwen, "gpt-oss-120b-mxfp-GGUF"}},
		},
		Recommended: &rec,
		Models:      []setupapi.GPUModel{{ID: qwen, FitsNow: false, FitsAtSettingGB: &at, Default: true}},
		GuidedSteps: []string{"Open AMD Software: Adrenalin Edition.", "Go to Performance > Tuning.", "Choose Custom and set it to 30 GB, as AMD Software shows it.", "Restart your PC."},
		GuidedValue: &guided,
	}
}

func discreteGPU() *setupapi.GPU {
	why := "This PC has a discrete GPU with its own memory."
	return &setupapi.GPU{Applies: false, HiddenReason: &why, Method: setupapi.MethodUnsupported}
}

// harness drives a Model with a fake clock and runs its commands inline.
type harness struct {
	t     *testing.T
	m     Model
	api   *f.Fake
	clock time.Time
}

func newHarness(t *testing.T, mode Mode, api *f.Fake, opts ...func(*Options)) *harness {
	t.Helper()
	h := &harness{t: t, api: api, clock: time.Date(2026, 10, 31, 12, 0, 0, 0, time.UTC)}
	o := Options{Mode: mode, Now: func() time.Time { return h.clock }}
	for _, fn := range opts {
		fn(&o)
	}
	h.m = New(api, o)
	h.m, _ = h.m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	h.run(h.m.Init())
	return h
}

// run executes cmd and feeds what it returns back in, skipping ticks (they
// sleep) — tests advance time and send ticks themselves.
func (h *harness) run(cmd tea.Cmd) {
	h.t.Helper()
	for depth := 0; cmd != nil && depth < 50; depth++ {
		msgs := collect(cmd)
		var next []tea.Cmd
		for _, msg := range msgs {
			var c tea.Cmd
			h.m, c = h.m.Update(msg)
			next = append(next, c)
		}
		cmd = tea.Batch(next...)
	}
}

// collect runs cmd, expanding batches, and drops anything that blocks (a
// tick) — a real Bubble Tea program would deliver those later.
func collect(cmd tea.Cmd) []tea.Msg {
	if cmd == nil {
		return nil
	}
	done := make(chan tea.Msg, 1)
	go func() { done <- cmd() }()
	var msg tea.Msg
	select {
	case msg = <-done:
	case <-time.After(150 * time.Millisecond):
		return nil
	}
	switch v := msg.(type) {
	case nil:
		return nil
	case tea.BatchMsg:
		var out []tea.Msg
		for _, c := range v {
			out = append(out, collect(c)...)
		}
		return out
	case tickMsg:
		return nil
	}
	return []tea.Msg{msg}
}

func (h *harness) key(keys ...string) {
	h.t.Helper()
	for _, k := range keys {
		var msg tea.KeyMsg
		switch k {
		case "enter":
			msg = tea.KeyMsg{Type: tea.KeyEnter}
		case "esc":
			msg = tea.KeyMsg{Type: tea.KeyEsc}
		case "up":
			msg = tea.KeyMsg{Type: tea.KeyUp}
		case "down":
			msg = tea.KeyMsg{Type: tea.KeyDown}
		case "left":
			msg = tea.KeyMsg{Type: tea.KeyLeft}
		case "right":
			msg = tea.KeyMsg{Type: tea.KeyRight}
		case "space":
			msg = tea.KeyMsg{Type: tea.KeySpace, Runes: []rune{' '}}
		default:
			msg = tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
		}
		var cmd tea.Cmd
		h.m, cmd = h.m.Update(msg)
		h.run(cmd)
	}
}

// tick advances the clock and delivers one tick, which may poll.
func (h *harness) tick(d time.Duration) {
	h.t.Helper()
	h.clock = h.clock.Add(d)
	var cmd tea.Cmd
	h.m, cmd = h.m.Update(tickMsg{gen: h.m.tickGen})
	h.run(cmd)
}

// poll advances the clock past the poll interval so a status read happens.
func (h *harness) poll() { h.tick(1100 * time.Millisecond) }

func (h *harness) send(msg tea.Msg) {
	var cmd tea.Cmd
	h.m, cmd = h.m.Update(msg)
	h.run(cmd)
}

func (h *harness) screen() string { return ansi.Strip(h.m.View()) }

// flat collapses whitespace, so a sentence wrapped across lines still matches.
func flat(s string) string { return strings.Join(strings.Fields(s), " ") }

func (h *harness) expect(substr string) {
	h.t.Helper()
	if s := h.screen(); !strings.Contains(flat(s), flat(substr)) {
		h.t.Fatalf("screen lacks %q:\n%s", substr, s)
	}
}

func (h *harness) expectNot(substr string) {
	h.t.Helper()
	if s := h.screen(); strings.Contains(flat(s), flat(substr)) {
		h.t.Fatalf("screen should not show %q:\n%s", substr, s)
	}
}

// focusID moves the cursor to the row for id.
func (h *harness) focusID(id string) {
	h.t.Helper()
	for i, it := range h.m.items() {
		if !it.group && sameID(it.row.id, id) {
			h.m.focus = i
			return
		}
	}
	h.t.Fatalf("no row for %s", id)
}

var spinnerGlyph = regexp.MustCompile(`[⣾⣽⣻⢿⡿⣟⣯⣷]`)

var errDropped = errors.New("connection refused")

func TestMain(m *testing.M) {
	// Frames are delivered by the tests, with a fake clock.
	tickAfter = func(time.Duration, func(time.Time) tea.Msg) tea.Cmd { return nil }
	os.Exit(m.Run())
}

func keyMsg(k string) tea.KeyMsg {
	switch k {
	case "enter":
		return tea.KeyMsg{Type: tea.KeyEnter}
	case "esc":
		return tea.KeyMsg{Type: tea.KeyEsc}
	}
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
}
