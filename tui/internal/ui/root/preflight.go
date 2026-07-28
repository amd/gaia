package root

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// beginPreflight puts the readiness gate between the hub and the chat.
//
// The gate runs on EVERY launch and nothing is remembered between them. Each row
// is a fact with no expiry notice — the daemon can be killed, its client token
// rotates on every restart, Lemonade can unload the model, a mailbox grant can be
// revoked from the web — so a cached pass would open a chat that cannot answer
// while claiming it had been verified. An all-green pass costs one attach, two
// relayed GETs, and one bounded relayed mailbox read, and is held ~800ms; that is
// the price of the answer being true now.
func (m RootModel) beginPreflight(agent catalog.Agent) (tea.Model, tea.Cmd) {
	if agent.Transport != catalog.TransportDaemon {
		// Every precondition the gate reports is probed THROUGH the daemon relay
		// (GET /v1/<agent>/init). A subprocess agent has no relay and no
		// registered sidecar, so the gate could only answer "not installed" —
		// four wrong rows with four wrong remedies, over a launch that works.
		return m.launchAgent(agent)
	}

	gate := preflight.New(m.preflightTransport(), preflight.ConfigFor(agent.ID, agent.Name), m.preflightOptions())
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

// preflightTransport builds the gate's transport on first use and keeps it for
// the session: it caches the daemon instance whose token authorized the last
// call, and that token rotates on every daemon restart. Building it lazily also
// keeps a session that never launches anything from constructing a daemon client.
func (m *RootModel) preflightTransport() preflight.Transport {
	if m.pfTransport == nil {
		m.pfTransport = preflight.NewDaemonTransport(daemon.New(daemon.Options{Logf: m.logf}))
	}
	return m.pfTransport
}

// preflightOptions returns the gate's options. ManualProceed is deliberately NOT
// wired to --debug: the gate's footer only offers `enter` while a report is
// neither blocked nor ready, so holding an all-green screen would leave a debug
// user on a green wall whose only advertised keys are re-check and back.
func (m RootModel) preflightOptions() preflight.Options {
	opts := m.pfOpts
	if opts.Logf == nil {
		opts.Logf = m.logf
	}
	return opts
}

// gateIsFor reports whether the gate on screen is the one that sent this
// message. A ProceedMsg from a gate the user already backed out of must not
// launch anything — the hold tick that produced it can still be in flight.
func (m RootModel) gateIsFor(agentID string) bool {
	return m.activeView == viewPreflight && m.preflight != nil &&
		m.pending != nil && m.pending.ID == agentID
}

// closeGate tears the gate down, cancelling what it started: a re-check, a
// sidecar start, a model pull.
//
// It does NOT stop the FIRST probe. preflight.Model.Init has a value receiver, so
// the cancel func for the check it launches is parked on a copy that dies with
// the call, and the model this host keeps has none. That probe therefore runs to
// its own 90s timeout after the user leaves; its result is dropped by
// updatePreflight rather than allowed to drive whatever is on screen. Fixing it
// properly needs a pointer-receiver initialiser in the preflight package.
//
// Since the mailbox row started proving its credentials, that abandoned check can
// include one live read of the user's mailbox, capped by the row's own 5s bound
// rather than the 90s one — so it is bounded and read-only, but it is no longer
// only loopback traffic.
func (m *RootModel) closeGate() {
	if m.preflight != nil {
		m.preflight.Cancel()
		m.preflight = nil
	}
	m.pending = nil
	m.connect = nil
}

func (m RootModel) proceedFromGate() (tea.Model, tea.Cmd) {
	agent := *m.pending
	m.closeGate()
	// Back to the hub FIRST. launchAgent stays where it is and reports the reason
	// when a client cannot be built, and "where it is" has to be a screen that
	// still renders — the gate is gone by now.
	m.activeView = viewHub
	return m.launchAgent(agent)
}

// cancelFromGate returns to the hub. The hub model is untouched while the gate is
// up, so its tab and highlighted row are exactly where the user left them.
func (m RootModel) cancelFromGate() (tea.Model, tea.Cmd) {
	rep := m.preflight.Report()
	m.closeGate()
	m.activeView = viewHub

	// Backing out of a gate that had a real blocker leaves the user staring at a
	// hub that looks like nothing happened. Say which row stopped it.
	if blocker, blocked := rep.Blocker(); blocked {
		// One line, and no trailing call to action: the hub truncates its status
		// row at the terminal width, and a hint that gets cut mid-word is worse
		// than the footer's own "enter run".
		m.hub.SetStatus(fmt.Sprintf("%s did not start — %s is %s",
			rep.AgentName, blocker.Label, blocker.Line))
	}

	var cmds []tea.Cmd
	if m.width > 0 && m.height > 0 {
		cmds = append(cmds, m.sizeCmd())
	}
	return m, tea.Batch(cmds...)
}

func (m RootModel) sizeCmd() tea.Cmd {
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
func (m RootModel) updatePreflight(msg tea.Msg) (tea.Model, tea.Cmd) {
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

func (m RootModel) openConnectHandoff(provider string) (tea.Model, tea.Cmd) {
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
func (m RootModel) handleConnectKey(key tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch key.String() {
	case "ctrl+c":
		// Same as everywhere else: ctrl+c leaves the app, not just the screen. The
		// view goes back to the hub so the last frame is never a view whose model
		// has just been torn out from under it.
		m.closeGate()
		m.activeView = viewHub
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
	connectTitle   = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("150"))
	connectDim     = lipgloss.NewStyle().Foreground(lipgloss.Color("243"))
	connectDivider = lipgloss.NewStyle().Foreground(lipgloss.Color("238"))
	connectText    = lipgloss.NewStyle().Foreground(lipgloss.Color("252"))
	connectCmd     = lipgloss.NewStyle().Foreground(lipgloss.Color("150"))
	connectKey     = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("39"))
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
