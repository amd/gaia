package preflight

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	tea "github.com/charmbracelet/bubbletea"
)

// ProceedMsg tells the host every precondition is satisfied (or the user
// accepted an indeterminate one) and the agent may be launched.
type ProceedMsg struct{ AgentID string }

// CancelMsg tells the host the user backed out of the launch.
type CancelMsg struct{ AgentID string }

// ConnectMailboxMsg asks the host to open the connector flow for Provider. The
// gate deliberately does not run OAuth itself: that flow is its own screen, and
// owning it here would fork it.
type ConnectMailboxMsg struct {
	AgentID  string
	Provider string
}

// Timeouts for the work the screen kicks off. Each is bounded so a wedged
// daemon or sidecar surfaces as an actionable row instead of a frozen screen.
const (
	checkTimeout     = 90 * time.Second
	startTimeout     = 60 * time.Second
	ensureTimeout    = 15 * time.Minute
	provisionTimeout = 60 * time.Minute
	// defaultReadyHold is how long an all-green screen is held so the user sees
	// what was verified before chat replaces it.
	defaultReadyHold = 800 * time.Millisecond
	// unknownHold is the longer hold used when nothing failed but something
	// could not be verified — long enough to read what went unproven.
	unknownHold = 2500 * time.Millisecond
)

type phase int

const (
	phaseChecking phase = iota
	phaseIdle
	phaseFixing
	phaseProvisioning
	phaseDone
)

// Options tunes the screen. The zero value is valid.
type Options struct {
	// ReadyHold is how long an all-ready report is shown before ProceedMsg.
	// Defaults to 800ms.
	ReadyHold time.Duration
	// ManualProceed keeps the screen up until the user presses enter, even when
	// everything is ready. Test-only: the sole caller of WithPreflight is
	// test/preflight_gate_test.go, not any interactive flag.
	ManualProceed bool
	// Logf receives diagnostics. Never given a token.
	Logf func(format string, args ...any)
}

// cancelBox holds the cancel func for whatever background work the screen has
// in flight.
//
// A POINTER field on Model, shared by every copy Bubble Tea makes, because
// Init() has to satisfy tea.Model's value receiver: a plain context.CancelFunc
// stored there is parked on a copy that dies with the call, so the model the
// host keeps has none and Cancel() cannot stop the first probe. Abandoning the
// screen then left that probe running to its own 90s timeout — and with a fix
// that spawns `gaia init`, ctrl+c during preflight would leak a child.
type cancelBox struct {
	mu sync.Mutex
	fn context.CancelFunc
}

func (c *cancelBox) set(fn context.CancelFunc) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.fn = fn
}

// cancel stops the work in flight and forgets it. Safe to call more than once.
func (c *cancelBox) cancel() {
	c.mu.Lock()
	fn := c.fn
	c.fn = nil
	c.mu.Unlock()
	if fn != nil {
		fn()
	}
}

// Model is the readiness gate screen. It renders whatever Runner it was built
// with — see runner.go for why there is one screen and two runners.
type Model struct {
	cfg  Config
	r    Runner
	opts Options

	rep     Report
	phase   phase
	focus   int
	details bool
	// note is a transient line under the rows: what a fix did, or why it failed.
	note string

	width, height int
	spin          spinner.Model

	provisionCh   chan provisionEvent
	provisionLine string
	cancel        *cancelBox

	// fixApplied is true once a fix has changed something on this machine —
	// setup ran, a daemon or sidecar started, a model downloaded.
	//
	// It stops the re-check that follows from handing off on its own. A fix can
	// run for minutes and then print the one line that says whether it worked;
	// auto-proceeding ~800ms later replaces that line with a chat prompt, so
	// the user never learns what the thing they waited for actually did. After
	// a fix the screen waits for enter. A launch that needed no fix is
	// untouched — nothing happened there worth pausing over.
	fixApplied bool
}

// New builds the gate for an agent the GAIA daemon supervises.
func New(t Transport, cfg Config, opts Options) Model {
	return newWith(NewDaemonRunner(t), cfg, opts)
}

// NewLocal builds the gate for an agent the TUI spawns itself. Same screen,
// same keys, different probes — see runner.go.
func NewLocal(local LocalOptions, cfg Config, opts Options) Model {
	return newWith(NewLocalRunner(local), cfg, opts)
}

func newWith(r Runner, cfg Config, opts Options) Model {
	if opts.ReadyHold == 0 {
		opts.ReadyHold = defaultReadyHold
	}
	if opts.Logf == nil {
		opts.Logf = func(string, ...any) {}
	}
	cfg = cfg.withDefaults()

	s := spinner.New()
	s.Spinner = spinner.Dot

	return Model{
		cfg:    cfg,
		r:      r,
		opts:   opts,
		rep:    Report{AgentID: cfg.AgentID, AgentName: cfg.AgentName, Rows: checkingRows(r, cfg)},
		phase:  phaseChecking,
		width:  80,
		height: 24,
		spin:   s,
		cancel: &cancelBox{},
	}
}

// checkingRows is the first frame: the shape of the answer before any of it is
// known, so the screen does not jump as rows arrive.
func checkingRows(r Runner, cfg Config) []Row {
	rows := r.Rows(cfg)
	rows[0].State = StateChecking
	rows[0].Line = "checking…"
	return rows
}

func (m Model) Init() tea.Cmd {
	return tea.Batch(m.checkCmd(), m.spin.Tick)
}

// Report is the current readiness answer — for an integrator's status line or
// control-API snapshot.
func (m Model) Report() Report { return m.rep }

// Ready reports whether every precondition passed.
func (m Model) Ready() bool { return m.rep.Ready() }

// Busy reports whether a probe or a fix is in flight.
func (m Model) Busy() bool {
	return m.phase == phaseChecking || m.phase == phaseFixing || m.phase == phaseProvisioning
}

// FocusKey is the row the user is on, for snapshots and tests.
func (m Model) FocusKey() string {
	if m.focus < 0 || m.focus >= len(m.rep.Rows) {
		return ""
	}
	return m.rep.Rows[m.focus].Key
}

// AgentID is the agent this gate is guarding.
func (m Model) AgentID() string { return m.cfg.AgentID }

// Cancel stops any in-flight probe or fix — the first check included. The host
// calls it when tearing the screen down so a model pull, or a `gaia init` child,
// does not outlive the screen that started it.
func (m Model) Cancel() {
	if m.cancel != nil {
		m.cancel.cancel()
	}
}

// RunnerLabel names the machinery behind this screen, for logs and snapshots.
func (m Model) RunnerLabel() string { return m.r.Label() }

// HeldForReview reports whether the screen is deliberately waiting for enter
// rather than handing off. The renderer asks this rather than re-deriving it,
// so the footer and the hand-off can never disagree about whether the launch
// is about to start.
func (m Model) HeldForReview() bool {
	return (m.opts.ManualProceed || m.fixApplied) && !m.Busy() && !m.rep.Blocked()
}

// --- messages --------------------------------------------------------------

type reportMsg struct{ rep Report }
type fixDoneMsg struct {
	key  string
	err  error
	note string
}
type provisionEvent struct {
	line   string
	done   bool
	result ProvisionResult
}
type provisionMsg struct {
	ch    chan provisionEvent
	event provisionEvent
}
type proceedTickMsg struct{}

// --- commands --------------------------------------------------------------

// begin builds the context for a piece of background work and parks its cancel
// on the model, so Cancel() stops EVERYTHING the screen started — not just a
// download. An abandoned ensure would otherwise keep spawning a sidecar minutes
// after the user left.
func (m Model) begin(timeout time.Duration) context.Context {
	m.Cancel()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	m.cancel.set(cancel)
	return ctx
}

func (m Model) checkCmd() tea.Cmd {
	ctx := m.begin(checkTimeout)
	r, cfg := m.r, m.cfg
	return func() tea.Msg { return reportMsg{rep: r.Check(ctx, cfg)} }
}

// quickFixCmd runs a fix that finishes in one call and reports a note.
func (m Model) quickFixCmd(key string, kind FixKind, timeout time.Duration) tea.Cmd {
	ctx := m.begin(timeout)
	r, cfg := m.r, m.cfg
	return func() tea.Msg {
		res := r.Fix(ctx, cfg, kind, nil)
		return fixDoneMsg{key: key, err: res.Err, note: res.Note}
	}
}

func waitProvision(ch chan provisionEvent, cfg Config) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-ch
		if ok {
			return provisionMsg{ch: ch, event: ev}
		}
		// The producer closed without a terminal event: the only way here is a
		// cancelled or timed-out pull. Say so — an empty "Download failed." with
		// no cause and no remedy is exactly the silent failure the rules forbid.
		return provisionMsg{ch: ch, event: provisionEvent{
			done: true,
			result: ProvisionResult{
				Final: "✗ the download stopped before it finished",
				Diagnosis: Diagnosis{
					Cause:   "The model download was cancelled, or ran past the time limit.",
					Remedy:  "Download it in a terminal instead, then press r to re-check.",
					Command: "gaia init",
					Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", cfg.AgentID),
				},
			},
		}}
	}
}

// send delivers ev, preferring delivery over an already-cancelled context.
//
// A plain `select { case ch <- ev: case <-ctx.Done(): }` picks UNIFORMLY when
// both are ready, so a pull that finished exactly as its deadline expired lost
// its result half the time — and the screen then had a failure with no cause
// and no remedy. The non-blocking attempt first makes delivery deterministic
// whenever the buffer has room; the fallback still refuses to park forever on a
// reader that is gone.
func send(ctx context.Context, ch chan provisionEvent, ev provisionEvent) {
	select {
	case ch <- ev:
		return
	default:
	}
	select {
	case ch <- ev:
	case <-ctx.Done():
	}
}

// focusFix is the fix on the focused row, FixNone when there is none.
func (m Model) focusFix() FixKind {
	if m.focus < 0 || m.focus >= len(m.rep.Rows) {
		return FixNone
	}
	return m.rep.Rows[m.focus].Fix
}

// provisionOpeningLine promises minutes rather than a progress bar.
//
// The daemon relay BUFFERS non-SSE responses (src/gaia/daemon/relay.py), so a
// model pull's progress lines all arrive at the END of the pull rather than as
// it runs. `gaia init` is a local child whose pipes are read live, so that one
// really does narrate.
func provisionOpeningLine(kind FixKind) string {
	if kind == FixRunSetup {
		return "running setup — the first run takes several minutes"
	}
	return "downloading the model — the first pull takes several minutes"
}

func (m Model) startProvision() (Model, tea.Cmd) {
	ch := make(chan provisionEvent, 64)
	ctx := m.begin(provisionTimeout)
	m.provisionCh = ch
	m.provisionLine = provisionOpeningLine(m.focusFix())
	m.phase = phaseProvisioning
	m.note = ""

	r, cfg, kind := m.r, m.cfg, m.focusFix()
	go func() {
		defer close(ch)
		res := r.Fix(ctx, cfg, kind, func(line string) {
			send(ctx, ch, provisionEvent{line: line})
		})
		send(ctx, ch, provisionEvent{done: true, result: ProvisionResult{
			OK:        res.OK(),
			Final:     res.Final,
			Diagnosis: res.Diagnosis,
		}})
	}()
	return m, tea.Batch(waitProvision(ch, m.cfg), m.spin.Tick)
}

// --- update ----------------------------------------------------------------

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		return m, nil

	case spinner.TickMsg:
		if !m.Busy() {
			return m, nil
		}
		var cmd tea.Cmd
		m.spin, cmd = m.spin.Update(msg)
		return m, cmd

	case reportMsg:
		m.rep = msg.rep
		m.phase = phaseIdle
		// The rows now say everything the "…re-checking" note did.
		m.note = ""
		if idx := m.rep.FirstAttention(); idx >= 0 {
			m.focus = idx
		} else {
			m.focus = 0
		}
		// A row the user can fix with one keypress holds the screen even when it
		// does not block: the mailbox row is exactly that, and handing off past it
		// would take `f connect a mailbox` away with the screen it was on.
		if !m.rep.Blocked() && !m.opts.ManualProceed && !m.fixApplied && !m.rep.HasOneKeyFix() {
			if m.rep.HasHalt() {
				// Three edits together: the tick, the phase, and the note each
				// independently claim the launch is starting.
				m.note = "Waiting — " + m.unverifiedSummary()
				return m, m.haltOutcomesCmd()
			}
			// Nothing failed, and nothing unproven is consequential enough to
			// hold for. Indeterminate rows do not block the launch — the
			// sidecar itself does not treat an unadvertised version as fatal, and
			// making the user press enter on EVERY launch against such a server
			// would train them to press it without reading. They are held longer
			// and named, not silently skipped.
			hold := m.opts.ReadyHold
			if !m.rep.Ready() {
				hold = unknownHold
				m.note = "Starting anyway — " + m.unverifiedSummary()
			}
			m.phase = phaseDone
			return m, tea.Tick(hold, func(time.Time) tea.Msg { return proceedTickMsg{} })
		}
		return m, nil

	case proceedTickMsg:
		// A tick already in flight when the user pressed esc or r must not
		// launch the agent they backed out of.
		if m.phase != phaseDone {
			return m, nil
		}
		return m, m.proceed()

	case fixDoneMsg:
		if msg.err == nil {
			m.fixApplied = true
		}
		if msg.err != nil {
			d := Ladder{AgentID: m.cfg.AgentID}.Error("apply that fix", msg.err)
			m.note = "Fix failed. " + d.String()
			m.phase = phaseIdle
			return m, nil
		}
		m.note = msg.note + " Re-checking…"
		m.phase = phaseChecking
		return m, tea.Batch(m.checkCmd(), m.spin.Tick)

	case provisionMsg:
		if msg.ch != m.provisionCh {
			// A late delivery from a cancelled pull: ignore it rather than let
			// it drive the screen that replaced it.
			return m, nil
		}
		if !msg.event.done {
			m.provisionLine = msg.event.line
			return m, waitProvision(msg.ch, m.cfg)
		}
		m.provisionCh = nil
		m.Cancel()
		res := msg.event.result
		if res.OK {
			m.fixApplied = true
			m.note = "Download complete. Re-checking…"
			m.phase = phaseChecking
			return m, tea.Batch(m.checkCmd(), m.spin.Tick)
		}
		m.phase = phaseIdle
		m.note = "Download failed. " + res.Diagnosis.String()
		if res.Diagnosis.Cause == "" {
			m.note = "Download failed. " + res.Final
		}
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m Model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "ctrl+c":
		// Matches the hub and chat: ctrl+c leaves the app, not just the screen.
		m.Cancel()
		m.provisionCh, m.phase = nil, phaseIdle
		return m, tea.Quit

	case "esc", "q":
		m.Cancel()
		// Back to idle, not left in phaseProvisioning/phaseDone: Busy() would
		// otherwise stay true forever and a re-shown gate would refuse every key.
		m.provisionCh, m.phase = nil, phaseIdle
		m.note = ""
		agentID := m.cfg.AgentID
		return m, func() tea.Msg { return CancelMsg{AgentID: agentID} }

	case "d":
		m.details = !m.details
		return m, nil

	case "up", "k":
		if m.focus > 0 {
			m.focus--
		}
		return m, nil

	case "down", "j":
		if m.focus < len(m.rep.Rows)-1 {
			m.focus++
		}
		return m, nil

	case "r":
		if m.Busy() {
			return m, nil
		}
		m.note = ""
		m.details = false
		m.fixApplied = false
		m.phase = phaseChecking
		m.rep.Rows = checkingRows(m.r, m.cfg)
		return m, tea.Batch(m.checkCmd(), m.spin.Tick)

	case "enter":
		if m.Busy() {
			return m, nil
		}
		if blocker, blocked := m.rep.Blocker(); blocked && !m.rep.OfferableDespiteFailure() {
			m.note = fmt.Sprintf("%s cannot start yet: %s is %s. Fix that row first.",
				m.cfg.AgentName, blocker.Label, blocker.Line)
			return m, nil
		}
		m.phase = phaseIdle
		return m, m.proceed()

	case "f":
		if m.Busy() {
			return m, nil
		}
		return m.applyFix()
	}
	return m, nil
}

func (m Model) applyFix() (tea.Model, tea.Cmd) {
	if m.focus < 0 || m.focus >= len(m.rep.Rows) {
		return m, nil
	}
	row := m.rep.Rows[m.focus]
	switch row.Fix {
	case FixStartDaemon:
		m.phase = phaseFixing
		m.note = "Starting the background service…"
		return m, tea.Batch(m.quickFixCmd(KeyDaemon, FixStartDaemon, startTimeout), m.spin.Tick)
	case FixStartSidecar:
		m.phase = phaseFixing
		m.note = "Starting the " + m.cfg.AgentName + " agent…"
		return m, tea.Batch(m.quickFixCmd(KeySidecar, FixStartSidecar, ensureTimeout), m.spin.Tick)
	case FixPullModel, FixRunSetup:
		return m.startProvision()
	case FixConnectMailbox:
		agentID, provider := m.cfg.AgentID, row.Provider
		return m, func() tea.Msg {
			return ConnectMailboxMsg{AgentID: agentID, Provider: provider}
		}
	default:
		if row.Remedy.Command != "" {
			m.note = "Nothing here can be fixed safely from the TUI — run: " + row.Remedy.Command
		}
		return m, nil
	}
}

// unverifiedSummary names what could not be proved, for the line shown while an
// indeterminate report is handed off.
//
// A known failure outranks an unproven row: if the launch proceeds over a broken
// mailbox, saying "could not be verified" would understate a state we measured.
func (m Model) unverifiedSummary() string {
	for _, row := range m.rep.Rows {
		if row.State == StateFailed {
			return row.Label + " is not working (" + row.Line +
				") — ask " + m.cfg.AgentName + " to fix it."
		}
	}
	for _, row := range m.rep.Rows {
		if row.State == StateUnknown {
			return row.Label + " could not be verified (" + row.Line + ")."
		}
	}
	return "not everything could be verified."
}

func (m Model) proceed() tea.Cmd {
	agentID := m.cfg.AgentID
	return func() tea.Msg { return ProceedMsg{AgentID: agentID} }
}

// haltOutcomesCmd emits one status.Outcome per HaltingRows() row, for the
// host's listener to act on. Only reached from the HasHalt() branch above —
// a Blocked() report never calls this, so an offerable failure (a mailbox
// the agent repairs in conversation) never gets a second prompt in front of
// the one it already offers.
func (m Model) haltOutcomesCmd() tea.Cmd {
	rows := m.rep.HaltingRows()
	cmds := make([]tea.Cmd, 0, len(rows))
	for _, row := range rows {
		o := row.Outcome()
		cmds = append(cmds, func() tea.Msg { return o })
	}
	return tea.Batch(cmds...)
}

func (m Model) View() string { return m.render() }
