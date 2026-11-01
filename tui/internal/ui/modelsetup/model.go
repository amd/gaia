// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package modelsetup is the TUI's model setup: first-run "choose your models",
// the GPU memory step, the download with per-model progress, and the ready
// summary — plus /models and /gpu, which reopen the same screens later.
//
// Every fact on screen comes from the daemon (setupapi), which serves the
// Agent UI's own plan and choice handlers, so the two apps never disagree
// about what gets downloaded. Choices are staged here and saved only when the
// user presses Continue or Apply; nothing downloads before that.
package modelsetup

import (
	"context"
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/setupapi"
)

// Mode is which job the screens are doing.
type Mode int

const (
	// ModeFirstRun is setup's own flow: models, GPU memory, download, ready.
	ModeFirstRun Mode = iota
	// ModeModels is /models: the same checklist with live state and Apply.
	ModeModels
	// ModeGPU is /gpu: the GPU memory screen alone.
	ModeGPU
)

type screen int

const (
	scrLoading screen = iota
	scrLoadError
	scrNoServer
	scrModels
	scrChatPick
	scrGPU
	scrGuide
	scrDownload
	scrReady
)

// Names for the control API and tests.
var screenNames = map[screen]string{
	scrLoading:   "loading",
	scrLoadError: "load-error",
	scrNoServer:  "no-server",
	scrModels:    "models",
	scrChatPick:  "chat-pick",
	scrGPU:       "gpu",
	scrGuide:     "gpu-guide",
	scrDownload:  "download",
	scrReady:     "ready",
}

// Messages the screens send their host.
type (
	// FinishedMsg: first-run setup is done and the user pressed enter on the
	// ready summary. The host re-checks and starts the agent.
	FinishedMsg struct{}
	// BackMsg: esc on the first screen of first-run setup. Choices are what
	// the user had ticked, for the host to pass back as Options.Keep.
	BackMsg struct{ Choices *Choices }
	// ClosedMsg: /models or /gpu was closed. Downloading says downloads keep
	// going in the background.
	ClosedMsg struct{ Downloading bool }
	// InstallServerMsg: the user asked to install the local model server,
	// which these screens need before they can list any model.
	InstallServerMsg struct{}
)

// Options configures the screens.
type Options struct {
	Mode Mode
	// Cloud is set when chat runs on a cloud provider: only the embedding
	// model is offered.
	Cloud  bool
	Motion Motion
	// OfferInstall lets the "server isn't running" screen install it, for a
	// host with no install step of its own in front of these screens.
	OfferInstall bool
	// StepsBefore is how many numbered setup steps the host showed before
	// these screens, so the count carries on rather than restarting. Zero
	// means one: where GAIA runs.
	StepsBefore int
	// StepsTotal is the host's step count, so the header agrees with it.
	// Zero means these screens are the last step.
	StepsTotal int
	// Keep is a choice the user made on an earlier visit, put back once the
	// plan loads so leaving and coming back loses nothing.
	Keep *Choices
	// Now is the clock, for tests.
	Now func() time.Time
}

// confirmKind is what the footer is asking.
type confirmKind int

const (
	askNone confirmKind = iota
	askNoFit
	askLowDisk
	askCancel
	askRemove
	askGPUConsent
	askBackendNoFit
	// askRedownload: a model downloaded but failed its load check; retrying
	// deletes it and downloads it again, so the user says yes first.
	askRedownload
)

// Model is the setup screens. It is a value; Update returns the next one.
type Model struct {
	api  setupapi.API
	opts Options
	scr  screen
	// back is where esc on the chat picker or guide returns to.
	back screen

	width, height int
	frame         int
	ticking       bool
	tickGen       int

	plan    *setupapi.Plan
	loadErr error
	sel     selection
	focus   int
	scroll  int
	showAll bool // the "already in Lemonade" group is open
	details bool
	pick    int // chat picker cursor

	ask       confirmKind
	askRow    rowEntry
	askAck    *setupapi.Ack
	ackedFit  map[string]bool
	ackedDisk bool
	keepTry   bool

	busy      string
	busySince time.Time
	note      string
	// resume is set when a partial download was found at load.
	resume bool

	gpu       *setupapi.GPU
	gpuErr    error
	gpuTarget int
	gpuPhase  gpuPhase
	gpuNote   string
	gpuWas    float64
	gpuDone   bool

	// The download.
	dl       download
	status   *setupapi.Status
	lost     error
	polling  bool
	inFlight bool
	lastPoll time.Time
	rates    map[string]*rate
	total    rate
	shown    map[string]float64
	readyAt  map[string]time.Time
	verifyAt map[string]time.Time
	// kept is the most of each model seen on disk this session. A resumed
	// run reports a model as queued, with no bytes, until its pull starts
	// again; the bytes are still there, so the bar keeps them.
	kept  map[string]int64
	since time.Time
	// runErr is why setup's own run stopped, kept for the ready summary.
	runErr string
}

type gpuPhase int

const (
	gpuIdle gpuPhase = iota
	gpuWaiting
	gpuApplied
	gpuDenied
	gpuPendingSaved
)

// download is what the download step has to do and has done.
type download struct {
	active bool
	// run is set once POST /run started setup's own download.
	run bool
	// queue is the single downloads still to start, in order; current is the
	// one running.
	queue   []string
	current string
	// ids are every model this step downloads, for the overall total.
	ids []string
	// cancelled is set after the user stopped it.
	cancelled bool
	// startedAt is when the backend accepted the latest run or single
	// download. starting is set from asking until it answers: a status read
	// in that window still shows the model as it was (failed, say) and must
	// not be taken for the new download's result.
	startedAt time.Time
	starting  bool
}

// New builds the screens; Init loads the plan.
func New(api setupapi.API, opts Options) Model {
	if opts.Now == nil {
		opts.Now = time.Now
	}
	m := Model{
		api:      api,
		opts:     opts,
		scr:      scrLoading,
		width:    80,
		height:   24,
		ackedFit: map[string]bool{},
		rates:    map[string]*rate{},
		shown:    map[string]float64{},
		readyAt:  map[string]time.Time{},
		verifyAt: map[string]time.Time{},
		kept:     map[string]int64{},
	}
	m.busy = readingModels
	m.busySince = opts.Now()
	// Init starts the first tick; this records that the chain is running.
	m.ticking = true
	return m
}

// Init loads the plan, the GPU split, and any download already running.
func (m Model) Init() tea.Cmd {
	gen := m.tickGen
	return tea.Batch(m.loadCmd(), tickAfter(m.opts.Motion.Tick(), func(time.Time) tea.Msg { return tickMsg{gen: gen} }))
}

// Screen names the screen on view, for the control API and tests.
func (m Model) Screen() string { return screenNames[m.scr] }

// Mode is the job these screens are doing.
func (m Model) Mode() Mode { return m.opts.Mode }

// Downloading reports whether a download is running, and how far it is.
func (m Model) Downloading() (bool, int) {
	if !m.dl.active || m.lost != nil {
		return false, 0
	}
	return true, m.overallPercent()
}

// Outcome says how a download that stopped ended, for a host that hid the
// screen: "lost" (GAIA stopped answering; /models reconnects), "failed"
// (with what failed, in words), or "done".
func (m Model) Outcome() (kind, detail string) {
	if m.lost != nil {
		return "lost", m.lost.Error()
	}
	for _, r := range m.shownRows() {
		if m.shownState(r) == setupapi.StateFailed {
			return "failed", m.nameOf(r.ID)
		}
		if r.State == setupapi.StateCancelled {
			return "failed", m.nameOf(r.ID)
		}
	}
	return "done", ""
}

// IsMsg reports whether msg belongs to these screens, so a host that hides
// them can still forward their ticks and status reads.
func IsMsg(msg tea.Msg) bool {
	switch msg.(type) {
	case loadedMsg, appliedMsg, startedMsg, statusMsg, tickMsg, gpuMsg, cancelledMsg, removedMsg, redownloadMsg:
		return true
	}
	return false
}

// Abandon stops a first-run download because its screen is going away: the
// spec says leaving the screen cancels it. Finished models and partial files
// stay. It waits a moment for the backend so the stop is not lost on exit.
func (m Model) Abandon() error {
	if !m.dl.active || m.opts.Mode != ModeFirstRun {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, err := m.api.Cancel(ctx, "")
	return err
}

// --- messages ---------------------------------------------------------------

type loadedMsg struct {
	plan    setupapi.Plan
	planErr error
	gpu     *setupapi.GPU
	gpuErr  error
	status  *setupapi.Status
}

type appliedMsg struct {
	plan setupapi.Plan
	ack  *setupapi.Ack
	err  error
}

type startedMsg struct {
	id  string // "" for a run
	ack *setupapi.Ack
	err error
}

type statusMsg struct {
	st  setupapi.Status
	err error
	// issued is when the read was sent, so one sent before a download
	// (re)started is never read as that download's result.
	issued time.Time
	at     time.Time
}

type tickMsg struct{ gen int }

type gpuMsg struct {
	gpu    setupapi.GPU
	err    error
	action string
}

type cancelledMsg struct{ err error }

type removedMsg struct {
	plan setupapi.Plan
	err  error
}

// redownloadMsg: a model that failed its load check was deleted, to download
// again.
type redownloadMsg struct {
	id  string
	err error
}

// --- commands ---------------------------------------------------------------

const callTimeout = 90 * time.Second

// tickAfter schedules the next frame. A var so tests deliver ticks themselves.
var tickAfter = tea.Tick

func (m Model) loadCmd() tea.Cmd {
	api, mode := m.api, m.opts.Mode
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		out := loadedMsg{}
		if mode != ModeGPU {
			out.plan, out.planErr = api.Plan(ctx)
			if st, err := api.Status(ctx); err == nil {
				out.status = &st
			}
		}
		if g, err := api.GPU(ctx, 0); err == nil {
			out.gpu = &g
		} else {
			out.gpuErr = err
		}
		return out
	}
}

// tickCmd keeps one tick chain alive while something moves.
func (m *Model) tickCmd() tea.Cmd {
	if m.ticking {
		return nil
	}
	m.ticking = true
	m.tickGen++
	gen := m.tickGen
	return tickAfter(m.opts.Motion.Tick(), func(time.Time) tea.Msg { return tickMsg{gen: gen} })
}

func (m Model) needsTick() bool {
	return m.busy != "" || m.dl.active || m.gpuPhase == gpuWaiting || m.flashing()
}

func (m Model) flashing() bool {
	now := m.opts.Now()
	for _, at := range m.readyAt {
		if now.Sub(at) < 200*time.Millisecond {
			return true
		}
	}
	return false
}

func (m Model) statusCmd() tea.Cmd {
	api, now := m.api, m.opts.Now
	issued := now()
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		st, err := api.Status(ctx)
		return statusMsg{st: st, err: err, issued: issued, at: now()}
	}
}

// --- update -----------------------------------------------------------------

// Update applies one message.
func (m Model) Update(msg tea.Msg) (Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.clampScroll()
		return m, nil

	case tickMsg:
		if msg.gen != m.tickGen {
			return m, nil
		}
		m.ticking = false
		m.frame++
		m.ease()
		var cmds []tea.Cmd
		now := m.opts.Now()
		if m.polling && !m.inFlight && m.lost == nil && now.Sub(m.lastPoll) >= time.Second {
			m.inFlight = true
			m.lastPoll = now
			cmds = append(cmds, m.statusCmd())
		}
		if m.needsTick() {
			cmds = append(cmds, m.tickCmd())
		}
		return m, tea.Batch(cmds...)

	case loadedMsg:
		return m.loaded(msg)

	case appliedMsg:
		return m.applied(msg)

	case startedMsg:
		return m.started(msg)

	case statusMsg:
		return m.statusArrived(msg)

	case gpuMsg:
		return m.gpuArrived(msg)

	case cancelledMsg:
		m.busy = ""
		if msg.err != nil {
			m.note = "Could not stop the download: " + msg.err.Error()
			return m, nil
		}
		// The current pull stops at its next chunk; keep reading until the
		// backend says so, so the row never freezes on "downloading".
		m.dl.cancelled = true
		m.dl.queue = nil
		return m, m.pollNow()

	case redownloadMsg:
		m.busy = ""
		if msg.err != nil {
			m.note = msg.err.Error()
			return m, nil
		}
		return m.restartOne(msg.id)

	case removedMsg:
		m.busy = ""
		if msg.err != nil {
			m.note = msg.err.Error()
			return m, nil
		}
		m = m.withPlan(msg.plan, true)
		return m.checkFinished(), nil

	case tea.KeyMsg:
		return m.key(msg)
	}
	return m, nil
}

func (m Model) loaded(msg loadedMsg) (Model, tea.Cmd) {
	m.busy = ""
	m.gpuErr = msg.gpuErr
	if msg.gpu != nil {
		g := *msg.gpu
		m.gpu = &g
		m.gpuTarget = m.recommendedPreset()
		m.gpuWas = g.GPUGB
	}
	if m.opts.Mode == ModeGPU {
		m.scr = scrGPU
		if m.gpu == nil {
			m.loadErr = msg.gpuErr
			m.scr = scrLoadError
		}
		return m, nil
	}
	if msg.planErr != nil {
		m.loadErr = msg.planErr
		m.scr = scrLoadError
		return m, nil
	}
	m.loadErr = nil
	m = m.withPlan(msg.plan, false)
	if m.opts.Keep != nil {
		m = m.applyChoices(*m.opts.Keep)
		m.opts.Keep = nil
	}
	if !msg.plan.Lemonade.Reachable {
		m.scr = scrNoServer
		return m, nil
	}
	// A download already running — another window, or this one reopened —
	// is attached to, never restarted.
	if st := msg.status; st != nil && (st.Running() || anyDownloading(*st)) {
		m.status = st
		m.dl = download{active: true, run: st.Running(), startedAt: m.opts.Now()}
		for _, row := range st.Models {
			if row.State != setupapi.StateSkipped {
				m.dl.ids = append(m.dl.ids, row.ID)
			}
		}
		m.scr = scrDownload
		m.since = m.opts.Now()
		m.focus = 0
		return m, m.startPolling()
	}
	m.scr = scrModels
	if m.opts.Mode == ModeFirstRun {
		for _, r := range m.sel.rows {
			if r.partial && m.sel.ticked[r.id] {
				m.resume = true
			}
		}
		if len(m.sel.pending()) == 0 && !m.sel.blocked() && !m.resume {
			m.scr = scrReady
		}
	}
	return m, nil
}

func anyDownloading(st setupapi.Status) bool {
	for _, r := range st.Models {
		if r.State == setupapi.StateDownloading {
			return true
		}
	}
	return false
}

// Choices is the user's staged choice on "Choose your models".
type Choices struct {
	Ticked     map[string]bool
	Unticked   map[string]bool
	Chat       string
	ChatChosen bool
	KeepTrying bool
}

func (m Model) choices() *Choices {
	if m.plan == nil {
		return nil
	}
	c := &Choices{Ticked: map[string]bool{}, Unticked: map[string]bool{}, Chat: m.sel.chat,
		ChatChosen: m.sel.chatChosen, KeepTrying: m.keepTry}
	for id, v := range m.sel.ticked {
		c.Ticked[id] = v
	}
	for id, v := range m.sel.unticked {
		c.Unticked[id] = v
	}
	return c
}

// applyChoices puts back an earlier visit's choice on a fresh plan: models
// the plan still lists, and a chat model that can still chat.
func (m Model) applyChoices(c Choices) Model {
	for id, v := range c.Ticked {
		if r, ok := m.sel.find(id); ok && !r.installed {
			m.sel.ticked[r.id] = v
		}
	}
	for id, v := range c.Unticked {
		if v {
			m.sel.unticked[id] = true
		}
	}
	if c.Chat != "" && m.sel.canChat(c.Chat) {
		m.sel.chat = c.Chat
		m.sel.chatChosen = m.sel.chatChosen || c.ChatChosen
	} else if !m.sel.canChat(m.sel.chat) {
		m.sel.chat = m.sel.fallbackChat()
	}
	m.keepTry = c.KeepTrying
	return m
}

// withPlan takes a new plan. keep carries the user's staged ticks over a
// refresh (a removal), so a reload never undoes what they chose.
func (m Model) withPlan(p setupapi.Plan, keep bool) Model {
	prev := m.sel
	m.plan = &p
	m.sel = newSelection(p, m.opts.Cloud)
	if keep {
		for id, v := range prev.ticked {
			if _, ok := m.sel.find(id); ok {
				m.sel.ticked[id] = v
			}
		}
		if prev.chat != "" && m.sel.canChat(prev.chat) {
			m.sel.chat = prev.chat
		}
	}
	m.clampFocus()
	return m
}

func (m Model) recommendedPreset() int {
	if m.gpu == nil || len(m.gpu.Presets) == 0 {
		return 0
	}
	if m.gpu.Recommended != nil {
		for i, p := range m.gpu.Presets {
			if p.SettingGB == *m.gpu.Recommended {
				return i
			}
		}
	}
	return 0
}

// --- choices ----------------------------------------------------------------

// proceed is Continue (setup) or Apply (/models): confirm what needs
// confirming, then save the choices.
func (m Model) proceed() (Model, tea.Cmd) {
	if m.sel.blocked() {
		m.note = zeroChat
		return m, nil
	}
	if r, ok := m.sel.noFit(m.ackedFit); ok {
		m.ask, m.askRow = askNoFit, r
		return m, nil
	}
	if !m.ackedDisk && m.sel.shortfall(m.freeGB(), m.drive()) != "" {
		m.ask = askLowDisk
		return m, nil
	}
	return m.save()
}

func (m Model) save() (Model, tea.Cmd) {
	if !m.sel.dirty(*m.plan) && m.opts.Mode == ModeModels {
		if len(m.sel.pending()) == 0 {
			m.note = "Nothing to apply."
			return m, nil
		}
		// Nothing to save, but a wanted model is still missing: fetch it.
		return m.startDownload()
	}
	m.busy, m.busySince, m.note = "Saving your choices…", m.opts.Now(), ""
	api, sel, plan := m.api, m.sel, *m.plan
	ackChat := m.ackedFit[sel.chat]
	return m, tea.Batch(func() tea.Msg {
		return applyChoices(api, plan, sel, ackChat)
	}, m.tickCmd())
}

// applyChoices saves the chat model, then which of setup's models to skip,
// against the plan the chat choice produced.
func applyChoices(api setupapi.API, plan setupapi.Plan, sel selection, ackChat bool) appliedMsg {
	ctx, cancel := context.WithTimeout(context.Background(), 3*callTimeout)
	defer cancel()
	if !sel.cloud && sel.chat != "" && !sameID(sel.chat, plan.ChatModel.ID) {
		p, ack, err := api.ChooseChat(ctx, sel.chat, ackChat)
		if err != nil || ack != nil {
			return appliedMsg{plan: plan, ack: ack, err: err}
		}
		plan = p
	}
	for _, e := range plan.Catalog {
		if !e.Planned {
			continue
		}
		want := sel.wants(e.ID)
		if want == !e.Skipped {
			continue
		}
		p, err := api.Skip(ctx, e.ID, !want)
		if err != nil {
			return appliedMsg{plan: plan, err: err}
		}
		plan = p
	}
	return appliedMsg{plan: plan}
}

func (m Model) applied(msg appliedMsg) (Model, tea.Cmd) {
	m.busy = ""
	if msg.err != nil {
		m.note = "Could not save your choices: " + msg.err.Error()
		return m, nil
	}
	if msg.ack != nil {
		m.ask, m.askAck = askBackendNoFit, msg.ack
		return m, nil
	}
	ticked := m.sel.ticked
	chat := m.sel.chat
	m = m.withPlan(msg.plan, false)
	// Extras: ticked models that are not setup's own stay ticked.
	for id, v := range ticked {
		if v {
			if r, ok := m.sel.find(id); ok && !r.installed {
				m.sel.ticked[r.id] = true
			}
		}
	}
	if chat != "" && m.sel.canChat(chat) {
		m.sel.chat = chat
	}
	if m.opts.Mode == ModeFirstRun && m.gpuStepShown() && !m.gpuDone {
		m.scr = scrGPU
		return m, nil
	}
	return m.startDownload()
}

// --- the GPU step -----------------------------------------------------------

// gpuStepShown reports whether setup shows the GPU step: an integrated GPU,
// a bigger share that changes what fits, and at least one model that gains
// from it still wanted.
func (m Model) gpuStepShown() bool {
	if m.gpu == nil || !m.gpu.Applies || !m.gpu.ShowInSetup || m.gpu.Method == setupapi.MethodUnsupported {
		return false
	}
	if len(m.gpu.Presets) == 0 || (m.gpu.Method == setupapi.MethodGuided && m.gpu.GuidedValue == nil) {
		// Nothing this PC can give.
		return false
	}
	for _, g := range m.gpu.Models {
		if m.gpuHelps(g) {
			return true
		}
	}
	return false
}

// gpuHelps reports whether a bigger GPU share is worth offering for g: it
// would then fit, and either the user wants it (ticked, or their chat model)
// or it is GAIA's default chat model for this PC, which starts unticked on a
// cold Strix Halo only because it doesn't fit yet. The default stops counting
// once the user unticks it or picks their own chat model.
func (m Model) gpuHelps(g setupapi.GPUModel) bool {
	if g.FitsNow || g.FitsAtSettingGB == nil {
		return false
	}
	if m.sel.wants(g.ID) {
		return true
	}
	return g.Default && !m.sel.untickedByHand(g.ID) && !m.sel.chatChosen
}

// These screens are one step of the host's setup ("choose and download the
// models"), so they keep its number and count; the models, GPU memory and the
// download are parts of that step.
func (m Model) stepLabel(s screen) string {
	part := m.partOf(s)
	if part == 0 {
		return ""
	}
	n := m.before() + 1
	of := n
	if m.opts.StepsTotal > of {
		of = m.opts.StepsTotal
	}
	label := fmt.Sprintf("step %d of %d", n, of)
	if parts := m.partCount(); parts > 1 {
		label += fmt.Sprintf(" · part %d of %d", part, parts)
	}
	return label
}

func (m Model) partCount() int {
	n := 1
	if m.gpuStepShown() {
		n++
	}
	if m.needsDownload() {
		n++
	}
	return n
}

func (m Model) partOf(s screen) int {
	switch s {
	case scrModels, scrChatPick:
		return 1
	case scrGPU, scrGuide:
		return 2
	case scrDownload:
		if m.gpuStepShown() {
			return 3
		}
		return 2
	}
	return 0
}

func (m Model) before() int {
	if m.opts.StepsBefore > 0 {
		return m.opts.StepsBefore
	}
	return 1
}

func (m Model) needsDownload() bool {
	if m.dl.active || len(m.dl.ids) > 0 {
		return true
	}
	return len(m.sel.pending()) > 0
}

// --- the download -----------------------------------------------------------

func (m Model) startDownload() (Model, tea.Cmd) {
	pending := m.sel.pending()
	if len(pending) == 0 {
		if m.opts.Mode == ModeModels {
			m.note = "Saved."
			return m, nil
		}
		m.scr = scrReady
		return m, nil
	}
	m.scr = scrDownload
	m.since = m.opts.Now()
	m.focus = 0
	m.note, m.runErr = "", ""
	m.dl = download{active: true}
	m.rates = map[string]*rate{}
	m.total = rate{}
	m.shown = map[string]float64{}
	for _, r := range pending {
		m.dl.ids = append(m.dl.ids, r.id)
	}
	// Setup's own download runs `gaia init`, which also loads each model to
	// check it; anything else ticked downloads one at a time after it.
	runs := false
	for _, r := range pending {
		if r.planned && m.opts.Mode == ModeFirstRun {
			runs = true
		} else {
			m.dl.queue = append(m.dl.queue, r.id)
		}
	}
	if runs {
		m.dl.run = true
		m.dl.starting = true
		return m, tea.Batch(m.runCmd(), m.tickCmd())
	}
	return m.nextSingle()
}

func (m Model) runCmd() tea.Cmd {
	api := m.api
	req := setupapi.RunRequest{SkipChatModel: m.opts.Cloud, AcknowledgeLowDisk: m.ackedDisk, KeepTrying: m.keepTry}
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		ack, err := api.Run(ctx, req)
		return startedMsg{ack: ack, err: err}
	}
}

// nextSingle starts the next queued single download, or finishes.
func (m Model) nextSingle() (Model, tea.Cmd) {
	if len(m.dl.queue) == 0 {
		m.dl.current = ""
		return m, m.pollNow()
	}
	id := m.dl.queue[0]
	m.dl.queue = m.dl.queue[1:]
	m.dl.current = id
	m.dl.starting = true
	api, ack, keep := m.api, m.ackedDisk, m.keepTry
	return m, tea.Batch(func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		a, err := api.Download(ctx, id, ack, keep)
		return startedMsg{id: id, ack: a, err: err}
	}, m.tickCmd())
}

func (m Model) started(msg startedMsg) (Model, tea.Cmd) {
	m.dl.starting = false
	m.dl.startedAt = m.opts.Now()
	if msg.ack != nil {
		// The backend's own disk check disagreed with the plan's: ask.
		m.ask, m.askAck = askLowDisk, msg.ack
		if msg.id != "" {
			m.dl.queue = append([]string{msg.id}, m.dl.queue...)
			m.dl.current = ""
		}
		m.dl.active = false
		m = m.toModels()
		return m, nil
	}
	if msg.err != nil {
		if msg.id == "" {
			m.dl.active = false
			m.note = "Could not start the download: " + msg.err.Error()
			m.scr = scrDownload
			return m, nil
		}
		// One model would not start; the rest still can.
		m.note = msg.err.Error()
		return m.nextSingle()
	}
	// A new run or a resume: the overall speed starts over with it.
	m.total = rate{}
	return m, m.startPolling()
}

func (m *Model) startPolling() tea.Cmd {
	m.polling = true
	m.lost = nil
	return tea.Batch(m.pollNow(), m.tickCmd())
}

func (m *Model) pollNow() tea.Cmd {
	if m.inFlight {
		return nil
	}
	m.inFlight = true
	m.lastPoll = m.opts.Now()
	return m.statusCmd()
}

func (m Model) statusArrived(msg statusMsg) (Model, tea.Cmd) {
	m.inFlight = false
	if m.dl.active && (m.dl.starting || msg.issued.Before(m.dl.startedAt)) {
		// Read before the backend took the new download: not its result.
		// started() polls again once it has.
		return m, nil
	}
	if msg.err != nil {
		// Never shown as progress: a stale bar would read as a download that
		// is still moving. The user reconnects with r.
		m.lost = msg.err
		return m, nil
	}
	m.lost = nil
	prev := m.status
	st := msg.st
	m.status = &st
	for _, row := range st.Models {
		r := m.rates[row.ID]
		if r == nil {
			r = &rate{}
			m.rates[row.ID] = r
		}
		if row.State == setupapi.StateDownloading {
			if wasNot(prev, row.ID, setupapi.StateDownloading) {
				// Resumed or retried: the last attempt's speed says nothing.
				*r = rate{}
			}
			r.observe(msg.at, row.BytesDone, row.BytesTotal)
		}
		if _, seen := m.shown[row.ID]; !seen {
			// First sight (or a download attached to): start where it is,
			// never animate up from zero.
			m.shown[row.ID] = frac(row.BytesDone, row.BytesTotal)
		}
		if row.State == setupapi.StateReady && wasNot(prev, row.ID, setupapi.StateReady) {
			m.readyAt[row.ID] = msg.at
		}
		if _, ok := m.verifyAt[row.ID]; !ok && row.State == setupapi.StateVerifying {
			m.verifyAt[row.ID] = msg.at
		}
		if row.BytesDone > m.kept[row.ID] {
			m.kept[row.ID] = row.BytesDone
		}
	}
	done, total := m.overallBytes()
	m.total.observe(msg.at, done, total)
	if m.opts.Motion.Reduced {
		m.snapShown()
	}
	return m.advance()
}

func wasNot(prev *setupapi.Status, id, state string) bool {
	if prev == nil {
		return true
	}
	for _, r := range prev.Models {
		if r.ID == id {
			return r.State != state
		}
	}
	return true
}

// advance moves the download on: the next single download, or the end.
func (m Model) advance() (Model, tea.Cmd) {
	if !m.dl.active {
		return m, nil
	}
	st := m.status
	if m.dl.run {
		if st.Running() {
			return m, m.tickCmd()
		}
		m.dl.run = false
		if st.State == setupapi.RunCancelled {
			m.dl.cancelled = true
		}
		if st.State == setupapi.RunFailed && st.Error != nil {
			// Said on the summary too: the models may all be here and setup
			// still have stopped on something else.
			m.runErr = *st.Error
		}
		if st.State == setupapi.RunIdle {
			// The run is gone without an ending: GAIA's background service
			// restarted under it. What finished is on disk; R resumes.
			m.note = runVanished
		}
	}
	if m.dl.current != "" {
		row, ok := m.progressFor(m.dl.current)
		if !ok || row.State == setupapi.StateDownloading || row.State == setupapi.StateQueued {
			return m, m.tickCmd()
		}
		m.dl.current = ""
	}
	if len(m.dl.queue) > 0 && !m.dl.cancelled {
		return m.nextSingle()
	}
	m.dl.active = false
	m.polling = false
	if m.allReady() {
		m = m.checkFinished()
		return m.refreshPlan()
	}
	m.focus = m.rootCause()
	return m, nil
}

// rootCause is the row to put the cursor on when a download ends with
// failures: the first one that failed for a reason of its own (a full disk, a
// dropped connection), ahead of models the run merely stopped before checking.
func (m Model) rootCause() int {
	first := -1
	for i, r := range m.shownRows() {
		if m.shownState(r) != setupapi.StateFailed {
			continue
		}
		if r.ErrorKind == nil || *r.ErrorKind != setupapi.ErrVerify {
			return i
		}
		if first < 0 {
			first = i
		}
	}
	if first < 0 {
		return m.focus
	}
	return first
}

// checkFinished moves a finished download on: the ready summary in setup, a
// note in /models. A failed model holds it until retried or skipped.
func (m Model) checkFinished() Model {
	if m.scr != scrDownload || m.dl.active || !m.allReady() {
		return m
	}
	if m.opts.Mode == ModeFirstRun {
		m.scr = scrReady
	} else {
		m.note = "Downloads finished."
	}
	return m
}

// refreshPlan re-reads the plan so "On this PC" reflects what just landed.
func (m Model) refreshPlan() (Model, tea.Cmd) {
	api := m.api
	return m, func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
		defer cancel()
		p, err := api.Plan(ctx)
		return removedMsg{plan: p, err: err}
	}
}

// progressFor is the status row for id.
func (m Model) progressFor(id string) (setupapi.ModelProgress, bool) {
	if m.status == nil {
		return setupapi.ModelProgress{}, false
	}
	for _, r := range m.status.Models {
		if sameID(r.ID, id) {
			return r, true
		}
	}
	return setupapi.ModelProgress{}, false
}

// shownRows are the models the download screen lists: every one the step
// downloads, plus setup's models already on disk.
func (m Model) shownRows() []setupapi.ModelProgress {
	var out []setupapi.ModelProgress
	seen := map[string]bool{}
	if m.status != nil {
		for _, r := range m.status.Models {
			if r.State == setupapi.StateSkipped {
				continue
			}
			out = append(out, r)
			seen[r.ID] = true
		}
	}
	for _, id := range m.dl.ids {
		found := false
		for k := range seen {
			if sameID(k, id) {
				found = true
			}
		}
		if !found {
			var total int64
			if r, ok := m.sel.find(id); ok {
				total = int64(r.left() * 1e9)
			}
			out = append(out, setupapi.ModelProgress{ID: id, State: setupapi.StateQueued, BytesTotal: total})
		}
	}
	return out
}

// allReady reports whether every listed model is on this PC.
func (m Model) allReady() bool {
	rows := m.shownRows()
	if len(rows) == 0 {
		return false
	}
	for _, r := range rows {
		if m.shownState(r) != setupapi.StateReady {
			return false
		}
	}
	return true
}

// doneBytes is what of r is on disk: what it reports while it downloads, and
// never less than was seen before while it waits.
func (m Model) doneBytes(r setupapi.ModelProgress) int64 {
	if r.State == setupapi.StateDownloading {
		return r.BytesDone
	}
	if k := m.kept[r.ID]; k > r.BytesDone {
		return k
	}
	return r.BytesDone
}

// shownState is a row's state as the user can act on it. "verifying" means
// downloaded and waiting for setup's load check; once no run is going to do
// that check (stopped, or finished), the model is simply on this PC.
func (m Model) shownState(r setupapi.ModelProgress) string {
	if r.State == setupapi.StateVerifying && !(m.dl.active && m.dl.run) {
		return setupapi.StateReady
	}
	return r.State
}

// othersDownloading reports whether a model is still downloading or queued,
// so a downloaded one is waiting for the end-of-run check, not in it.
func (m Model) othersDownloading() bool {
	for _, r := range m.shownRows() {
		if r.State == setupapi.StateDownloading || r.State == setupapi.StateQueued {
			return true
		}
	}
	return false
}

func (m Model) failedCount() int {
	n := 0
	for _, r := range m.shownRows() {
		if r.State == setupapi.StateFailed {
			n++
		}
	}
	return n
}

// Sizes on these screens all come from the plan, so the download screen
// counts the same gigabytes "Choose your models" promised. The backend's byte
// counts give only how far each download is.

// sizeOf is a model's full size in bytes: the plan's, else what the download
// reports.
func (m Model) sizeOf(r setupapi.ModelProgress) int64 {
	if e, ok := m.sel.find(r.ID); ok && e.fullGB > 0 {
		return int64(e.fullGB * 1e9)
	}
	return r.BytesTotal
}

// inPlanBytes rescales bytes the download reports to the plan's size.
func (m Model) inPlanBytes(r setupapi.ModelProgress, b int64) int64 {
	size := m.sizeOf(r)
	if r.BytesTotal <= 0 || size == r.BytesTotal {
		return b
	}
	return int64(frac(b, r.BytesTotal) * float64(size))
}

// overallBytes is what this step downloads, in the plan's "Download" figure:
// each model's size still to download when the step began, and how much of it
// has arrived since.
func (m Model) overallBytes() (done, total int64) {
	for _, r := range m.shownRows() {
		if !m.counts(r.ID) {
			continue
		}
		var t, before int64
		if e, ok := m.sel.find(r.ID); ok && e.leftGB != nil {
			t = int64(e.left() * 1e9)
			before = int64(e.partialGB * 1e9)
		} else {
			t = r.BytesTotal
		}
		d := m.inPlanBytes(r, m.doneBytes(r)) - before
		if st := m.shownState(r); st == setupapi.StateReady || st == setupapi.StateVerifying {
			// Downloaded: the backend reports a model already on disk with no
			// byte count, and a resumed run must not drop the bar back.
			d = t
		}
		if d > t {
			d = t
		}
		if d < 0 {
			d = 0
		}
		done += d
		total += t
	}
	return done, total
}

func (m Model) counts(id string) bool {
	for _, d := range m.dl.ids {
		if sameID(d, id) {
			return true
		}
	}
	return false
}

func (m Model) overallPercent() int {
	done, total := m.overallBytes()
	if total <= 0 {
		return 0
	}
	return int(done * 100 / total)
}

// ease moves each bar's shown fill toward its real value, at most 10 Hz, so
// 1 s status reads grow smoothly instead of in jumps.
func (m *Model) ease() {
	if m.opts.Motion.Reduced {
		return
	}
	for _, r := range m.shownRows() {
		target := frac(r.BytesDone, r.BytesTotal)
		cur := m.shown[r.ID]
		if target < cur || target-cur < 0.002 {
			m.shown[r.ID] = target
			continue
		}
		m.shown[r.ID] = cur + (target-cur)*0.35
	}
	done, total := m.overallBytes()
	target := frac(done, total)
	cur := m.shown[""]
	if target < cur || target-cur < 0.002 {
		m.shown[""] = target
	} else {
		m.shown[""] = cur + (target-cur)*0.35
	}
}

func (m *Model) snapShown() {
	for _, r := range m.shownRows() {
		m.shown[r.ID] = frac(r.BytesDone, r.BytesTotal)
	}
	done, total := m.overallBytes()
	m.shown[""] = frac(done, total)
}

func frac(done, total int64) float64 {
	if total <= 0 {
		return 0
	}
	f := float64(done) / float64(total)
	if f > 1 {
		return 1
	}
	return f
}

// --- GPU --------------------------------------------------------------------

func (m Model) gpuArrived(msg gpuMsg) (Model, tea.Cmd) {
	m.busy = ""
	if msg.err != nil {
		switch {
		case setupapi.IsStatus(msg.err, 403):
			m.gpuPhase = gpuDenied
			m.gpuNote = gpuDeniedText
		default:
			m.gpuPhase = gpuIdle
			m.gpuNote = msg.err.Error()
		}
		return m, nil
	}
	g := msg.gpu
	m.gpu = &g
	target, pool := m.targetGB(), m.targetPool()
	switch msg.action {
	case "apply":
		m.gpuPhase = gpuApplied
		m.gpuNote = gpuDone(formatGBShort(pool))
		m.gpuDone = true
	case "reread":
		if g.Setting.CurrentGB >= target-0.5 {
			m.gpuNote = gpuTookEffect(g.GPUGB)
			m.gpuDone = true
			return m, nil
		}
		// Not in effect yet: a restart applies it. Remember it so the next
		// start confirms the new split instead of trusting this one.
		m.busy = "Saving the change for after the restart…"
		m.busySince = m.opts.Now()
		api := m.api
		return m, func() tea.Msg {
			ctx, cancel := context.WithTimeout(context.Background(), callTimeout)
			defer cancel()
			g2, err := api.GPUPending(ctx, target)
			return gpuMsg{gpu: g2, err: err, action: "pending"}
		}
	case "pending":
		m.gpuPhase = gpuPendingSaved
		m.gpuNote = gpuPendingLine(formatGBShort(pool))
		m.gpuDone = true
	case "keep":
		m.gpuNote = ""
		m.gpuPhase = gpuIdle
	case "revert":
		m.gpuPhase = gpuApplied
		m.gpuNote = gpuDone(m.poolFor(g.Setting.CurrentGB))
	case "guide":
		// guided_steps rendered for the chosen value.
	}
	return m, nil
}

// targetPreset is the preset the user has selected.
func (m Model) targetPreset() setupapi.GPUPreset {
	if m.gpu == nil || len(m.gpu.Presets) == 0 {
		return setupapi.GPUPreset{}
	}
	i := m.gpuTarget
	if i < 0 || i >= len(m.gpu.Presets) {
		i = 0
	}
	return m.gpu.Presets[i]
}

// targetGB is the setting to change (Variable Graphics Memory, or the GTT
// limit): what the apply and guide calls take.
func (m Model) targetGB() float64 { return m.targetPreset().SettingGB }

// targetPool is what the GPU gets for models at that setting: what the user
// reads, since "the GPU has 24 GB now" counts the same way.
func (m Model) targetPool() float64 { return m.targetPreset().GPUGB }

// poolFor is the GPU memory a setting gives, in words. A setting no preset
// names is never shown as if it were GPU memory.
func (m Model) poolFor(setting float64) string {
	if m.gpu != nil {
		for _, p := range m.gpu.Presets {
			if p.SettingGB == setting {
				return formatGBShort(p.GPUGB)
			}
		}
	}
	return "the new GPU memory"
}

func (m Model) gpuCmd(action string, setting float64) tea.Cmd {
	api := m.api
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
		defer cancel()
		var (
			g   setupapi.GPU
			err error
		)
		switch action {
		case "apply":
			g, err = api.GPUApply(ctx, setting)
		case "revert":
			g, err = api.GPURevert(ctx)
		case "keep":
			g, err = api.GPUKeep(ctx)
		default:
			g, err = api.GPU(ctx, setting)
		}
		return gpuMsg{gpu: g, err: err, action: action}
	}
}

// --- helpers ----------------------------------------------------------------

func (m Model) freeGB() *float64 {
	if m.plan == nil {
		return nil
	}
	return m.plan.Disk.FreeGB
}

func (m Model) drive() string {
	if m.plan == nil {
		return "the model drive"
	}
	return driveOf(m.plan.Disk.Path)
}

// item is one line the cursor can sit on: a model, or the line that opens
// and closes the "already in Lemonade" group.
type item struct {
	row   rowEntry
	group bool
}

// items are the checklist lines in screen order: setup's and GAIA's models,
// the group line, then the group's models while it is open.
func (m Model) items() []item {
	var out []item
	for _, r := range m.sel.rows {
		if !r.installed {
			out = append(out, item{row: r})
		}
	}
	if m.installedCount() == 0 {
		return out
	}
	out = append(out, item{group: true})
	if m.showAll {
		for _, r := range m.sel.rows {
			if r.installed {
				out = append(out, item{row: r})
			}
		}
	}
	return out
}

// installedCount is how many models sit in the "already in Lemonade" group.
func (m Model) installedCount() int {
	n := 0
	for _, r := range m.sel.rows {
		if r.installed {
			n++
		}
	}
	return n
}

func (m *Model) clampFocus() {
	if n := len(m.items()); m.focus >= n {
		m.focus = n - 1
	}
	if m.focus < 0 {
		m.focus = 0
	}
}

func (m *Model) clampScroll() {
	if m.scroll < 0 {
		m.scroll = 0
	}
}

// focusedRow is the model under the cursor; false on the group line.
func (m Model) focusedRow() (rowEntry, bool) {
	items := m.items()
	if m.focus >= 0 && m.focus < len(items) && !items[m.focus].group {
		return items[m.focus].row, true
	}
	return rowEntry{}, false
}

// toModels goes back to the checklist, or to the load error when there is no
// plan to show (the recommended set was used without one).
func (m Model) toModels() Model {
	if m.plan == nil {
		m.scr = scrLoadError
		return m
	}
	m.scr = scrModels
	return m
}

// nameOf is a model's display name: the plan's, else its id as people read it.
func (m Model) nameOf(id string) string {
	if r, ok := m.sel.find(id); ok && r.name != "" {
		return r.name
	}
	if m.plan != nil {
		for _, e := range m.plan.Catalog {
			if sameID(e.ID, id) {
				return displayName(e)
			}
		}
	}
	return shortName(id)
}
