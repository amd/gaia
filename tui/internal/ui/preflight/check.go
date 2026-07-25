package preflight

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/amd/gaia/tui/internal/daemon"
)

// ExtraCheck is a per-agent precondition appended after the four generic ones.
//
// The first four rows are the same for any sidecar agent that implements
// GET /v1/<agent>/init. Anything agent-specific — for email, a mailbox that is
// both connected AND granted send — arrives here, so the screen stays generic.
type ExtraCheck struct {
	Key   string
	Label string
	Run   func(ctx context.Context, t Transport, cfg Config) Row
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
		c.AgentName = strings.ToUpper(c.AgentID[:1]) + c.AgentID[1:]
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
		row.Key, row.Label = extra.Key, extra.Label
		setRow(&rep, row)
		if row.State == StateFailed {
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
		{Key: KeyLemonade, Label: "Local AI"},
		{Key: KeyModel, Label: "AI model"},
	}
	for _, extra := range cfg.Extras {
		rows = append(rows, Row{Key: extra.Key, Label: extra.Label})
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

	resp, err := t.Do(ctx, http.MethodGet, daemon.APIPrefix+"/agents", nil)
	if err != nil {
		d := l.Error("list the agents the background service supervises", err)
		row.State, row.Line, row.Detail, row.Remedy, row.Raw =
			StateFailed, "cannot be listed", d.Cause, d.AsRemedy(), err.Error()
		return row.commit(rep)
	}
	row.Raw = string(resp.Body)
	if resp.Status != http.StatusOK {
		d := l.Status("list the supervised agents", resp.Status, string(resp.Body))
		row.State, row.Line, row.Detail, row.Remedy =
			StateFailed, "cannot be listed", d.Cause, d.AsRemedy()
		return row.commit(rep)
	}

	var body agentsBody
	if err := json.Unmarshal(resp.Body, &body); err != nil {
		row.State, row.Line = StateFailed, "unreadable answer"
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
			row.State = StateOK
			row.Line = describeSidecar(a.AgentVersion, a.PID)
			return row.commit(rep)
		}
		row.State = StateFailed
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

// ---------------------------------------------------------------------------
// 3+4. GET /v1/<agent>/init — one probe, two rows.
// ---------------------------------------------------------------------------

type initBody struct {
	Ready    bool `json:"ready"`
	Lemonade struct {
		Reachable  bool    `json:"reachable"`
		BaseURL    string  `json:"base_url"`
		Version    *string `json:"version"`
		MinVersion string  `json:"min_version"`
		// Compatible is null when the server does not advertise a version. That
		// is an indeterminate check, NOT a pass.
		Compatible *bool `json:"compatible"`
	} `json:"lemonade"`
	Model struct {
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

func checkInit(ctx context.Context, t Transport, cfg Config, rep *Report) State {
	l := Ladder{AgentID: cfg.AgentID}
	lemonade := Row{Key: KeyLemonade}
	model := Row{Key: KeyModel}

	resp, err := t.Do(ctx, http.MethodGet, "/v1/"+cfg.AgentID+"/init", nil)
	if err != nil {
		d := l.Error("check whether the local AI is ready", err)
		lemonade.State, lemonade.Line, lemonade.Detail, lemonade.Remedy, lemonade.Raw =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy(), err.Error()
		setRow(rep, lemonade)
		return StateFailed
	}
	raw := string(resp.Body)
	lemonade.Raw, model.Raw = raw, raw

	// 200 = ready, 503 = not ready, SAME body either way. Anything else is the
	// relay or the sidecar failing, not an answer about readiness.
	if resp.Status != http.StatusOK && resp.Status != http.StatusServiceUnavailable {
		d := l.Status("check whether the local AI is ready", resp.Status, raw)
		lemonade.State, lemonade.Line, lemonade.Detail, lemonade.Remedy =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy()
		setRow(rep, lemonade)
		return StateFailed
	}

	var body initBody
	if err := json.Unmarshal(resp.Body, &body); err != nil {
		lemonade.State, lemonade.Line = StateFailed, "unreadable answer"
		lemonade.Detail = fmt.Sprintf("The %s agent's readiness answer could not be read.", cfg.AgentName)
		lemonade.Remedy = Remedy{
			Action:  "Restart the agent's sidecar, then re-check.",
			Command: "gaia daemon start-agent " + cfg.AgentID,
			Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", cfg.AgentID),
		}
		setRow(rep, lemonade)
		return StateFailed
	}

	// --- Local AI ---------------------------------------------------------
	switch {
	case !body.Lemonade.Reachable:
		d := l.Text("reach the local model server", firstNonEmpty(body.hint(), "not reachable"))
		lemonade.State = StateFailed
		lemonade.Line = "not running at " + body.Lemonade.BaseURL
		lemonade.Detail = "GAIA needs a local model server. It runs on your machine; no message text ever leaves it."
		lemonade.Remedy = d.AsRemedy()
		// The sidecar cannot install or launch Lemonade — that is a host
		// prerequisite — so there is no honest one-key fix here.
		lemonade.Fix = FixNone
		setRow(rep, lemonade)
		return StateFailed

	case modelListUnreadable(body):
		// Reachable, but its model list could not be read: `present:false` here
		// means "we could not tell", not "it is missing". Reporting it on the
		// model row would send the user to download something they may already
		// have. The sidecar's own hint is the only thing that distinguishes the
		// two, and it orders this before the version check — so does this.
		lemonade.State = StateFailed
		lemonade.Line = "running, but not answering properly"
		lemonade.Detail = body.hint()
		lemonade.Remedy = Remedy{
			Action:  "Restart the local model server, then re-check.",
			Command: "lemonade-server serve",
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}
		setRow(rep, lemonade)
		return StateFailed

	case body.Lemonade.Compatible != nil && !*body.Lemonade.Compatible:
		lemonade.State = StateFailed
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
		return StateFailed

	case body.Lemonade.Compatible == nil:
		// Reachable, but it did not say which version it is. Indeterminate is
		// not a pass: the agent's minimum cannot be verified either way.
		lemonade.State = StateUnknown
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

	// --- AI model ---------------------------------------------------------
	if !body.Model.Present {
		model.State = StateFailed
		model.Line = body.Model.ID + " not downloaded"
		// The sidecar's hint here restates the line and the command; the raw body
		// is one `d` away, so the row gets the sentence that tells the user
		// something they do not already see.
		model.Detail = "About 4 GB. Once downloaded it is reused by every GAIA agent."
		model.Fix = FixPullModel
		model.Remedy = Remedy{
			Action:  "Download it — press f to pull it here, or run the command.",
			Command: "gaia init",
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}
		setRow(rep, model)
		return StateFailed
	}

	model.State = StateOK
	model.Line = body.Model.ID
	if body.Model.CtxSize != nil && *body.Model.CtxSize > 0 {
		model.Line += fmt.Sprintf(" · %s context", humanCtx(*body.Model.CtxSize))
	} else {
		model.Line += " · downloaded, not loaded yet"
	}
	setRow(rep, model)
	return model.State
}

// modelListUnreadable reports the one readiness failure the structured fields
// cannot express: Lemonade answered /health but its /models read failed, so
// `present:false` means "could not tell", not "missing". Only the hint carries
// it (api_routes._compute_init_status).
func modelListUnreadable(body initBody) bool {
	h := strings.ToLower(body.hint())
	return strings.Contains(h, "model list") && strings.Contains(h, "could not be read")
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
// 5. [email-specific] The mailbox: connected AND granted send.
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

// Send scopes, mirroring gaia_agent_email.scopes / .outlook_scopes. Quoted in a
// remedy, so a wrong value would send the user down a dead end.
const (
	gmailSendScope   = "https://www.googleapis.com/auth/gmail.send"
	outlookSendScope = "https://graph.microsoft.com/Mail.Send"
	// emailAgentGrantID mirrors connector_routes.EMAIL_AGENT_ID.
	emailAgentGrantID = "installed:email"
)

// MailboxCheck is the email agent's extra precondition.
//
// `connected` and `can_send` are separate answers and the difference matters:
// an account can be linked while the agent has no send grant, and today that
// only surfaces as a 403 in the middle of a task the user already approved.
func MailboxCheck() ExtraCheck {
	return ExtraCheck{
		Key:   KeyMailbox,
		Label: "Mailbox",
		Run:   runMailboxCheck,
	}
}

func runMailboxCheck(ctx context.Context, t Transport, cfg Config) Row {
	l := Ladder{AgentID: cfg.AgentID}
	row := Row{Key: KeyMailbox, Label: "Mailbox"}

	resp, err := t.Do(ctx, http.MethodGet, "/v1/"+cfg.AgentID+"/connectors", nil)
	if err != nil {
		d := l.Error("check which mailboxes are connected", err)
		row.State, row.Line, row.Detail, row.Remedy, row.Raw =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy(), err.Error()
		return row
	}
	row.Raw = string(resp.Body)
	if resp.Status != http.StatusOK {
		d := l.Status("check which mailboxes are connected", resp.Status, string(resp.Body))
		row.State, row.Line, row.Detail, row.Remedy =
			StateFailed, "cannot be checked", d.Cause, d.AsRemedy()
		return row
	}

	var body connectorsBody
	if err := json.Unmarshal(resp.Body, &body); err != nil {
		row.State, row.Line = StateFailed, "unreadable answer"
		row.Detail = "The mailbox connector list could not be read."
		row.Remedy = Remedy{
			Action:  "Restart the agent's sidecar, then re-check.",
			Command: "gaia daemon start-agent " + cfg.AgentID,
			Where:   fmt.Sprintf("~/.gaia/agents/%s/logs/", cfg.AgentID),
		}
		return row
	}

	// Prefer a fully usable mailbox; fall back to reporting the connected-but-
	// not-granted one, which is a distinct failure with a distinct remedy.
	var connected *connectorEntry
	for i := range body.Providers {
		p := &body.Providers[i]
		if !p.Connected {
			continue
		}
		if p.CanSend {
			row.State = StateOK
			row.Line = fmt.Sprintf("%s (%s) · can send",
				accountOr(p.AccountEmail, "connected"), providerName(p.Provider))
			return row
		}
		if connected == nil {
			connected = p
		}
	}

	if connected != nil {
		row.State = StateFailed
		row.Line = fmt.Sprintf("%s connected, send not allowed", accountOr(connected.AccountEmail, providerName(connected.Provider)))
		row.Detail = fmt.Sprintf(
			"The account is linked but %s was never granted permission to send, so a send fails mid-task.",
			cfg.AgentName)
		row.Fix = FixConnectMailbox
		row.Provider = connected.Provider
		row.Remedy = Remedy{
			Action: "Grant the send scope — press f to reconnect, or run the command.",
			Command: fmt.Sprintf("gaia connectors grants grant %s %s --scopes %s",
				connected.Provider, emailAgentGrantID, sendScope(connected.Provider)),
			Where: "https://amd-gaia.ai/docs/guides/email",
		}
		return row
	}

	row.State = StateFailed
	row.Line = "not connected"
	row.Detail = fmt.Sprintf("%s cannot do anything until it can read a mailbox. Connecting takes about three minutes.", cfg.AgentName)
	row.Fix = FixConnectMailbox
	row.Provider = "google"
	row.Remedy = Remedy{
		Action: "Connect Gmail or Outlook — press f to start, or run the command.",
		Command: fmt.Sprintf("gaia connectors connect google --grant-agent %s --scopes %s",
			emailAgentGrantID, gmailSendScope),
		Where: "https://amd-gaia.ai/docs/guides/email",
	}
	return row
}

func sendScope(provider string) string {
	if provider == "microsoft" {
		return outlookSendScope
	}
	return gmailSendScope
}

func providerName(provider string) string {
	switch provider {
	case "google":
		return "Gmail"
	case "microsoft":
		return "Outlook"
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
