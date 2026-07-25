package preflight

import (
	"context"
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
	// everything is ready. Used by tests and by `--debug`.
	ManualProceed bool
	// Logf receives diagnostics. Never given a token.
	Logf func(format string, args ...any)
}

// Model is the readiness gate screen.
type Model struct {
	cfg  Config
	t    Transport
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
	cancel        context.CancelFunc
}

// New builds the gate for one agent.
func New(t Transport, cfg Config, opts Options) Model {
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
		t:      t,
		opts:   opts,
		rep:    Report{AgentID: cfg.AgentID, AgentName: cfg.AgentName, Rows: checkingRows(cfg)},
		phase:  phaseChecking,
		width:  80,
		height: 24,
		spin:   s,
	}
}

// checkingRows is the first frame: the shape of the answer before any of it is
// known, so the screen does not jump as rows arrive.
func checkingRows(cfg Config) []Row {
	rows := blankRows(cfg)
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

// Cancel stops any in-flight probe or fix. The host calls it when tearing the
// screen down so a model pull does not keep writing into a dead screen.
func (m *Model) Cancel() {
	if m.cancel != nil {
		m.cancel()
		m.cancel = nil
	}
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

func (m Model) checkCmd() tea.Cmd {
	t, cfg := m.t, m.cfg
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), checkTimeout)
		defer cancel()
		return reportMsg{rep: Check(ctx, t, cfg)}
	}
}

func (m Model) startDaemonCmd() tea.Cmd {
	t := m.t
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), startTimeout)
		defer cancel()
		if _, err := t.Start(ctx); err != nil {
			return fixDoneMsg{key: KeyDaemon, err: err}
		}
		return fixDoneMsg{key: KeyDaemon, note: "Background service started."}
	}
}

func (m Model) ensureAgentCmd() tea.Cmd {
	t, cfg := m.t, m.cfg
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), ensureTimeout)
		defer cancel()
		if err := t.EnsureAgent(ctx, cfg.AgentID); err != nil {
			return fixDoneMsg{key: KeySidecar, err: err}
		}
		return fixDoneMsg{key: KeySidecar, note: cfg.AgentName + " agent started."}
	}
}

func waitProvision(ch chan provisionEvent) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-ch
		if !ok {
			return provisionMsg{ch: ch, event: provisionEvent{done: true}}
		}
		return provisionMsg{ch: ch, event: ev}
	}
}

func (m Model) startProvision() (Model, tea.Cmd) {
	ch := make(chan provisionEvent, 64)
	ctx, cancel := context.WithTimeout(context.Background(), provisionTimeout)
	m.Cancel()
	m.cancel = cancel
	m.provisionCh = ch
	// The daemon relay BUFFERS non-SSE responses (src/gaia/daemon/relay.py), so
	// the sidecar's progress lines all arrive at the end of the pull rather than
	// as it runs. Promise minutes, not a progress bar, until the relay streams
	// text/plain too.
	m.provisionLine = "downloading the model — the first pull takes several minutes"
	m.phase = phaseProvisioning
	m.note = ""

	t, cfg := m.t, m.cfg
	go func() {
		defer close(ch)
		res := Provision(ctx, t, cfg, func(line string) {
			// Never block on a screen that stopped reading — a cancelled gate
			// must not park this goroutine for the length of a model pull.
			select {
			case ch <- provisionEvent{line: line}:
			case <-ctx.Done():
			}
		})
		select {
		case ch <- provisionEvent{done: true, result: res}:
		case <-ctx.Done():
		}
	}()
	return m, tea.Batch(waitProvision(ch), m.spin.Tick)
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
		if m.rep.Ready() && !m.opts.ManualProceed {
			m.phase = phaseDone
			return m, tea.Tick(m.opts.ReadyHold, func(time.Time) tea.Msg { return proceedTickMsg{} })
		}
		return m, nil

	case proceedTickMsg:
		return m, m.proceed()

	case fixDoneMsg:
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
			return m, waitProvision(msg.ch)
		}
		m.provisionCh = nil
		m.Cancel()
		res := msg.event.result
		if res.OK {
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
		m.provisionCh = nil
		return m, tea.Quit

	case "esc", "q":
		m.Cancel()
		m.provisionCh = nil
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
		m.phase = phaseChecking
		m.rep.Rows = checkingRows(m.cfg)
		return m, tea.Batch(m.checkCmd(), m.spin.Tick)

	case "enter":
		if m.Busy() || m.rep.Blocked() {
			return m, nil
		}
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
		return m, tea.Batch(m.startDaemonCmd(), m.spin.Tick)
	case FixStartSidecar:
		m.phase = phaseFixing
		m.note = "Starting the " + m.cfg.AgentName + " agent…"
		return m, tea.Batch(m.ensureAgentCmd(), m.spin.Tick)
	case FixPullModel:
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

func (m Model) proceed() tea.Cmd {
	agentID := m.cfg.AgentID
	return func() tea.Msg { return ProceedMsg{AgentID: agentID} }
}

func (m Model) View() string { return m.render() }
