package root

import (
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/preflight"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// beginPreflight puts the readiness gate between the splash and the chat.
//
// The gate runs on EVERY launch and nothing is remembered between them. Each row
// is a fact with no expiry notice — the daemon can be killed, its client token
// rotates on every restart, Lemonade can unload the model, a mailbox grant can be
// revoked from the web — so a cached pass would open a chat that cannot answer
// while claiming it had been verified.
//
// WHICH gate depends on how the agent is reached, and that used to decide
// whether there was one at all. A subprocess agent has no relay and no
// registered sidecar, so the daemon runner's rows could only ever answer "not
// installed" — four wrong rows with four wrong remedies over a launch that
// works — and the launch was let through ungated instead. It now gets the LOCAL
// runner, which probes the same facts directly on this machine.
func (m FlagshipModel) beginPreflight(agent catalog.Agent) (tea.Model, tea.Cmd) {
	cfg := preflight.ConfigFor(agent.ID, agent.Name)
	var gate preflight.Model
	if agent.Transport == catalog.TransportDaemon {
		gate = preflight.New(m.preflightTransport(), cfg, m.preflightOptions())
	} else {
		gate = preflight.NewLocal(m.localOptions(agent), cfg, m.preflightOptions())
	}
	m.preflight = &gate
	m.pending = &agent
	m.connect = nil
	m.activeView = viewPreflight

	cmds := []tea.Cmd{gate.Init()}
	if m.width > 0 && m.height > 0 {
		cmds = append(cmds, m.sizeCmd())
	}
	return m, tea.Batch(cmds...)
}

// localOptions describes the agent to the local runner: the binary the launch
// will actually spawn, and whether this session runs on Claude.
func (m FlagshipModel) localOptions(agent catalog.Agent) preflight.LocalOptions {
	if m.pfLocal != nil {
		return *m.pfLocal
	}
	return preflight.LocalOptions{Binary: agent.BinaryPath, ClaudeMode: m.useClaude}
}

// preflightTransport builds the gate's transport on first use and keeps it for
// the session: it caches the daemon instance whose token authorized the last
// call, and that token rotates on every daemon restart. Building it lazily also
// keeps a session that never launches anything from constructing a daemon client.
func (m *FlagshipModel) preflightTransport() preflight.Transport {
	if m.pfTransport == nil {
		m.pfTransport = preflight.NewDaemonTransport(daemon.New(daemon.Options{Logf: m.logf}))
	}
	return m.pfTransport
}

// preflightOptions returns the gate's options. ManualProceed is deliberately NOT
// wired to --debug: the gate's footer only offers `enter` while a report is
// neither blocked nor ready, so holding an all-green screen would leave a debug
// user on a green wall whose only advertised keys are re-check and back.
func (m FlagshipModel) preflightOptions() preflight.Options {
	opts := m.pfOpts
	if opts.Logf == nil {
		opts.Logf = m.logf
	}
	return opts
}

// gateIsFor reports whether the gate on screen is the one that sent this
// message. A ProceedMsg from a gate the user already backed out of must not
// launch anything — the hold tick that produced it can still be in flight.
func (m FlagshipModel) gateIsFor(agentID string) bool {
	return m.activeView == viewPreflight && m.preflight != nil &&
		m.pending != nil && m.pending.ID == agentID
}

// closeGate tears the gate down, cancelling everything it started — the first
// probe included. That last part was not true until preflight.Model started
// holding its cancel func behind a pointer (see cancelBox): an abandoned check
// used to run to its own 90s timeout, and once a fix could spawn `gaia init`,
// ctrl+c during preflight would have left that child running.
func (m *FlagshipModel) closeGate() {
	if m.preflight != nil {
		m.preflight.Cancel()
		m.preflight = nil
	}
	m.pending = nil
	m.connect = nil
	// Whatever was halting belonged to THIS gate session — leaving it, by
	// proceeding or backing out, resolves it. Automation's Overlay must not
	// keep reading "halt" once the gate that raised it is gone.
	m.halted = nil
}

func (m FlagshipModel) proceedFromGate() (tea.Model, tea.Cmd) {
	agent := *m.pending
	// The gate has just asked whether setup is ready, so the chat's own
	// first-boot check must not ask again — each costs a fresh Python
	// interpreter for up to 30s, and a cold launch was paying that twice.
	verified := gateAskedAboutSetup(m.preflight.Report())
	// Before closeGate clears halted: proceeding IS the deliberate choice
	// the halt exists to gate, so mark these StepIDs accepted for the rest
	// of the session before the record of them is gone.
	m.suppressHalted()
	m.closeGate()
	return m.launchAgent(agent, verified)
}

// gateAskedAboutSetup reports whether the AI model row was actually PROBED —
// not whether it passed.
//
// Suppressing only on a pass looked stricter and was worse. An indeterminate
// row (`gaia` not on PATH, so `gaia init --check` cannot answer) is a state the
// gate already resolved, named on screen, and deliberately proceeded past. The
// chat then re-ran the identical doomed probe and reported the identical
// finding a second time — as a red ERROR, which is louder than the notice the
// gate gave. One question, asked once, answered once.
//
// A StateFailed row never reaches here: the gate blocks on it. StatePending
// means the walk stopped at an earlier failure and never got to this row, so
// nothing was asked and the chat still should.
func gateAskedAboutSetup(rep preflight.Report) bool {
	row, ok := rep.Find(preflight.KeyModel)
	return ok && row.State != preflight.StatePending
}

// cancelFromGate leaves. There is no screen behind the gate to go back to: the
// splash is a frame, not a destination, and the one agent this TUI runs is the
// one the user just declined to start.
func (m FlagshipModel) cancelFromGate() (tea.Model, tea.Cmd) {
	rep := m.preflight.Report()
	m.closeGate()

	// Say which row it was on the way out — the one that refused the launch, or
	// the one the user was being asked to fix. To stderr, because the alt screen
	// is about to be torn down and anything drawn into it goes with it.
	if row, found := attentionRow(rep); found {
		fmt.Fprintf(os.Stderr, "%s did not start — %s is %s\n",
			rep.AgentName, row.Label, row.Line)
	}
	return m, tea.Quit
}

// haltOnLaunchFailure reports a client that could not be built and leaves.
// Reached when the agent binary goes away between the check and the spawn —
// rare, but opening a chat that cannot talk is the one outcome the gate exists
// to prevent.
func (m FlagshipModel) haltOnLaunchFailure(agent catalog.Agent, err error) (tea.Model, tea.Cmd) {
	fmt.Fprintf(os.Stderr, "%s could not start: %v\n", agent.Name, err)
	return m, tea.Quit
}

// attentionRow is the row worth naming after a gate the user left: the one that
// refused the launch, or — when nothing did — the one it was asking them to fix.
func attentionRow(rep preflight.Report) (preflight.Row, bool) {
	if blocker, blocked := rep.Blocker(); blocked {
		return blocker, true
	}
	idx := rep.FirstAttention()
	if idx < 0 || rep.Rows[idx].State != preflight.StateFailed {
		return preflight.Row{}, false
	}
	return rep.Rows[idx], true
}

func (m FlagshipModel) sizeCmd() tea.Cmd {
	w, h := m.width, m.height
	return func() tea.Msg { return tea.WindowSizeMsg{Width: w, Height: h} }
}

// updatePreflight forwards a message to the gate, then refuses any result that
// turns out to belong to a gate the user already left.
//
// The gate's async results are unexported types, so they cannot be filtered
// before the fact — a probe started for agent A and answered after the user
// backed out and launched agent B lands on B's gate. Its report is exported and
// names its agent, so the check is done after the update: a report for the wrong
// agent is dropped along with whatever command it wanted to run. Without this, an
// all-green report for A drives B's screen, and B's gate hands off — opening a
// chat for an agent nothing ever probed, which is the one outcome this gate
// exists to prevent.
func (m FlagshipModel) updatePreflight(msg tea.Msg) (tea.Model, tea.Cmd) {
	if m.preflight == nil {
		return m, nil
	}
	updated, cmd := m.preflight.Update(msg)
	gate := updated.(preflight.Model)
	if id := gate.Report().AgentID; id != "" && m.pending != nil && id != m.pending.ID {
		return m, nil
	}
	m.preflight = &gate
	return m, cmd
}

// --- the mailbox hand-off ---------------------------------------------------

// connectHandoff is what ConnectMailboxMsg turns into.
//
// The TUI has no connector screen, and half of one would be worse than none:
// OAuth already has a working implementation behind `gaia connectors`, and a
// second one here would fork the flow that owns tokens, scopes and grants. So the
// gate hands over the exact command — the one the mailbox row already computed,
// with the scope union and the agent grant in it — and re-checks when the user
// comes back. Swallowing the request, or answering it with "coming soon", would
// leave the user on a screen that names a problem and no way past it.
type connectHandoff struct {
	agentName string
	// provider is the mailbox to reconnect. Empty means the user has nothing
	// connected and the choice of provider is still theirs.
	provider string
	row      preflight.Row
	haveRow  bool
}

func (m FlagshipModel) openConnectHandoff(provider string) (tea.Model, tea.Cmd) {
	rep := m.preflight.Report()
	row, ok := rep.Find(preflight.KeyMailbox)
	m.connect = &connectHandoff{
		agentName: rep.AgentName,
		provider:  provider,
		row:       row,
		haveRow:   ok,
	}
	return m, nil
}

// handleConnectKey owns every key while the hand-off is up.
func (m FlagshipModel) handleConnectKey(key tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch key.String() {
	case "ctrl+c":
		// Same as everywhere else: ctrl+c leaves the app, not just the screen.
		// Back to the splash so the last frame is never a view whose model has
		// just been torn out from under it.
		m.closeGate()
		m.activeView = viewSplash
		return m, tea.Quit
	case "r":
		// Dismiss, then let the gate's own `r` do the re-check — the screen that
		// owns the probes owns the re-check too.
		m.connect = nil
		return m.updatePreflight(key)
	case "esc", "q":
		m.connect = nil
		return m, nil
	}
	return m, nil
}

var (
	connectTitle   = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)
	connectDim     = lipgloss.NewStyle().Foreground(theme.Dim)
	connectDivider = lipgloss.NewStyle().Foreground(theme.Divider)
	connectText    = lipgloss.NewStyle().Foreground(theme.Text)
	connectCmd     = lipgloss.NewStyle().Foreground(theme.Accent)
	connectKey     = lipgloss.NewStyle().Bold(true).Foreground(theme.Info)
)

// outlookSetupDoc is where the Outlook path continues. It is a doc, not a
// command, because Outlook needs a one-time Microsoft app registration (or the
// device-code flow) before any connect command can succeed — and the mailbox
// row's Gmail scopes are wrong for Microsoft, so "swap google for microsoft"
// would hand the user a command that fails.
const outlookSetupDoc = "https://amd-gaia.ai/docs/connectors/microsoft"

func (h connectHandoff) view(width, height int) string {
	w := width
	if w < 40 {
		w = 40
	}

	title := fmt.Sprintf("Connect a mailbox for %s", h.agentName)
	if h.provider != "" {
		title = fmt.Sprintf("Reconnect %s for %s", providerLabel(h.provider), h.agentName)
	}

	head := []string{
		"  " + connectTitle.Render(title),
		"  " + connectDivider.Render(strings.Repeat("─", w-4)),
	}
	var body []block
	cur := dropContext
	add := func(style lipgloss.Style, text string, indent int) {
		prefix := strings.Repeat(" ", indent)
		for _, line := range wrapLines(text, w-indent-2) {
			body = append(body, block{line: prefix + style.Render(line), drop: cur})
		}
	}
	blank := func() { body = append(body, block{line: "", drop: cur}) }

	if h.row.Detail != "" {
		add(connectDim, h.row.Detail, 2)
		blank()
	}

	switch {
	case !h.haveRow || h.row.Remedy.Command == "":
		// The gate asked for a mailbox and recorded no command to get one. That is
		// a bug, and it must not read as a shrug.
		add(connectText, "The readiness check asked for a mailbox connection but recorded no "+
			"command for it. Report that, then connect from a terminal:", 2)
		cur = dropNever
		add(connectCmd, "gaia connectors list", 4)
		cur = dropContext
		add(connectDim, "look:  https://amd-gaia.ai/docs/guides/email", 4)

	case h.provider != "":
		add(connectText, "This cannot be done from here — it opens a browser sign-in. Run this in "+
			"another terminal, then come back and press r.", 2)
		// The command is printed verbatim, so if the check produced one for a
		// different provider than the mailbox it is talking about, say so rather
		// than let the title vouch for it.
		if named := providerFromCommand(h.row.Remedy.Command); named != "" && named != h.provider {
			cur = dropNever
			add(connectText, fmt.Sprintf(
				"Heads up: the check produced a %s command for a %s mailbox. Check it before "+
					"running it, and report it.", providerLabel(named), providerLabel(h.provider)), 2)
		}
		cur = dropNever
		blank()
		add(connectCmd, h.row.Remedy.Command, 4)
		cur = dropContext
		if h.row.Remedy.Where != "" {
			blank()
			add(connectDim, "look:  "+h.row.Remedy.Where, 4)
		}

	default:
		// Provider is empty: nothing is connected, so the choice is the user's.
		// Both paths are named, and neither is a command that would fail.
		add(connectText, "This cannot be done from here — it opens a browser sign-in. "+
			"Pick one, run it in another terminal, then come back and press r.", 2)
		offered := providerFromCommand(h.row.Remedy.Command)
		heading := "Run this:"
		if offered != "" {
			heading = providerLabel(offered) + " — one command, about a minute:"
		}
		cur = dropNever
		blank()
		add(connectText, heading, 2)
		add(connectCmd, h.row.Remedy.Command, 4)
		if offered != "microsoft" {
			cur = dropAlternative
			blank()
			add(connectText, "Outlook — needs a one-time Microsoft app setup first:", 2)
			add(connectDim, outlookSetupDoc, 4)
		}
	}

	foot := "  " + connectKey.Render("r") + " " + connectDim.Render("re-check") +
		connectDim.Render(" · ") + connectKey.Render("esc") + " " + connectDim.Render("back to the checks")

	out := append(head, fitBody(body, height-len(head)-2)...)
	return strings.Join(append(out, "", foot), "\n")
}

// Which lines the hand-off gives up first when the terminal is too short. The
// command sits in the MIDDLE of the screen, so trimming by position — from
// either end — can eat the one thing the user came here for. Lines are dropped
// by what they are instead.
const (
	// dropNever is the command itself and the sentence that introduces it.
	dropNever = iota
	// dropAlternative is the second way to do it (the Outlook pointer).
	dropAlternative
	// dropContext is explanation: why this is needed, where to read more.
	dropContext
)

// block is one rendered line plus how readily it can be dropped.
type block struct {
	line string
	drop int
}

// fitBody drops whole categories of line, least important first, until the body
// fits room rows. Below that it hard-trims and lets the caller's footer stand —
// a terminal that short is under every size this app supports.
func fitBody(body []block, room int) []string {
	if room < 1 {
		room = 1
	}
	for prio := dropContext; prio > dropNever && len(body) > room; prio-- {
		kept := body[:0:0]
		for _, b := range body {
			if b.drop != prio {
				kept = append(kept, b)
			}
		}
		body = kept
	}
	lines := make([]string, 0, len(body))
	for _, b := range body {
		lines = append(lines, b.line)
	}
	if len(lines) > room {
		lines = lines[:room]
	}
	return lines
}

// providerFromCommand reads the connector id out of a
// `gaia connectors connect <id> …` command, so the heading above a command can
// never name a provider the command does not use.
func providerFromCommand(cmd string) string {
	fields := strings.Fields(cmd)
	for i, f := range fields {
		if f == "connect" && i+1 < len(fields) {
			return fields[i+1]
		}
	}
	return ""
}

func providerLabel(provider string) string {
	switch provider {
	case "google":
		return "Gmail"
	case "microsoft":
		return "Outlook"
	case "microsoft_work":
		return "Microsoft 365"
	default:
		return provider
	}
}

// wrapLines wraps plain text to w columns, hard-breaking a single token that is
// longer than the line — a connect command with full OAuth scope URLs in it must
// be readable in full, never truncated.
func wrapLines(s string, w int) []string {
	if w < 8 {
		w = 8
	}
	rendered := lipgloss.NewStyle().Width(w).Render(s)
	lines := strings.Split(strings.TrimRight(rendered, "\n"), "\n")
	for i := range lines {
		// Width() pads every line to w; the padding would show up as trailing
		// whitespace in a captured screen.
		lines[i] = strings.TrimRight(lines[i], " ")
	}
	return lines
}
