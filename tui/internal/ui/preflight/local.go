package preflight

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// The local runner: readiness for an agent the TUI spawns itself.
//
// The flagship is a child process — TUI → agent → Lemonade — with no daemon, no
// HTTP port, no bearer token and no model-slot lease in the path. So none of
// the daemon runner's probes apply: there is no relay to ask, and asking would
// answer "not installed" for a launch that works. That is exactly why this
// launch used to have NO gate at all.
//
// Three rows, each probed directly on this machine for a local session, plus
// the Claude credential row when --use-claude is active:
//
//	GAIA agent  is the program on disk?      catalog.Find
//	Claude      is the Anthropic credential set?  process environment
//	Local AI    is the model server up?      one GET on loopback
//	AI model    are the models downloaded?   gaia init --check
//
// Every remedy is the daemon runner's, verbatim where one exists —
// lemonadeStartRemedy in particular is host-local, has no daemon dependency,
// and resolves the launcher against THIS machine rather than a GOOS table. Two
// screens that answer "Lemonade is down" with two different commands is the
// drift this reuse exists to prevent.

// lemonadeProbeTimeout bounds the one loopback GET that proves the model server
// is serving. A healthy server answers in milliseconds; past this it is not
// answering rather than answering slowly.
const lemonadeProbeTimeout = 3 * time.Second

// lemonadePorts are the ports a local Lemonade listens on, newest first.
// Mirrors client.DetectLemonadeURL — a probe that looked elsewhere would report
// a running server as down.
var lemonadePorts = []string{"13305", "8000"}

// lemonadeBaseURLEnv points the agent at a specific server, possibly on another
// machine. When it is set it is the ONLY thing probed: finding a local server
// on 13305 would prove nothing about the one the agent will actually use.
const lemonadeBaseURLEnv = "LEMONADE_BASE_URL"

// claudeAPIKeyEnv is the credential the agent's Claude provider reads before
// constructing its first client. The preflight only checks presence; it never
// prints the value or makes a remote request that could validate a secret.
const claudeAPIKeyEnv = "ANTHROPIC_API_KEY"

var (
	errFixFailed = errors.New("the fix did not succeed")
	errNoFix     = errors.New("this row has no fix that can be applied from here")
)

// LocalOptions describes the agent the local runner is checking.
type LocalOptions struct {
	// Binary is the executable to look for, e.g. "gaia-agent".
	Binary string
	// ClaudeMode mirrors --use-claude. It changes what "ready" means: a
	// Claude-backed session never calls the local chat LLM, so `gaia init` is
	// asked with --skip-chat-model and a down Lemonade must not refuse the
	// launch — see checkLemonade.
	ClaudeMode bool
	Model      string
}

// NewLocalRunner builds the runner for an agent the TUI spawns itself.
func NewLocalRunner(opts LocalOptions) Runner { return localRunner{opts: opts} }

type localRunner struct{ opts LocalOptions }

func (l localRunner) Label() string { return "local" }

func (l localRunner) Rows(cfg Config) []Row {
	rows := []Row{
		{Key: KeyBinary, Label: cfg.AgentName + " agent"},
	}
	if l.opts.ClaudeMode {
		rows = append(rows, Row{Key: KeyClaudeCredential, Label: "Claude credential"})
	}
	modelLabel := modelRowLabel
	if l.skipChatModel() {
		modelLabel = "Embeddings"
	}
	rows = append(rows,
		Row{Key: KeyLemonade, Label: lemonadeRowLabel, Step: installServerStep},
		Row{Key: KeyModel, Label: modelLabel, Step: l.downloadStep(nil)},
	)
	for i := range rows {
		rows[i].State = StatePending
		rows[i].Line = "—"
	}
	return rows
}

// Check walks the rows in dependency order and STOPS at the first
// failure, the same way the daemon walk does: "the models are not downloaded"
// is meaningless when the program that would use them is not on the machine.
func (l localRunner) Check(ctx context.Context, cfg Config) Report {
	cfg = cfg.withDefaults()
	rep := Report{AgentID: cfg.AgentID, AgentName: cfg.AgentName, Rows: l.Rows(cfg)}

	steps := []func(context.Context, Config) Row{l.checkBinary}
	if l.opts.ClaudeMode {
		steps = append(steps, l.checkClaudeCredential)
	}
	steps = append(steps, l.checkLemonade, func(ctx context.Context, cfg Config) Row {
		row, chat := l.verifyModels(ctx, cfg)
		rep.Chat = chat
		return row
	})
	for _, step := range steps {
		row := step(ctx, cfg)
		setRow(&rep, row)
		if row.State == StateFailed {
			markPending(&rep)
			return rep
		}
	}
	return rep
}

// --- 1.5. the Claude credential --------------------------------------------

// claudeCredentialConfigured mirrors the agent's credential sources that are
// available before the child starts: an inherited process variable or a .env
// file discoverable from the TUI's working directory. The subprocess inherits
// that same working directory, so accepting a .env here cannot turn a launch
// into a false pass that the child would reject.
func claudeCredentialConfigured() bool {
	if strings.TrimSpace(os.Getenv(claudeAPIKeyEnv)) != "" {
		return true
	}

	dir, err := os.Getwd()
	if err != nil {
		return false
	}
	for {
		if dotenvHasNonEmptyValue(filepath.Join(dir, ".env"), claudeAPIKeyEnv) {
			return true
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return false
		}
		dir = parent
	}
}

// dotenvHasNonEmptyValue reads only the named key. It is intentionally a
// small presence check rather than a general dotenv loader: preflight must not
// mutate the TUI environment or expose a credential in a report.
func dotenvHasNonEmptyValue(path, key string) bool {
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimSpace(strings.TrimPrefix(line, "export "))
		name, value, ok := strings.Cut(line, "=")
		if !ok || strings.TrimSpace(name) != key {
			continue
		}
		if strings.TrimSpace(dotenvValue(value)) != "" {
			return true
		}
	}
	return false
}

func dotenvValue(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
		if unquoted, err := strconv.Unquote(value); err == nil {
			return unquoted
		}
	}
	if len(value) >= 2 && value[0] == '\'' && value[len(value)-1] == '\'' {
		return value[1 : len(value)-1]
	}
	return value
}

func (l localRunner) checkClaudeCredential(_ context.Context, _ Config) Row {
	row := Row{Key: KeyClaudeCredential}
	if claudeCredentialConfigured() {
		row.State = StateOK
		row.Line = "set"
		return row
	}

	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Line = "not set"
	row.Detail = "Claude needs an Anthropic credential before the first message."
	row.Remedy = Remedy{
		Action: "Set " + claudeAPIKeyEnv + " (or put it in .env), then relaunch — this session cannot pick up a variable exported after it started.",
		Where:  "https://docs.anthropic.com/en/api/getting-started",
	}
	// Keep the raw answer diagnostic but never include the value of the secret.
	row.Raw = claudeAPIKeyEnv + " is not set"
	return row
}

// --- 1. the agent's own program --------------------------------------------

func (l localRunner) checkBinary(_ context.Context, cfg Config) Row {
	row := Row{Key: KeyBinary}
	found := catalog.Find(l.opts.Binary, cfg.AgentID)

	switch {
	case found.Found() && found.PresenceOnly:
		// Windows carries no exec bit, and this match had no PATHEXT extension
		// either, so all that was established is that a file is sitting there.
		// Notify, not Halt: the launch is about to prove it for real, and a
		// prompt the user cannot act on would fire on every launch forever.
		row.State = StateUnknown
		row.Disposition = status.DispositionNotify
		row.Line = "found at " + found.Path
		row.Detail = "Only that the file is there — Windows carries no way to check it " +
			"runs on this machine until it starts."
		row.Raw = found.Path
		return row

	case found.Found():
		row.State = StateOK
		row.Line = found.Path
		row.Raw = found.Path
		return row

	case found.Unverified != "":
		// A file IS there; nothing proves what it is. `gaia-agent` is both the
		// stdio child this wants and the frozen REST sidecar other installers
		// stage into the same directory (#3062), so running it is not safe.
		row.State = StateFailed
		row.Disposition = status.DispositionHalt
		row.Line = "install unfinished"
		row.Detail = fmt.Sprintf(
			"%s is there but the install left no %s behind, so nothing proves it is the "+
				"right program to run.", found.Unverified, catalog.SentinelName)
		row.Remedy = Remedy{
			Action:  "Install it again so the install can finish and verify itself.",
			Command: "gaia hub install " + cfg.AgentID,
			Where:   catalog.AgentDocsURL,
		}
		row.Raw = found.Unverified
		return row
	}

	// The common case for anyone who ran only the TUI binary.
	//
	// The PROGRAM is named, not whatever path the entry happened to carry: the
	// installer ships `gaia-agent`, and "it ships C:/some/where/gaia-agent" is
	// a claim about a path nobody has.
	program := filepath.Base(l.opts.Binary)
	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Line = "not on this machine"
	row.Detail = fmt.Sprintf(
		"%s is the program that does the thinking. Nothing runs without it.\nLooked in:  %s",
		program, strings.Join(found.Looked, ", "))
	row.Remedy = Remedy{
		// The URL rides in the action, not in Command: that field renders under
		// `run:` and means "type this", and a URL is not a command.
		Action: "Re-run the GAIA installer — it ships " + program +
			" alongside gaia-tui: " + catalog.InstallerURL,
		Where: catalog.AgentDocsURL,
	}
	// No one-key fix: a TUI quietly fetching a ~90 MB binary over a path
	// nothing verifies is worse than telling the user where to get it.
	row.Fix = FixNone
	row.Raw = strings.Join(found.Looked, "\n")
	return row
}

// --- 2. the local model server ---------------------------------------------

func (l localRunner) checkLemonade(ctx context.Context, _ Config) Row {
	row := Row{Key: KeyLemonade}

	base, reachable, probe := probeLemonade(ctx)
	row.Raw = probe

	if reachable {
		row.State = StateOK
		row.Line = "running at " + base
		return row
	}

	if l.opts.ClaudeMode {
		// --use-claude exists to avoid starting the local backend, so a down
		// Lemonade must not refuse this launch. It is not a pass either:
		// embeddings have no Anthropic equivalent, so document search, memory
		// and the code index still need it.
		row.State = StateUnknown
		row.Disposition = status.DispositionNotify
		row.Line = "not running — this session runs on Claude"
		row.Detail = "Chat works without it. Document search, memory and the code index " +
			"do not: embeddings are always computed locally."
		row.Remedy = lemonadeStartRemedy()
		return row
	}

	// Installed but stopped is the commonest way to land here, and it is the one
	// case this screen can resolve by itself. Starting is not installing: an
	// absent Lemonade still falls through to the `f` key below, because pulling
	// gigabytes needs a human to agree.
	if started, base, trace := tryAutoStartLemonade(ctx); started {
		row.State = StateOK
		row.Line = "started for you, running at " + base
		row.Raw = probe + "\n" + trace
		return row
	} else if trace != "" {
		// A failed attempt is reported, never swallowed: the row goes red as it
		// always did, and `d details` now shows what was run and how it failed.
		probe += "\n" + trace
		row.Raw = probe
	}

	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Line = "not running"
	row.Detail = "GAIA needs Lemonade to run local models or route chat to your chosen provider."
	row.Remedy = lemonadeStartRemedy()
	// Installing and starting Lemonade is `gaia init`'s job, so this row gets
	// the same one-key setup the model row does. It cannot run twice: Check
	// stops at the FIRST failure, so whenever this row is the blocker the model
	// row below it is pending and unfocusable.
	//
	// Two states are excluded because `gaia init` provably cannot fix them:
	//
	//   - a bad LEMONADE_SERVER_PATH: setup would inherit the same bad value,
	//     so the key would fail every time while the step that DOES fix it
	//     (unset the variable) sat below as the optional alternative;
	//   - a non-loopback LEMONADE_BASE_URL: `gaia init` auto-detects remote
	//     mode from it and then refuses to install or start anything. The
	//     daemon runner already special-cases this (check.go); matching it here
	//     is what keeps the two screens from diverging.
	l0 := resolveLemonade()
	if l0.BadOverride != "" || !isLoopback(base) {
		return row
	}
	row.Fix = FixRunSetup
	if !l0.Found && !lemonade.EmbeddedInstalled() && readEmbeddedLemonade() == nil {
		// Nothing installed is how every new machine starts — a step, not a fault.
		row.FirstRun = true
		row.Line = "not installed yet"
		row.Step = installServerStep
		row.Detail = "Lemonade runs AI models on this machine and routes chat to the provider " +
			"you pick. Setup installs it privately for GAIA — nothing system-wide."
		return row
	}
	// Installed, and GAIA could not start it: a real failure, with the start
	// command as the manual alternative to `f`.
	if !l0.Found {
		// GAIA's own server: its state file proves it is installed, which the
		// shared remedy — resolved from system installs — would call missing.
		row.Remedy = Remedy{
			Action:  "Press f and setup starts it. By hand instead: run this, then press r to re-check.",
			Command: "gaia lemonade embedded start",
			Where:   installDocs,
		}
		return row
	}
	row.Remedy.Action = "Press f and setup starts it. By hand instead: " + row.Remedy.Action
	return row
}

// installServerStep is the Lemonade row's first-run step.
const installServerStep = "install the local model server (~1 min)"

// probeLemonade asks the local model server for its model list, which is the
// smallest call that proves it is actually serving rather than merely bound.
// embeddedLemonade is what `gaia lemonade embedded` records about the private
// server it runs: a port chosen at start time, and a generated API key.
type embeddedLemonade = lemonade.EmbeddedState

// readEmbeddedLemonade loads that state file, or returns nil.
//
// Both fields matter and neither was used here. The port is picked when the
// server starts — 63207 on the machine this was found on — so probing the
// fixed 13305/8000 could never reach it; and the key means an unauthenticated
// probe gets 401 from a server that is healthy and serving. Together they made
// this screen report "Lemonade not running" for GAIA's own model server, then
// offer to install a second one.
func readEmbeddedLemonade() *embeddedLemonade {
	return lemonade.ReadEmbedded()
}

// lemonadeAPIKey resolves the credential a local Lemonade may demand.
//
// LEMONADE_API_KEY wins when set, so an explicitly configured credential is
// never overridden by whatever a local state file happens to hold.
func lemonadeAPIKey() string {
	return lemonade.APIKeyFor(lemonade.ResolveBaseURL(""))
}

// It returns the base URL it settled on, whether it answered, and a trace for
// the details pane.
// probeLemonade asks whether a local model server is answering, and where.
//
// A var so a test can decide that answer. Without it every row-level test here
// depends on whether the developer running `go test` happens to have Lemonade
// up — which silently skipped the entire auto-start path on any machine that
// did, testing nothing while reporting green.
var probeLemonade = probeLemonadeHTTP

func probeLemonadeHTTP(ctx context.Context) (base string, reachable bool, trace string) {
	ctx, cancel := context.WithTimeout(ctx, lemonadeProbeTimeout)
	defer cancel()

	var bases []string
	if override := strings.TrimSpace(os.Getenv(lemonadeBaseURLEnv)); override != "" {
		// The agent will use exactly this, so it is the only thing worth
		// probing — a local server on 13305 proves nothing about it.
		bases = []string{lemonade.ResolveBaseURL(override)}
	} else if readEmbeddedLemonade() != nil {
		// The agent resolves this recorded endpoint too. A different server
		// answering on a standard port cannot make that connection ready.
		bases = []string{lemonade.ResolveBaseURL("")}
	} else {
		for _, port := range lemonadePorts {
			bases = append(bases, "http://localhost:"+port+"/api/v1")
		}
	}

	// Authentication belongs to the endpoint being probed, not any redirect
	// target (even a different port on the same hostname).
	probeClient := *http.DefaultClient
	probeClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	var traces []string
	for _, b := range bases {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, b+"/models", nil)
		if err != nil {
			traces = append(traces, fmt.Sprintf("GET %s/models -> %v", b, err))
			continue
		}
		if key := lemonade.APIKeyFor(b); key != "" {
			req.Header.Set("Authorization", "Bearer "+key)
		}
		resp, err := probeClient.Do(req)
		if err != nil {
			traces = append(traces, fmt.Sprintf("GET %s/models -> %v", b, err))
			continue
		}
		resp.Body.Close()
		traces = append(traces, fmt.Sprintf("GET %s/models -> HTTP %d", b, resp.StatusCode))
		if resp.StatusCode == http.StatusUnauthorized {
			traces = append(traces, "  (401: a server IS listening but rejected the "+
				"credential — set LEMONADE_API_KEY, or check "+
				"~/.gaia/lemonade/state.json for the embedded server's key)")
		}
		if resp.StatusCode == http.StatusOK {
			return b, true, strings.Join(traces, "\n")
		}
	}
	return bases[len(bases)-1], false, strings.Join(traces, "\n")
}

// --- 3. the models -----------------------------------------------------------

func (l localRunner) skipChatModel() bool {
	return l.opts.ClaudeMode || lemonade.IsCloudID(l.opts.Model)
}

// localChatModel is the local chat model to load when the user picked one other
// than the profile default; empty otherwise.
func (l localRunner) localChatModel() string {
	if l.skipChatModel() {
		return ""
	}
	return l.opts.Model
}

// verifyModels answers the model row by LOADING the models, not listing them: an
// embedder llama-server cannot start is "downloaded" and still breaks document
// search and memory on the first turn. It also returns the chat line for the
// hand-off, empty when there is nothing proven to name.
func (l localRunner) verifyModels(ctx context.Context, _ Config) (Row, string) {
	row := Row{Key: KeyModel}

	st, err := gaiainit.Verify(ctx, l.skipChatModel(), l.localChatModel())
	switch {
	case errors.Is(err, gaiainit.ErrUnanswered):
		// The question was never answered — an installed gaia older than
		// `--check --load` exits 2 for "unrecognized arguments". Reading that as
		// "not set up" ran a full multi-minute `gaia init` on every single launch.
		// Unknown is what this package already has for exactly that.
		row.State = StateUnknown
		row.Disposition = status.DispositionNotify
		row.Line = "could not be checked"
		row.Detail = err.Error()
		row.Remedy = Remedy{
			Action:  "Run setup yourself if anything below behaves oddly.",
			Command: gaiainit.RunCommand(l.skipChatModel()),
			Where:   installDocs,
		}
		row.Raw = err.Error()
		return row, ""

	case err != nil:
		// Verify only ever wraps ErrUnanswered today; anything else reaching
		// here is a bug, and must not read as a clean machine.
		row.State = StateUnknown
		row.Disposition = status.DispositionHalt
		row.Line = "could not be checked"
		row.Detail = err.Error()
		row.Raw = err.Error()
		return row, ""

	case st.Ready:
		row.State = StateOK
		row.Raw = strings.Join(modelSummaries(st), "\n")
		switch {
		case l.opts.ClaudeMode:
			row.Line = "embedder loads — chat runs on Claude"
			return row, "Claude"
		case lemonade.IsCloudID(l.opts.Model):
			row.Line = "embedder loads — chat uses " + l.opts.Model
			return row, l.opts.Model + " (in the cloud)"
		}
		chat, ok := st.Chat()
		if !ok {
			row.Line = "loads"
			return row, ""
		}
		row.Line = chat.ID + sizeSuffix(chat.SizeGB) + " and the embedder load"
		return row, describeChat(chat)
	}

	switch st.Stage {
	case gaiainit.StageLoad:
		return l.loadFailedRow(st), ""
	case gaiainit.StageServer:
		// Installed and not answering is a fault, not a step still to do.
		row.State = StateFailed
		row.Disposition = status.DispositionHalt
		row.Line = "model server not answering"
		row.Detail = strings.Join(st.Reasons, "\n")
		row.Fix = FixRunSetup
		row.Remedy = Remedy{
			Action:  "Press f and setup starts it again, then checks the models.",
			Command: gaiainit.RunCommand(l.skipChatModel()),
			Where:   installDocs,
		}
		row.Raw = row.Detail
		return row, ""
	}

	// Not downloaded yet: the normal state of a first run.
	row.State = StateFailed
	row.FirstRun = true
	row.Disposition = status.DispositionHalt
	row.Line = "not downloaded yet"
	row.Step = l.downloadStep(st.Models)
	row.Detail = "Downloaded once, then reused by every GAIA session."
	if l.skipChatModel() {
		row.Line = "embedding model not downloaded yet"
		row.Detail = "Chat uses your selected provider. Document search and memory run on a " +
			"small local embedding model."
	}
	row.Fix = FixRunSetup
	row.Remedy = Remedy{
		Action:  "Setup downloads what is missing and starts the local server.",
		Command: gaiainit.RunCommand(l.skipChatModel()),
		Where:   installDocs,
	}
	row.Raw = strings.Join(st.Reasons, "\n")
	return row, ""
}

// loadFailedRow is a model that is downloaded and will not load — a real
// failure, which running setup again cannot fix.
func (l localRunner) loadFailedRow(st gaiainit.Status) Row {
	row := Row{Key: KeyModel, State: StateFailed, Disposition: status.DispositionHalt}
	var failed []gaiainit.Model
	chatFailed := false
	for _, m := range st.Models {
		if !m.Loaded {
			failed = append(failed, m)
			chatFailed = chatFailed || m.Role == "chat"
		}
	}
	if len(failed) == 0 {
		// A load-stage answer that names no failed model contradicts itself.
		failed = []gaiainit.Model{{ID: "a model", Error: strings.Join(st.Reasons, "; ")}}
		chatFailed = true
	}
	row.Line = failed[0].ID + " will not load"
	if chatFailed {
		row.Detail = "It is downloaded, but the model server could not start it, so GAIA " +
			"cannot answer."
	} else {
		// Chat still works, so the launch may go ahead — but never quietly.
		row.Optional = true
		row.Detail = "It is downloaded, but the model server could not start it, so document " +
			"search and memory will not work. Chat still does."
	}
	if readEmbeddedLemonade() != nil {
		row.Remedy = Remedy{
			Action: "Stop GAIA's model server, then press r — it starts again with current settings. " +
				"If it still fails, attach the output of `gaia diagnostics` to a bug report.",
			Command: "gaia lemonade embedded stop",
			Where:   "https://github.com/amd/gaia/issues",
		}
	} else {
		row.Remedy = lemonadeRestartRemedy()
	}
	var raw []string
	for _, m := range failed {
		raw = append(raw, m.ID+": "+m.Error)
	}
	row.Raw = strings.Join(raw, "\n")
	return row
}

// downloadStep is the model row's first-run step, sized from what Verify
// reported when the server could say, otherwise from the profile.
func (l localRunner) downloadStep(models []gaiainit.Model) string {
	var total float64
	known := len(models) > 0
	for _, m := range models {
		if m.SizeGB == nil {
			known = false
			break
		}
		total += *m.SizeGB
	}
	switch {
	case known:
		return fmt.Sprintf("download the models (%s)", formatGB(total))
	case l.skipChatModel():
		return "download the embedding model (under 1 GB)"
	}
	return "download the models (" + gaiainit.ProfileSize + ")"
}

func formatGB(gb float64) string {
	if gb < 1 {
		return fmt.Sprintf("%.0f MB", gb*1000)
	}
	return fmt.Sprintf("%.1f GB", gb)
}

// describeChat names a local chat model for the hand-off line.
func describeChat(m gaiainit.Model) string {
	if m.SizeGB == nil {
		return m.ID + " (on this machine)"
	}
	return fmt.Sprintf("%s (%s, on this machine)", m.ID, formatGB(*m.SizeGB))
}

func sizeSuffix(gb *float64) string {
	if gb == nil {
		return ""
	}
	return " (" + formatGB(*gb) + ")"
}

func modelSummaries(st gaiainit.Status) []string {
	var out []string
	for _, m := range st.Models {
		out = append(out, fmt.Sprintf("%s (%s)%s: loaded", m.ID, m.Role, sizeSuffix(m.SizeGB)))
	}
	return out
}

const installDocs = "https://amd-gaia.ai/docs/guides/install"

// --- fixes ------------------------------------------------------------------

// maxSetupLog bounds the setup transcript kept for `d details`.
const maxSetupLog = 200

func (l localRunner) Fix(ctx context.Context, _ Config, kind FixKind, onProgress func(Progress)) FixResult {
	if kind != FixRunSetup {
		return FixResult{Err: errNoFix}
	}

	ch, cancel, err := gaiainit.Start(l.skipChatModel())
	if err != nil {
		return FixResult{Err: err, Diagnosis: Diagnosis{
			Cause:   err.Error(),
			Remedy:  "Run setup in a terminal instead, then press r to re-check.",
			Command: gaiainit.RunCommand(l.skipChatModel()),
			Where:   installDocs,
		}}
	}
	defer cancel()

	var (
		log     []string
		current = Progress{Row: KeyLemonade, Text: "Starting setup", Percent: -1}
		failure string
	)
	stopped := func(cause string) string {
		if failure != "" {
			cause += " " + failure
		}
		return cause
	}
	for {
		select {
		case <-ctx.Done():
			// cancel() runs on the way out, so the child dies with the screen.
			return FixResult{Err: ctx.Err(), Final: current.Text, Log: log, Diagnosis: Diagnosis{
				Cause:   "Setup was cancelled, or ran past the time limit.",
				Remedy:  "Run it in a terminal instead, then press r to re-check.",
				Command: gaiainit.RunCommand(l.skipChatModel()),
				Where:   installDocs,
			}}
		case evt, ok := <-ch:
			if !ok {
				return FixResult{Err: errFixFailed, Final: current.Text, Log: log, Diagnosis: Diagnosis{
					Cause:   stopped("Setup ended without saying whether it worked."),
					Remedy:  "Press r to re-check whether the models landed, then retry.",
					Command: gaiainit.RunCommand(l.skipChatModel()),
					Where:   installDocs,
				}}
			}
			if !evt.Done {
				log = append(log, evt.Line)
				if len(log) > maxSetupLog {
					log = log[len(log)-maxSetupLog:]
				}
				if next, changed := advance(current, evt.Line, &failure); changed {
					current = next
					if onProgress != nil {
						onProgress(current)
					}
				}
				continue
			}
			if evt.Err != nil {
				return FixResult{Err: evt.Err, Final: current.Text, Log: log, Diagnosis: Diagnosis{
					Cause: stopped(fmt.Sprintf("Setup stopped while %s (%v).",
						lowerFirst(current.Text), evt.Err)),
					Remedy:  "Press d for the setup log, fix what it names, then retry.",
					Command: gaiainit.RunCommand(l.skipChatModel()),
					Where:   installDocs,
				}}
			}
			return FixResult{Note: "Setup complete.", Final: current.Text, Log: log}
		}
	}
}

// advance folds one line of `gaia init` output into the progress shown to the
// user. Lines that mean nothing to a person — Python log records above all —
// change nothing; they are kept for the details view only.
func advance(cur Progress, line string, failure *string) (Progress, bool) {
	p, ok := gaiainit.Describe(line)
	if !ok {
		return cur, false
	}
	if p.Failed {
		*failure = p.Text
		return cur, false
	}
	if p.Percent >= 0 {
		if p.Percent == cur.Percent {
			return cur, false
		}
		cur.Percent = p.Percent
		return cur, true
	}
	cur.Text, cur.Percent = p.Text, -1
	if p.Phase == gaiainit.PhaseServer {
		cur.Row = KeyLemonade
	} else if p.Phase != gaiainit.PhaseNone {
		cur.Row = KeyModel
	}
	return cur, true
}
