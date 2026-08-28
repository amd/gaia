package preflight

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// ExtraCheck is a per-agent precondition appended after the four generic ones.
//
// The first four rows are the same for any sidecar agent that implements
// GET /v1/<agent>/init. Anything agent-specific — for email, a mailbox that is
// connected, granted send, AND proven readable — arrives here, so the screen
// stays generic.
type ExtraCheck struct {
	Key   string
	Label string
	// Optional marks a check that gates a CAPABILITY rather than the launch —
	// see Row.Optional. Its verdict is reported in full; it just does not stop
	// the walk or refuse the run.
	Optional bool
	Run      func(ctx context.Context, t Transport, cfg Config) Row
}

// Config parameterises a readiness check.
type Config struct {
	// AgentID is the daemon-registered id, e.g. "email". It is the `<agent>` in
	// every relayed path and in every remedy command.
	AgentID string
	// AgentName is the display name, e.g. "Email".
	AgentName string
	// Extras run after the generic checks, in order.
	Extras []ExtraCheck
}

func (c Config) withDefaults() Config {
	if c.AgentID == "" {
		c.AgentID = "email"
	}
	if c.AgentName == "" {
		// Rune-wise: byte-slicing corrupts a non-ASCII id into mojibake.
		r := []rune(c.AgentID)
		c.AgentName = strings.ToUpper(string(r[:1])) + string(r[1:])
	}
	return c
}

// EmailConfig is the built-in configuration for the email agent: the four
// generic checks plus the mailbox check.
func EmailConfig() Config {
	return Config{
		AgentID:   "email",
		AgentName: "Email",
		Extras:    []ExtraCheck{MailboxCheck()},
	}
}

// ConfigFor is what a launch site calls: the generic checks for any agent, plus
// whatever extras that agent has. One call site, so adding an agent's extra
// check never means finding every place a gate is built.
func ConfigFor(agentID, agentName string) Config {
	cfg := Config{AgentID: agentID, AgentName: agentName}.withDefaults()
	if cfg.AgentID == "email" {
		cfg.Extras = []ExtraCheck{MailboxCheck()}
	}
	return cfg
}

// Check probes every precondition in dependency order and returns a typed
// report. It is safe to call headlessly — the screen is a renderer over this.
//
// Dependency order matters and the walk STOPS at the first failure: "the model
// is not downloaded" is meaningless when the model server is down, and
// GET /v1/<agent>/init already orders its own hints that way (version-too-old
// before model-missing). Rows after a failure are Pending, never OK.
func Check(ctx context.Context, t Transport, cfg Config) Report {
	cfg = cfg.withDefaults()

	rep := Report{
		AgentID:   cfg.AgentID,
		AgentName: cfg.AgentName,
		Rows:      blankRows(cfg),
	}

	steps := []func(context.Context, Transport, Config, *Report) State{
		checkDaemon,
		checkSidecar,
		checkInit, // fills BOTH the lemonade and model rows from one probe
	}
	for _, step := range steps {
		if state := step(ctx, t, cfg, &rep); state == StateFailed {
			markPending(&rep)
			return rep
		}
	}

	for _, extra := range cfg.Extras {
		row := extra.Run(ctx, t, cfg)
		row.Key, row.Label, row.Optional = extra.Key, extra.Label, extra.Optional
		setRow(&rep, row)
		// An optional row is reported, never a stop sign: it gates a capability
		// the ask may not need, and halting here would hide the rows below it.
		if row.State == StateFailed && !extra.Optional {
			markPending(&rep)
			return rep
		}
	}
	return rep
}

// blankRows lays out every row up front so a report always has the same shape —
// a screen that grows rows as they are answered makes the user re-read it.
func blankRows(cfg Config) []Row {
	rows := []Row{
		{Key: KeyDaemon, Label: "Background service"},
		{Key: KeySidecar, Label: cfg.AgentName + " agent"},
		{Key: KeyLemonade, Label: lemonadeRowLabel},
		{Key: KeyModel, Label: modelRowLabel},
	}
	for _, extra := range cfg.Extras {
		rows = append(rows, Row{Key: extra.Key, Label: extra.Label, Optional: extra.Optional})
	}
	for i := range rows {
		rows[i].State = StatePending
		rows[i].Line = "—"
	}
	return rows
}

func setRow(rep *Report, row Row) {
	for i := range rep.Rows {
		if rep.Rows[i].Key == row.Key {
			row.Label = rep.Rows[i].Label
			rep.Rows[i] = row
			return
		}
	}
	rep.Rows = append(rep.Rows, row)
}

// markPending annotates every still-unanswered row with what it is waiting on,
// so a blank row never reads as "fine" or as "broken".
func markPending(rep *Report) {
	blocker, ok := rep.Blocker()
	if !ok {
		return
	}
	for i := range rep.Rows {
		if rep.Rows[i].State == StatePending && rep.Rows[i].Line == "—" {
			rep.Rows[i].Detail = fmt.Sprintf("checked once %q is fixed", blocker.Label)
		}
	}
}

// ---------------------------------------------------------------------------
// 1. The daemon: alive, ours, and speaking a contract we can use.
// ---------------------------------------------------------------------------

func checkDaemon(ctx context.Context, t Transport, cfg Config, rep *Report) State {
	l := Ladder{AgentID: cfg.AgentID}
	row := Row{Key: KeyDaemon}

	inf, err := t.Attach(ctx)
	if err != nil {
		d := l.Error("reach the background service", err)
		row.State = StateFailed
		// The daemon is not agent-repairable — nothing downstream of it can be
		// checked, so there is no partial conversation to hand off to.
		row.Disposition = status.DispositionHalt
		row.Line = "not running"
		row.Detail = d.Cause
		row.Remedy = d.AsRemedy()
		row.Raw = err.Error()
		var missing *daemon.NotRunningError
		if errors.As(err, &missing) {
			// The line already says "not running"; the detail's job is to say
			// why that stops everything else.
			row.Detail = "Every agent on this machine runs under it, so nothing " +
				"else can be checked yet."
		}
		// Starting a daemon is safe and reversible; reclaiming a wedged one or
		// resolving a version skew is not, and must stay the user's call.
		if startable(err) {
			row.Fix = FixStartDaemon
			row.Line = "not running"
		} else {
			row.Line = "unusable"
		}
		return row.commit(rep)
	}

	row.State = StateOK
	row.Line = fmt.Sprintf("running (pid %d) · host API v%s", inf.PID, inf.APIVersion)
	return row.commit(rep)
}

// startable reports whether the failure is one that starting a daemon fixes. A
// version skew never is — a second daemon cannot come up alongside the first.
func startable(err error) bool {
	var notRunning *daemon.NotRunningError
	if errors.As(err, &notRunning) {
		return true
	}
	var stale *daemon.StaleError
	if errors.As(err, &stale) {
		return stale.Kind != daemon.StaleUnresponsive
	}
	return false
}

func (r Row) commit(rep *Report) State {
	setRow(rep, r)
	return r.State
}

// ---------------------------------------------------------------------------
// 2. The agent's sidecar: registered with the daemon, and running.
// ---------------------------------------------------------------------------

type agentsBody struct {
	Agents []struct {
		AgentID      string  `json:"agent_id"`
		State        string  `json:"state"`
		PID          *int    `json:"pid"`
		AgentVersion *string `json:"agent_version"`
		APIVersion   *string `json:"api_version"`
	} `json:"agents"`
}

func checkSidecar(ctx context.Context, t Transport, cfg Config, rep *Report) State {
	l := Ladder{AgentID: cfg.AgentID}
	row := Row{Key: KeySidecar}

	// KeySidecar is not agent-repairable — every branch below is Halt, never
	// Notify: the agent itself is not up, so there is no partial conversation
	// for it to repair.
	resp, err := t.Do(ctx, http.MethodGet, daemon.APIPrefix+"/agents", nil)
	if err != nil {
		d := l.Error("list the agents the background service supervises", err)
		row.State, row.Line, row.Detail, row.Remedy, row.Raw =
			StateFailed, "cannot be listed", d.Cause, d.AsRemedy(), err.Error()
		row.Disposition = status.DispositionHalt
		return row.commit(rep)
	}
	row.Raw = string(resp.Body)
	if resp.Status != http.StatusOK {
		d := l.Status("list the supervised agents", resp.Status, string(resp.Body))
		row.State, row.Line, row.Detail, row.Remedy =
			StateFailed, "cannot be listed", d.Cause, d.AsRemedy()
		row.Disposition = status.DispositionHalt
		return row.commit(rep)
	}

	var body agentsBody
	if err := json.Unmarshal(resp.Body, &body); err != nil {
		row.State, row.Line = StateFailed, "unreadable answer"
		row.Disposition = status.DispositionHalt
		row.Detail = "The background service answered with something this build cannot read."
		row.Remedy = Remedy{
			Action:  "Restart it so both sides speak the same contract.",
			Command: "gaia daemon restart",
			Where:   daemonLog(),
		}
		return row.commit(rep)
	}

	for _, a := range body.Agents {
		if a.AgentID != cfg.AgentID {
			continue
		}
		if a.State == "running" {
			live := probeSidecarAnswers(ctx, t, cfg)
			row.Raw = strings.TrimSpace(row.Raw + "\n\n" + live.trace)
			if !live.answered {
				row.State = StateFailed
				row.Line = live.line
				row.Detail = live.cause
				row.Remedy = live.remedy
				// `gaia daemon start-agent` ATTACHES to a registered sidecar, so a
				// one-key "start the agent" would report success and change nothing.
				row.Fix = FixNone
				return row.commit(rep)
			}
			row.State = StateOK
			row.Line = describeSidecar(a.AgentVersion, a.PID)
			return row.commit(rep)
		}
		row.State = StateFailed
		row.Disposition = status.DispositionHalt
		row.Line = "installed, not started"
		row.Detail = fmt.Sprintf("The %s agent is registered but its state is %q.", cfg.AgentName, a.State)
		row.Fix = FixStartSidecar
		row.Remedy = Remedy{
			Action:  "Start it — the background service supervises it from then on.",
			Command: "gaia daemon start-agent " + cfg.AgentID,
			Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", cfg.AgentID),
		}
		return row.commit(rep)
	}

	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Line = "not installed"
	row.Detail = fmt.Sprintf("The background service has no agent registered as %q.", cfg.AgentID)
	row.Remedy = Remedy{
		Action:  "Install it from the hub, then re-check.",
		Command: "gaia hub install " + cfg.AgentID,
		Where:   daemonLog(),
	}
	return row.commit(rep)
}

func describeSidecar(version *string, pid *int) string {
	parts := []string{}
	if version != nil && *version != "" {
		parts = append(parts, *version)
	}
	if pid != nil && *pid > 0 {
		parts = append(parts, fmt.Sprintf("running (pid %d)", *pid))
	} else {
		parts = append(parts, "running")
	}
	return strings.Join(parts, " · ")
}

// sidecarProbeTimeout bounds the one call that proves the agent's own process is
// serving. GET /v1/<agent>/health touches no mailbox, no model and no network,
// so a healthy sidecar answers in milliseconds on loopback; past this the
// process is not answering rather than answering slowly.
const sidecarProbeTimeout = 10 * time.Second

// sidecarLiveness is what the liveness probe established, plus the words for the
// row it produces.
type sidecarLiveness struct {
	answered bool
	// line is the row's one-liner, so the subject it names matches the cause.
	line   string
	cause  string
	remedy Remedy
	// trace is appended to Row.Raw so `d` shows the probe and what it cost.
	trace string
}

// probeSidecarAnswers asks the agent's own process to say something.
//
// The daemon's agent listing reports what its REGISTRY records, and a sidecar
// whose event loop is blocked keeps its pid, keeps its port, and serves nothing
// — so "running" is a claim about a process, not about an agent that answers.
// That gap is what put "cannot be checked" on the Mailbox row over an agent that
// had stopped answering every route it has.
//
// ANY answer proves the loop is alive: a 404 from an agent with no health route
// still came off it. Only a call that never produced one — a transport failure,
// or a relay saying it could not get an answer out of the sidecar — means the
// process is not serving.
func probeSidecarAnswers(ctx context.Context, t Transport, cfg Config) sidecarLiveness {
	path := "/v1/" + cfg.AgentID + "/health"

	ctx, cancel := context.WithTimeout(ctx, sidecarProbeTimeout)
	defer cancel()

	start := time.Now()
	resp, err := t.Do(ctx, http.MethodGet, path, nil)
	took := time.Since(start)
	trace := func(outcome string) string {
		return fmt.Sprintf("sidecar probe: GET %s -> %s in %dms", path, outcome, took.Milliseconds())
	}

	if err != nil {
		if !timedOut(err) {
			// Not the agent going quiet: the TUI dials exactly one host, so a
			// refused or reset connection is the background service itself.
			d := Ladder{AgentID: cfg.AgentID}.Error("reach the "+cfg.AgentName+" agent", err)
			return sidecarLiveness{
				line:   "cannot be reached",
				cause:  d.Cause,
				remedy: d.AsRemedy(),
				trace:  trace("no answer: " + err.Error()),
			}
		}
		return sidecarLiveness{
			line: "running, not answering",
			cause: fmt.Sprintf(
				"The background service says the %s agent is running, but the agent itself "+
					"never replied — it took the request and held it, so every call to it hangs, "+
					"this check included. Nothing below it can be checked until it answers.",
				cfg.AgentName),
			remedy: restartSidecarRemedy(cfg.AgentID),
			trace:  trace("no answer: " + err.Error()),
		}
	}

	detail := jsonDetail(resp.Body)
	outcome := fmt.Sprintf("HTTP %d %s", resp.Status, strings.TrimSpace(string(resp.Body)))
	if (resp.Status == http.StatusBadGateway || resp.Status == http.StatusServiceUnavailable) &&
		relayGaveUp(detail) {
		return sidecarLiveness{
			line: "running, not answering",
			cause: fmt.Sprintf(
				"The background service says the %s agent is running, but it could not get an "+
					"answer out of it. %s", cfg.AgentName, detailSuffix(firstSentence(detail))),
			remedy: restartSidecarRemedy(cfg.AgentID),
			trace:  trace(outcome),
		}
	}
	return sidecarLiveness{answered: true, trace: trace(outcome)}
}

// restartSidecarRemedy is the fix for a sidecar that is registered but not
// serving. It stops before it starts on purpose: `start-agent` spawns-or-ATTACHES,
// so on its own it attaches to the very process that is stuck and reports
// success. Only replacing the process clears it.
func restartSidecarRemedy(agentID string) Remedy {
	return Remedy{
		Action: "Stop the agent, then start it again — `start-agent` on its own attaches to " +
			"the process that is already registered, so it would change nothing.",
		Command: fmt.Sprintf("gaia daemon stop-agent %s && gaia daemon start-agent %s", agentID, agentID),
		Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", agentID),
	}
}

// timedOut reports whether a call was given up on rather than refused. The
// daemon client reports a deadline as text on its own error type rather than
// wrapping context.DeadlineExceeded, so both forms are checked.
func timedOut(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	return containsAny(strings.ToLower(err.Error()), "timed out", "timeout", "deadline exceeded")
}

// ---------------------------------------------------------------------------
// 3+4. GET /v1/<agent>/init — one probe, two rows.
// ---------------------------------------------------------------------------

type initBody struct {
	Ready bool `json:"ready"`
	// Pointers on purpose. A 503 from the RELAY (the sidecar died between the
	// agents listing and this call) carries `{"detail": ...}` and no `lemonade`
	// object at all; decoding that into a value struct would read as
	// "reachable:false" and send the user to start Lemonade over a dead sidecar.
	Lemonade *struct {
		Reachable  bool    `json:"reachable"`
		BaseURL    string  `json:"base_url"`
		Version    *string `json:"version"`
		MinVersion string  `json:"min_version"`
		// Compatible is null when the server does not advertise a version. That
		// is an indeterminate check, NOT a pass.
		Compatible *bool `json:"compatible"`
	} `json:"lemonade"`
	Model *struct {
		ID       string `json:"id"`
		Present  bool   `json:"present"`
		Loadable *bool  `json:"loadable"`
		CtxSize  *int   `json:"ctx_size"`
	} `json:"model"`
	Hint *string `json:"hint"`
}

func (b initBody) hint() string {
	if b.Hint == nil {
		return ""
	}
	return *b.Hint
}

// checkInit answers the Local AI and AI model rows, starting the local model
// server first if it is down.
//
// GAIA ships with a model server and manages it, so "Lemonade is not running"
// is not a question to put to the user — it is a thing to fix. The start is
// asked of the DAEMON (see Transport.StartLemonade): this process never spawns
// a server and never shells out to the Python CLI, and routing every front-end
// through the one custody process is what stops two launches racing into two
// servers fighting over the port.
//
// It is attempted exactly once. A second attempt after a genuine failure would
// re-wait the full start budget to reach the same answer, and every failure the
// starter reports (not installed, port held by a stranger, the server died) is
// one that a retry cannot change.
func checkInit(ctx context.Context, t Transport, cfg Config, rep *Report) State {
	state, startable := probeInit(ctx, t, cfg, rep)
	if !startable {
		return state
	}
	if !autoStartLemonade(ctx, t, cfg, rep) {
		return StateFailed
	}
	state, _ = probeInit(ctx, t, cfg, rep)
	return state
}

// autoStartLemonade asks the daemon to start the server and reports whether a
// re-probe is worth doing. On failure it leaves the Local AI row saying that
// GAIA tried, why it could not, and what the user can still do.
func autoStartLemonade(ctx context.Context, t Transport, cfg Config, rep *Report) bool {
	err := t.StartLemonade(ctx)
	if err == nil {
		return true
	}

	row := Row{Key: KeyLemonade, State: StateFailed, Disposition: status.DispositionHalt}
	row.Line = "not running — GAIA could not start it"
	row.Raw = err.Error()
	row.Fix = FixNone

	var refused *LemonadeStartRefused
	switch {
	case !errors.As(err, &refused):
		// The POST never reached the daemon, so the daemon is the subject — not
		// Lemonade. Diagnosing this as "start Lemonade" would name the wrong fix.
		d := Ladder{AgentID: cfg.AgentID}.Error("ask the background service to start the local AI", err)
		row.Line = "cannot be started"
		row.Detail = d.Cause
		row.Remedy = d.AsRemedy()
	case refused.TooOldToStartLemonade():
		row.Detail = "The installed GAIA core is older than this app and cannot start the " +
			"local model server for you. Upgrading it makes every launch do this automatically."
		row.Remedy = lemonadeStartRemedy()
	default:
		row.Detail = "GAIA tried to start the local model server and could not."
		row.Remedy = lemonadeAutoStartFailedRemedy(refused.Detail)
	}
	setRow(rep, row)
	return false
}

// probeInit runs GET /v1/<agent>/init and fills both rows from the answer. The
// second return is true only for the one failure GAIA can repair itself: a
// local model server that is not running.
func probeInit(ctx context.Context, t Transport, cfg Config, rep *Report) (State, bool) {
	l := Ladder{AgentID: cfg.AgentID}
	lemonade := Row{Key: KeyLemonade}
	model := Row{Key: KeyModel}

	// KeyLemonade is not agent-repairable — every StateFailed branch below is
	// Halt. The one StateUnknown branch (an unadvertised version) is the
	// BLOCKER-1 guard: Notify, because it fires on every launch against such a
	// server forever, and a blanket halt there is not shippable.
	resp, err := t.Do(ctx, http.MethodGet, "/v1/"+cfg.AgentID+"/init", nil)
	if err != nil {
		d := l.Error("check whether the local AI is ready", err)
		lemonade.State, lemonade.Line, lemonade.Detail, lemonade.Remedy, lemonade.Raw =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy(), err.Error()
		lemonade.Disposition = status.DispositionHalt
		setRow(rep, lemonade)
		return StateFailed, false
	}
	raw := string(resp.Body)
	lemonade.Raw, model.Raw = raw, raw

	// 200 = ready, 503 = not ready, SAME body either way. Anything else is the
	// relay or the sidecar failing, not an answer about readiness.
	if resp.Status != http.StatusOK && resp.Status != http.StatusServiceUnavailable {
		d := l.Status("check whether the local AI is ready", resp.Status, raw)
		lemonade.State, lemonade.Line, lemonade.Detail, lemonade.Remedy =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy()
		lemonade.Disposition = status.DispositionHalt
		setRow(rep, lemonade)
		return StateFailed, false
	}

	// Whatever happens below, the model row keeps the body it was probed with —
	// `d` on that row must show what the probe saw, not "(no raw answer)". It
	// stays Pending with the placeholder line so markPending can still tell the
	// user what it is waiting on.
	model.State, model.Line = StatePending, "—"
	setRow(rep, model)

	var body initBody
	if err := json.Unmarshal(resp.Body, &body); err != nil || body.Lemonade == nil || body.Model == nil {
		// Either unparseable, or parseable but not a readiness answer at all —
		// the relay's own 503/502 (`{"detail": ...}`) lands here. Diagnosing it
		// as "Lemonade is down" would name the wrong subject AND the wrong fix.
		d := l.Status("check whether the local AI is ready", resp.Status, raw)
		if err == nil {
			d.Cause = fmt.Sprintf(
				"The %s agent answered the readiness check without saying anything about "+
					"the local AI — it is most likely no longer running. %s",
				cfg.AgentName, d.Cause)
		}
		lemonade.State, lemonade.Line, lemonade.Detail, lemonade.Remedy =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy()
		lemonade.Disposition = status.DispositionHalt
		setRow(rep, lemonade)
		return StateFailed, false
	}

	// --- Local AI ---------------------------------------------------------
	switch {
	case !body.Lemonade.Reachable:
		d := l.Text("reach the local model server", firstNonEmpty(body.hint(), "not reachable"))
		lemonade.State = StateFailed
		lemonade.Disposition = status.DispositionHalt
		lemonade.Line = "not running at " + body.Lemonade.BaseURL
		lemonade.Detail = "GAIA needs a local model server. It runs on your machine; no message text ever leaves it."
		lemonade.Remedy = d.AsRemedy()
		// Nothing on this row is a one-KEY fix: the caller starts the server
		// automatically before the user ever sees it, and once that has failed
		// pressing f would only repeat it.
		lemonade.Fix = FixNone
		if !isLoopback(body.Lemonade.BaseURL) {
			// The agent is pointed at another machine, so a server started here
			// is one nothing would talk to. Not startable — by us or the daemon.
			lemonade.Detail = fmt.Sprintf(
				"The %s agent is configured to use a model server on another machine (%s), "+
					"so it has to be started there — starting one here would not be used.",
				cfg.AgentName, body.Lemonade.BaseURL)
			lemonade.Remedy = Remedy{
				Action: "Start the model server on that machine, or point LEMONADE_BASE_URL at a " +
					"reachable one, then press r to re-check.",
				Command: "gaia init",
				Where:   lemonadeDocs,
			}
			setRow(rep, lemonade)
			return StateFailed, false
		}
		// A LOCAL server that is simply down — the one failure GAIA repairs
		// itself. The row is filled in anyway: if the daemon cannot start it,
		// this manually-resolved remedy is what the user falls back to.
		setRow(rep, lemonade)
		return StateFailed, true

	case modelListUnreadable(body):
		// Reachable, but its model list could not be read: `present:false` here
		// means "we could not tell", not "it is missing". Reporting it on the
		// model row would send the user to download something they may already
		// have. The sidecar's own hint is the only thing that distinguishes the
		// two, and it orders this before the version check — so does this.
		lemonade.State = StateFailed
		lemonade.Disposition = status.DispositionHalt
		lemonade.Line = "running, but not answering properly"
		lemonade.Detail = body.hint()
		lemonade.Remedy = lemonadeRestartRemedy()
		setRow(rep, lemonade)
		return StateFailed, false

	case body.Lemonade.Compatible != nil && !*body.Lemonade.Compatible:
		lemonade.State = StateFailed
		lemonade.Disposition = status.DispositionHalt
		lemonade.Line = fmt.Sprintf("%s is older than %s",
			versionOr(body.Lemonade.Version, "the installed version"), body.Lemonade.MinVersion)
		lemonade.Detail = fmt.Sprintf(
			"The %s agent needs Lemonade %s or newer; upgrading keeps every other GAIA agent working too.",
			cfg.AgentName, body.Lemonade.MinVersion)
		lemonade.Remedy = Remedy{
			Action:  "Upgrade the local model server, then re-check.",
			Command: "gaia init",
			Where:   "https://lemonade-server.ai",
		}
		setRow(rep, lemonade)
		return StateFailed, false

	case body.Lemonade.Compatible == nil:
		// Reachable, but it did not say which version it is. Indeterminate is
		// not a pass: the agent's minimum cannot be verified either way.
		//
		// BLOCKER-1 guard: this fires on EVERY launch against such a server,
		// forever — nothing the user does changes it. A blanket halt here
		// would train them to press a key without reading, so this stays
		// Notify: named on screen, not blocking. See report.go's package doc.
		lemonade.State = StateUnknown
		lemonade.Disposition = status.DispositionNotify
		lemonade.Line = "running, version not advertised"
		lemonade.Detail = fmt.Sprintf(
			"Lemonade at %s answered but reported no version, so it cannot be verified as %s or newer.",
			body.Lemonade.BaseURL, body.Lemonade.MinVersion)
		lemonade.Remedy = Remedy{
			Action:  "Upgrade it if anything below behaves oddly; the checks continue either way.",
			Command: "gaia init",
			Where:   "https://lemonade-server.ai",
		}

	default:
		lemonade.State = StateOK
		lemonade.Line = "Lemonade " + versionOr(body.Lemonade.Version, "(version unreported)")
	}
	setRow(rep, lemonade)

	// --- AI model -----------------------------------------------------------
	// KeyModel is not agent-repairable either — a missing model blocks the
	// same way a down daemon does.
	if !body.Model.Present {
		model.State = StateFailed
		model.Disposition = status.DispositionHalt
		model.Line = body.Model.ID + " not downloaded"
		// The sidecar's hint here restates the line and the command; the raw body
		// is one `d` away, so the row gets the sentence that tells the user
		// something they do not already see.
		model.Detail = "About 4 GB. Once downloaded it is reused by every GAIA agent."
		model.Fix = FixPullModel
		model.Remedy = Remedy{
			Action:  "It downloads once, then every GAIA agent reuses it.",
			Command: "gaia init",
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}
		setRow(rep, model)
		return StateFailed, false
	}

	model.State = StateOK
	model.Line = body.Model.ID
	switch {
	case body.Model.CtxSize == nil || *body.Model.CtxSize <= 0:
		// Not loaded right now, so there is no window to judge. `/init` reports
		// only what the server says it loaded — never a config echo — so silence
		// here is "not known yet", not "fine".
		model.Line += " · downloaded, not loaded yet"
	case *body.Model.CtxSize < profileCtxTarget():
		markCtxShortfall(&model, *body.Model.CtxSize)
	default:
		model.Line += fmt.Sprintf(" · %s context", humanCtx(*body.Model.CtxSize))
	}
	setRow(rep, model)
	return model.State, false
}

// markCtxShortfall reports a model loaded into a smaller context window than this
// machine's profile pins.
//
// This is the mailbox bug one layer down: the row was green, the server was
// healthy, short turns worked — and a document-sized request came back
// `context_length_exceeded`. Measured on a 10.10.0 machine, the model loaded at
// 25037 with the server started bare and 32527 with the profile's 65536
// requested; a 40k-token request fails against both. So the number is real, it
// varies with free memory, and it is invisible unless the row says it.
//
// What it is NOT is a property of the machine. Enumerating every llama-server
// still alive on that same box and reading the --ctx-size each was launched
// with gave 5 × 65536, 3 × 32768, 1 × 36807 across twelve hours — the full
// profile window, reached five separate times. So a short load is a bad moment,
// not a ceiling, and the remedy must not tell the user their hardware cannot do
// it. That sentence was here and it was wrong.
//
// It is StateUnknown, deliberately, not StateFailed: the agent works perfectly
// for ordinary turns, and nothing here proves the launch broken the way a
// down daemon does. Unknown is the state this package already has for "not
// proven ready, not proven broken" — it renders [?] and keeps Ready() false.
//
// Its Disposition is Halt, though — this is the row the issue exists to fix.
// StateUnknown normally auto-proceeds after a hold (see Disposition ==
// Notify elsewhere in this file), but a document-sized request against a
// short-loaded model comes back context_length_exceeded, which the user
// feels. See internal/ui/status and RootModel's listener for what Halt does
// with this once it leaves the row.
func markCtxShortfall(model *Row, loaded int) {
	target := profileCtxTarget()
	model.State = StateUnknown
	model.Disposition = status.DispositionHalt
	model.Line = fmt.Sprintf("%s · %s of %s context",
		model.Line, humanCtx(loaded), humanCtx(target))
	model.Detail = fmt.Sprintf(
		"The model is loaded with room for about %d tokens, but this machine's profile "+
			"pins %d. Ordinary turns are unaffected; a long document or a large paste "+
			"comes back as a context-length error rather than an answer.",
		loaded, target)

	// The resolved RESTART remedy, and its wording is kept rather than replaced.
	// This row only exists once a model is loaded, which means a server is already
	// holding the port — so the stop is part of the instruction, and an earlier
	// version of this function threw that clause away by overwriting the Action.
	// The result was a command that could not run from the only state that
	// produces this row: lemond has no stop or restart verb, and a second instance
	// exits with "Port … is already in use".
	//
	// The appended caveat keeps the hedge — a restart may not fix it — without
	// claiming why. "This machine did not have the memory for more" was a
	// conclusion the data never supported, and it ends the interaction: there is
	// nothing a user does about hardware. Naming the load moment instead is both
	// true and leaves them somewhere to go.
	//
	// It deliberately does NOT name what to close. The only concrete suspects on
	// the measured machine were orphaned llama-servers totalling ~35 MB, which the
	// memory evidence does not support as the cause — and pointing a user at the
	// wrong processes is a worse remedy than a vague one. This package also has no
	// way to see them: it dials the daemon and nothing else (see Ladder.Error), and
	// the relayed /init reports the model, not the machine.
	r := lemonadeRestartRemedy()
	r.Action += " The window is chosen when the model loads, out of whatever memory is " +
		"free at that moment — so a short one is usually a busy moment rather than a " +
		"limit, and loading again on a quieter machine often gets more."
	model.Remedy = r
	// Nothing here is safe to do from the TUI: restarting the model server is a
	// host-level action, exactly as it is on the Local AI row.
	model.Fix = FixNone
}

// modelListUnreadable reports the one readiness failure the structured fields
// cannot express: Lemonade answered /health but its /models read failed, so
// `present:false` means "could not tell", not "missing". Only the hint carries
// it (api_routes._compute_init_status).
func modelListUnreadable(body initBody) bool {
	h := strings.ToLower(body.hint())
	return strings.Contains(h, "model list") && strings.Contains(h, "could not be read")
}

// isLoopback reports whether a base URL names this machine. A blank or
// unparseable URL is treated as local: that is what every default is, and
// guessing "remote" would withhold the launcher the user does need.
func isLoopback(baseURL string) bool {
	if strings.TrimSpace(baseURL) == "" {
		return true
	}
	u, err := url.Parse(baseURL)
	if err != nil || u.Host == "" {
		return true
	}
	host := u.Hostname()
	if host == "localhost" || host == "" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

func versionOr(v *string, fallback string) string {
	if v == nil || *v == "" {
		return fallback
	}
	return *v
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

func humanCtx(n int) string {
	if n >= 1024 && n%1024 == 0 {
		return fmt.Sprintf("%dK", n/1024)
	}
	return fmt.Sprintf("%d", n)
}

// ---------------------------------------------------------------------------
// 5. [email-specific] The mailbox: connected, granted send, AND usable.
// ---------------------------------------------------------------------------

type connectorEntry struct {
	Provider     string   `json:"provider"`
	Connected    bool     `json:"connected"`
	AccountEmail *string  `json:"account_email"`
	Scopes       []string `json:"scopes"`
	CanSend      bool     `json:"can_send"`
}

type connectorsBody struct {
	AgentID   string           `json:"agent_id"`
	Providers []connectorEntry `json:"providers"`
}

// emailAgentGrantID mirrors connector_routes.EMAIL_AGENT_ID.
const emailAgentGrantID = "installed:email"

// connectScopes is the EXACT scope list a reconnect must request, per provider:
// the connector's default_scopes ∪ the agent's FULL requested union (mail +
// calendar), mirroring connector_routes._build_scope_union post-#2730 D3.
// Guarded against drift from the Python side by
// tests/fixtures/connectors/email_scopes.json (see
// TestConnectScopesMatchesTheSharedFixture in check_test.go) — edit the
// fixture's connect_union alongside this map, never one without the other.
//
// The union is not decoration. `gaia connectors connect --scopes` REPLACES the
// provider defaults rather than adding to them (flow.py: `list(scopes) or
// list(provider.default_scopes)`), so a remedy naming only the mail scopes
// authorizes an account with less than it had before — the exact silent
// narrowing #2730 removes. Every connect path now requests the same wide
// union; only the daemon's forward-out mint narrows to what it enforces.
var connectScopes = map[string][]string{
	"google": {
		// catalog/google.py default_scopes
		"openid", "email", "profile",
		// gaia_agent_email.scopes.ALL_SCOPES (GMAIL_SCOPES + CALENDAR_SCOPES)
		"https://www.googleapis.com/auth/gmail.modify",
		"https://www.googleapis.com/auth/gmail.send",
		"https://www.googleapis.com/auth/calendar.events",
		"https://www.googleapis.com/auth/calendar.readonly",
	},
	"microsoft": {
		// catalog/microsoft.py default_scopes
		"openid", "offline_access", "https://graph.microsoft.com/User.Read",
		// gaia_agent_email.outlook_scopes.OUTLOOK_ALL_SCOPES (OUTLOOK_MAIL_SCOPES + OUTLOOK_CALENDAR_SCOPES)
		"https://graph.microsoft.com/Mail.ReadWrite",
		"https://graph.microsoft.com/Mail.Send",
		"https://graph.microsoft.com/Calendars.ReadWrite",
	},
	"microsoft_work": {
		// catalog/microsoft.py default_scopes (work tenant — same Graph app registration shape)
		"openid", "offline_access", "https://graph.microsoft.com/User.Read",
		// gaia_agent_email.outlook_scopes.OUTLOOK_ALL_SCOPES — identical to personal Outlook;
		// only the connector id (token/grant/keyring slot) differs.
		"https://graph.microsoft.com/Mail.ReadWrite",
		"https://graph.microsoft.com/Mail.Send",
		"https://graph.microsoft.com/Calendars.ReadWrite",
	},
}

// sendScopes is the per-provider scope `can_send` is really asking about. It
// lets the row say WHICH half of the authorization is missing when the payload
// carries enough to tell: a sign-in that never requested the scope needs a new
// sign-in, while a sign-in that has it but was not handed to the agent does not.
var sendScopes = map[string]string{
	"google":         "https://www.googleapis.com/auth/gmail.send",
	"microsoft":      "https://graph.microsoft.com/Mail.Send",
	"microsoft_work": "https://graph.microsoft.com/Mail.Send",
}

// connectCommand is the one command that fixes every mailbox state: it
// re-authorizes with the FULL requested union (mail + calendar) AND grants it
// to the agent in the same flow, so the token and the grant can never
// disagree, and never end up narrower than what the Agent UI would have
// granted for the same account (#2730 D3).
//
// Deliberately NOT `gaia connectors grants grant`: that cannot add a scope the
// stored token never carried, so on its own it would trade "cannot send" for
// "cannot read".
func connectCommand(provider string) string {
	scopes, ok := connectScopes[provider]
	if !ok {
		provider, scopes = "google", connectScopes["google"]
	}
	return fmt.Sprintf("gaia connectors connect %s --grant-agent %s --scopes %s",
		provider, emailAgentGrantID, strings.Join(scopes, " "))
}

// MailboxCheck is the email agent's extra precondition.
//
// It answers "is this mailbox USABLE", which is three questions, not one, and
// they fail independently:
//
//   - Is an account linked?              `connected`
//   - May the agent send from it?        `can_send`
//   - Do the stored credentials work?    only a real read can say
//
// The third is the one that used to be assumed. A stored connection keeps
// reporting `connected: true` after its refresh token is revoked, expired, or
// (under the daemon's forward-out custody model) never reaches the sidecar at
// all — so the row went green over a mailbox whose very first read 502s. See
// mailboxProbeBodyJSON and mailboxProbeTimeout for what that read costs and why
// it is bounded.
//
// It is an OPTIONAL check (Row.Optional), which is not the same as a soft one:
// the row still proves what it can and still fails loudly when the mailbox is
// unusable. What it must not do is refuse the launch, because the agent's
// capabilities do not all need a mailbox — POST /v1/<agent>/triage classifies
// the subject and body carried IN the request ("No mail is read or sent"), so a
// user with nothing connected must still be able to run a triage query. The
// mailbox operations — search, send, archive, quarantine, pre-scan, briefing —
// fail on their own, with the sidecar's own error, and this row is what warns
// them first.
func MailboxCheck() ExtraCheck {
	return ExtraCheck{
		Key:      KeyMailbox,
		Label:    "Mailbox",
		Optional: true,
		Run:      runMailboxCheck,
	}
}

// Every StateFailed branch KeyMailbox can produce below is Disposition
// Notify: Report.OfferableDespiteFailure treats the whole key as
// agent-repairable (agentRepairable[KeyMailbox]), so the screen already
// offers a single-keypress path past any mailbox failure into the
// conversation where the agent walks the user through fixing it. Halting in
// front of that offer would be a second prompt for one decision — see
// TestAMailboxWhoseCredentialsAreRejectedNeverGreenLightsALaunch.
func runMailboxCheck(ctx context.Context, t Transport, cfg Config) Row {
	l := Ladder{AgentID: cfg.AgentID}
	row := Row{Key: KeyMailbox, Label: "Mailbox"}

	resp, err := t.Do(ctx, http.MethodGet, "/v1/"+cfg.AgentID+"/connectors", nil)
	if err != nil {
		d := l.Error("check which mailboxes are connected", err)
		row.State, row.Line, row.Detail, row.Remedy, row.Raw =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy(), err.Error()
		row.Disposition = status.DispositionNotify
		return row
	}
	row.Raw = string(resp.Body)
	if resp.Status != http.StatusOK {
		d := l.Status("check which mailboxes are connected", resp.Status, string(resp.Body))
		row.State, row.Line, row.Detail, row.Remedy =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy()
		row.Disposition = status.DispositionNotify
		return row
	}

	var body connectorsBody
	if err := json.Unmarshal(resp.Body, &body); err != nil {
		row.State, row.Line = StateFailed, "unreadable answer"
		row.Disposition = status.DispositionNotify
		row.Detail = "The mailbox connector list could not be read."
		row.Remedy = Remedy{
			Action:  "Restart the agent's sidecar, then re-check.",
			Command: "gaia daemon start-agent " + cfg.AgentID,
			Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", cfg.AgentID),
		}
		return row
	}

	// Prefer a mailbox whose metadata says it should work; fall back to reporting
	// the connected-but-not-granted one, which is a distinct failure with a
	// distinct remedy.
	var sendable, connected *connectorEntry
	connectedCount := 0
	for i := range body.Providers {
		p := &body.Providers[i]
		if !p.Connected {
			continue
		}
		connectedCount++
		if p.CanSend && sendable == nil {
			sendable = p
		}
		if connected == nil {
			connected = p
		}
	}

	switch {
	case sendable != nil:
		// Metadata says linked + granted. That is exactly the state that used to
		// go green on a mailbox nothing had ever read, so PROVE it.
		return finishMailboxRow(row, probeMailbox(ctx, t, cfg, connectedCount), sendable, cfg)
	case connected != nil:
		return notGrantedRow(row, connected, cfg)
	}

	row.State = StateFailed
	row.Disposition = status.DispositionNotify
	row.Line = "not connected"
	row.Detail = fmt.Sprintf("%s cannot do anything until it can read a mailbox. Connecting takes about three minutes.", cfg.AgentName)
	row.Fix = FixConnectMailbox
	// No provider: nothing is connected, so the choice between Gmail and Outlook
	// is the user's and belongs to the connector flow. Naming one here would
	// route an Outlook user into a Google sign-in.
	row.Provider = ""
	row.Remedy = Remedy{
		Action:  "Connect Gmail, Outlook, or Microsoft 365 — press f to choose. For Outlook swap `google` for `microsoft` below, or `microsoft_work` for a work account.",
		Command: connectCommand("google"),
		Where:   "https://amd-gaia.ai/docs/guides/email",
	}
	return row
}

// notGrantedRow reports a linked account the agent may not send from.
//
// `can_send` is false for two different reasons that need two different
// sentences: the sign-in itself never requested the send scope, or it did and
// the agent was simply never handed it. The connector list carries the
// connection's own scopes, so where that list is populated the row says which
// one it is; where it is empty (the daemon's forward-out deployment keeps the
// connection in its own custody store) the row says it cannot tell rather than
// picking one. The command is the same either way — it re-runs the sign-in and
// the grant together, which is the only fix that covers both halves; see
// connectCommand for the one thing it can still narrow.
func notGrantedRow(row Row, p *connectorEntry, cfg Config) Row {
	who := accountOr(p.AccountEmail, providerName(p.Provider))
	row.State = StateFailed
	row.Disposition = status.DispositionNotify
	row.Fix = FixConnectMailbox
	row.Provider = p.Provider
	row.Remedy = Remedy{
		Action:  "Reconnect it — press f, or run the command. Takes about a minute.",
		Command: connectCommand(p.Provider),
		Where:   "https://amd-gaia.ai/docs/guides/email",
	}

	sendScope, known := sendScopes[p.Provider]
	switch {
	case len(p.Scopes) == 0:
		row.Line = fmt.Sprintf("%s · send not allowed", who)
		row.Detail = fmt.Sprintf(
			"The account is linked but %s may not send from it, so a send fails mid-task. "+
				"The connector list does not say whether the sign-in or the grant is the "+
				"missing half; the command below redoes both.", cfg.AgentName)
	case known && !containsString(p.Scopes, sendScope):
		row.Line = fmt.Sprintf("%s · sign-in has no send access", who)
		row.Detail = fmt.Sprintf(
			"The account was signed in without the send scope, so nothing short of signing "+
				"in again can add it — a grant cannot hand %s a permission the sign-in "+
				"never carried.", cfg.AgentName)
	default:
		row.Line = fmt.Sprintf("%s · send access not granted", who)
		row.Detail = fmt.Sprintf(
			"The sign-in itself does include send permission, but %s was never granted it, "+
				"so a send fails mid-task. The command below redoes the sign-in and the "+
				"grant together so the two cannot disagree.", cfg.AgentName)
	}
	return row
}

// --- the credential probe ---------------------------------------------------

// mailboxProbeBodyJSON is the cheapest read the email contract has: POST
// /v1/<agent>/search with no query and no labels lists the INBOX (api_routes
// ._search_inbox scopes an empty search to it), and max_results 1 bounds the
// per-message hydration to a single fetch.
//
// It is a read, never a write, and it resolves its mailbox exactly the way the
// agent's own first tool call does — so what it proves is what the user is about
// to ask for.
const mailboxProbeBodyJSON = `{"max_results":1}`

// mailboxProbeTimeout bounds the one live call the gate makes.
//
// The read is two provider round trips (a list and one hydrating fetch), ~100ms
// warm on a normal connection, against a screen the gate already holds for
// ~800ms — so a healthy mailbox is mostly paid for out of time the user was
// already spending. This bound is what stops a slow or wedged provider turning
// that into a wait people resent: past it the row reports "could not be
// verified", which does not block the launch.
const mailboxProbeTimeout = 5 * time.Second

// probeVerdict is what the credential probe established.
type probeVerdict int

const (
	// probeUsable — the sidecar read the mailbox.
	probeUsable probeVerdict = iota
	// probeRefused — the sidecar tried and the mailbox refused it. Proved broken.
	probeRefused
	// probeInconclusive — no answer about the mailbox at all. NOT a pass.
	probeInconclusive
)

// probeResult is the verdict plus the words for the row it produces.
type probeResult struct {
	verdict probeVerdict
	// cause is what happened, in the user's terms.
	cause string
	// remedy is what to do about an inconclusive probe; a refusal reconnects.
	remedy Remedy
	// trace is appended to Row.Raw so `d` shows the probe and what it cost.
	trace string
}

// probeMailbox issues the read and classifies the answer into exactly three
// outcomes. The classification rule is deliberately narrow: only an answer the
// SIDECAR gave about the MAILBOX may fail the row. Anything else — the relay not
// reaching the sidecar, a probe this build sent wrong, an ambiguity the contract
// cannot resolve — is inconclusive, because blaming the mailbox for it would
// hand the user a reconnect that fixes nothing.
func probeMailbox(ctx context.Context, t Transport, cfg Config, connectedCount int) probeResult {
	l := Ladder{AgentID: cfg.AgentID}
	path := "/v1/" + cfg.AgentID + "/search"

	// With two mailboxes linked the read route refuses to choose between them
	// (api_routes.get_search_backend 400s on 2+) and it takes no provider
	// argument, so the answer is knowable without asking: spending a round trip
	// on every launch to be told the same thing is the cost users resent.
	if connectedCount > 1 {
		return probeResult{
			verdict: probeInconclusive,
			cause: fmt.Sprintf(
				"%d mailboxes are linked, and the agent's read cannot be aimed at one of "+
					"them, so neither could be verified before launch.", connectedCount),
			remedy: Remedy{
				Action:  "Leave one mailbox linked if you want this verified before launch.",
				Command: "gaia connectors list",
				Where:   "https://amd-gaia.ai/docs/guides/email",
			},
			trace: fmt.Sprintf("mailbox probe: skipped — %d mailboxes linked and POST %s "+
				"takes no provider", connectedCount, path),
		}
	}

	ctx, cancel := context.WithTimeout(ctx, mailboxProbeTimeout)
	defer cancel()

	start := time.Now()
	resp, err := t.Do(ctx, http.MethodPost, path, []byte(mailboxProbeBodyJSON))
	took := time.Since(start)
	trace := func(outcome string) string {
		return fmt.Sprintf("mailbox probe: POST %s %s -> %s in %dms",
			path, mailboxProbeBodyJSON, outcome, took.Milliseconds())
	}

	if err != nil {
		d := l.Error("read the mailbox", err)
		return probeResult{
			verdict: probeInconclusive,
			cause:   d.Cause,
			remedy:  d.AsRemedy(),
			trace:   trace("transport error: " + err.Error()),
		}
	}

	detail := jsonDetail(resp.Body)
	outcome := fmt.Sprintf("HTTP %d %s", resp.Status, strings.TrimSpace(string(resp.Body)))

	switch {
	case resp.Status == http.StatusOK:
		return probeResult{verdict: probeUsable, trace: trace(outcome)}

	// The sidecar told us its forwarded token lapsed and the daemon has not
	// re-sent one YET (forwarded_credentials: "Retry in a moment"). Blocking a
	// launch on that would send the user through a browser sign-in for something
	// pressing r clears — so believe the "transient" it just told us.
	case transientCredentialGap(detail):
		return probeResult{
			verdict: probeInconclusive,
			cause: "The mailbox credential the background service hands the agent had just " +
				"lapsed and a fresh one had not arrived yet, so the mailbox could not be " +
				"verified. " + detailSuffix(firstSentence(detail)),
			remedy: Remedy{
				Action:  "Press r — this usually clears on its own within a minute.",
				Command: "gaia connectors list",
				Where:   "https://amd-gaia.ai/docs/guides/email",
			},
			trace: trace(outcome),
		}

	// 403 (auth missing/expired/revoked, or a scope the token does not carry)
	// and a sidecar-authored 502 (gaia.connectors refused to produce a usable
	// credential) are the mailbox itself saying no. So is a sidecar-authored
	// 503 — it can no longer resolve a mailbox at all, which contradicts the
	// metadata this row just read, and the metadata is the side that has been
	// wrong. The relay authors a 503 of its own, and that one is NOT the mailbox.
	case resp.Status == http.StatusForbidden,
		(resp.Status == http.StatusServiceUnavailable ||
			resp.Status == http.StatusBadGateway) && !relayGaveUp(detail):
		// Only the DIAGNOSIS half of the sidecar's detail. The rest of it is the
		// sidecar's own remedy, which carries a `<scopes>` placeholder — quoting
		// that verbatim would show the user a command they cannot copy, and
		// truncating it would show half a command, which is worse.
		return probeResult{
			verdict: probeRefused,
			cause:   firstNonEmpty(firstSentence(detail), "The mailbox refused the read."),
			trace:   trace(outcome),
		}

	// The sidecar is running (the row above proved it) but has no read route, so
	// it is older than this check. The Ladder's generic 404 answer — "the
	// background service does not know this agent, install it" — names the wrong
	// subject over an agent that is up.
	case resp.Status == http.StatusNotFound, resp.Status == http.StatusUnprocessableEntity:
		return probeResult{
			verdict: probeInconclusive,
			cause: fmt.Sprintf(
				"The running %s agent does not answer the read this check verifies a mailbox "+
					"with, so the mailbox could not be verified. %s",
				cfg.AgentName, detailSuffix(detail)),
			remedy: Remedy{
				Action:  "Update the agent if you want this verified before launch.",
				Command: "gaia hub install " + cfg.AgentID,
				Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", cfg.AgentID),
			},
			trace: trace(outcome),
		}
	}

	// Everything else — a relay 502, a 500 from a bug on the way to the mailbox —
	// says nothing about the credentials.
	d := l.Status("read the mailbox", resp.Status, string(resp.Body))
	return probeResult{
		verdict: probeInconclusive,
		cause:   d.Cause,
		remedy:  d.AsRemedy(),
		trace:   trace(outcome),
	}
}

// finishMailboxRow turns a probe verdict into the row.
func finishMailboxRow(row Row, res probeResult, p *connectorEntry, cfg Config) Row {
	row.Raw = strings.TrimSpace(row.Raw + "\n\n" + res.trace)
	who := accountOr(p.AccountEmail, "connected")

	switch res.verdict {
	case probeUsable:
		row.State = StateOK
		row.Line = fmt.Sprintf("%s (%s) · can read and send", who, providerName(p.Provider))
		return row

	case probeRefused:
		row.State = StateFailed
		row.Disposition = status.DispositionNotify
		row.Line = fmt.Sprintf("%s · sign-in no longer works",
			accountOr(p.AccountEmail, providerName(p.Provider)))
		row.Detail = fmt.Sprintf(
			"The account is linked and granted, but reading it just failed, so the very "+
				"first thing %s does would fail too. %s", cfg.AgentName, detailSuffix(res.cause))
		row.Fix = FixConnectMailbox
		row.Provider = p.Provider
		row.Remedy = Remedy{
			Action:  "Sign in again — press f, or run the command. Takes about a minute.",
			Command: connectCommand(p.Provider),
			Where:   "https://amd-gaia.ai/docs/guides/email",
		}
		return row
	}

	// Inconclusive: linked and granted, but unproven. Not a pass — it renders
	// [?], keeps Ready() false and is named on screen — and not a block either,
	// because nothing here says the mailbox is broken. This is also the
	// BLOCKER-1 guard for 2+ linked mailboxes (probeMailbox refuses to probe
	// when connectedCount > 1): that state is permanent for a supported,
	// shipped configuration, so it must stay Notify, never Halt.
	row.State = StateUnknown
	row.Disposition = status.DispositionNotify
	row.Line = fmt.Sprintf("%s (%s) · connected, not verified", who, providerName(p.Provider))
	row.Detail = res.cause
	row.Remedy = res.remedy
	if row.Remedy.Empty() {
		row.Remedy = Remedy{
			Action:  "Press r to try again; the checks continue either way.",
			Command: "gaia connectors list",
			Where:   "https://amd-gaia.ai/docs/guides/email",
		}
	}
	return row
}

// relayGaveUp reports whether the answer came from the daemon relay rather than
// from the sidecar. A dead relay hop is not a mailbox that refused anything, and
// answering it with a browser sign-in hides the one fix that works.
//
// The relay names itself in every body it authors, and this depends on that
// wording: its two 502s say "sidecar for agent '<id>'" (relay.py), its 503 for a
// dead sidecar says the agent "has no running sidecar to relay to", and its 503
// for a live one that stopped serving says the process "is alive but did not
// answer" its own health route (sidecars/manager.check_responsive). Nothing on
// the Python side pins those strings, so the fixtures in check_test.go carry
// them VERBATIM — a reword there breaks a test here rather than silently turning
// a wedged sidecar into a dead mailbox.
func relayGaveUp(detail string) bool {
	d := strings.ToLower(detail)
	return strings.Contains(d, "sidecar for agent") ||
		strings.Contains(d, "no running sidecar") ||
		strings.Contains(d, "is alive but did not answer")
}

// transientCredentialGap reports whether the sidecar said the forwarded token
// had merely lapsed between re-forwards, which is the one credential failure that
// clears itself (forwarded_credentials: "has not re-forwarded a fresh one yet").
func transientCredentialGap(detail string) bool {
	return strings.Contains(strings.ToLower(detail), "has not re-forwarded")
}

// jsonDetail pulls FastAPI's `{"detail": ...}` out of an error body so the row
// can quote the sidecar's own actionable sentence instead of raw JSON.
//
// A non-string `detail` is FastAPI's validation shape — an array of
// `{type, loc, msg, ...}` objects. Only the `msg` fields are language a user can
// read; the array itself must never reach a row, per the package's no-raw-status
// rule.
func jsonDetail(body []byte) string {
	var wrapper struct {
		Detail json.RawMessage `json:"detail"`
	}
	if err := json.Unmarshal(body, &wrapper); err != nil || len(wrapper.Detail) == 0 {
		return strings.TrimSpace(string(body))
	}
	var text string
	if err := json.Unmarshal(wrapper.Detail, &text); err == nil {
		return strings.TrimSpace(text)
	}
	var problems []struct {
		Msg string `json:"msg"`
	}
	if err := json.Unmarshal(wrapper.Detail, &problems); err == nil {
		msgs := make([]string, 0, len(problems))
		for _, p := range problems {
			if strings.TrimSpace(p.Msg) != "" {
				msgs = append(msgs, strings.TrimSpace(p.Msg))
			}
		}
		return strings.Join(msgs, "; ")
	}
	// Structured, and not a shape with words in it. The row's own prose stands.
	return ""
}

// firstSentence keeps the leading sentence of an upstream message, which is
// where these errors put what went wrong. A hard cap covers a message with no
// sentence break at all.
func firstSentence(s string) string {
	s = strings.TrimSpace(s)
	const limit = 200
	if i := strings.Index(s, ". "); i >= 0 && i < limit {
		return s[:i+1]
	}
	return clip(s, limit)
}

// clip truncates to at most limit RUNES. Byte-slicing splits the multibyte
// characters these messages are full of — `Settings → Connections`, em dashes —
// into mojibake that no amount of trimming repairs.
func clip(s string, limit int) string {
	r := []rune(s)
	if len(r) <= limit {
		return s
	}
	return strings.TrimSpace(string(r[:limit])) + "…"
}

func containsString(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

func providerName(provider string) string {
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

func accountOr(email *string, fallback string) string {
	if email == nil || *email == "" {
		return fallback
	}
	return *email
}
