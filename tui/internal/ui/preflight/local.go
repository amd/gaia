package preflight

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiainit"
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
	rows = append(rows,
		Row{Key: KeyLemonade, Label: lemonadeRowLabel},
		Row{Key: KeyModel, Label: "AI model"},
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
	steps = append(steps, l.checkLemonade, l.checkModels)
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

func (l localRunner) checkClaudeCredential(_ context.Context, _ Config) Row {
	row := Row{Key: KeyClaudeCredential}
	if strings.TrimSpace(os.Getenv(claudeAPIKeyEnv)) != "" {
		row.State = StateOK
		row.Line = "set"
		return row
	}

	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Line = "not set"
	row.Detail = "Claude needs an Anthropic credential before the first message."
	row.Remedy = Remedy{
		Action: "Set " + claudeAPIKeyEnv + " in your shell, then press r to re-check.",
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

	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Line = "not running"
	row.Detail = "GAIA needs a local model server. It runs on your machine; no message " +
		"text ever leaves it."
	row.Remedy = lemonadeStartRemedy()
	// Starting Lemonade is `gaia init`'s job, and that is the AI model row's
	// fix — offering it twice would run the same multi-minute command from two
	// rows.
	row.Fix = FixNone
	return row
}

// probeLemonade asks the local model server for its model list, which is the
// smallest call that proves it is actually serving rather than merely bound.
// It returns the base URL it settled on, whether it answered, and a trace for
// the details pane.
func probeLemonade(ctx context.Context) (base string, reachable bool, trace string) {
	ctx, cancel := context.WithTimeout(ctx, lemonadeProbeTimeout)
	defer cancel()

	var bases []string
	if override := strings.TrimSpace(os.Getenv(lemonadeBaseURLEnv)); override != "" {
		// The agent will use exactly this, so it is the only thing worth
		// probing — a local server on 13305 proves nothing about it.
		bases = []string{strings.TrimRight(override, "/")}
	} else {
		for _, port := range lemonadePorts {
			bases = append(bases, "http://localhost:"+port+"/api/v1")
		}
	}

	var traces []string
	for _, b := range bases {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, b+"/models", nil)
		if err != nil {
			traces = append(traces, fmt.Sprintf("GET %s/models -> %v", b, err))
			continue
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			traces = append(traces, fmt.Sprintf("GET %s/models -> %v", b, err))
			continue
		}
		resp.Body.Close()
		traces = append(traces, fmt.Sprintf("GET %s/models -> HTTP %d", b, resp.StatusCode))
		if resp.StatusCode == http.StatusOK {
			return b, true, strings.Join(traces, "\n")
		}
	}
	return bases[len(bases)-1], false, strings.Join(traces, "\n")
}

// --- 3. the models -----------------------------------------------------------

func (l localRunner) checkModels(ctx context.Context, _ Config) Row {
	row := Row{Key: KeyModel}

	ready, err := gaiainit.Check(ctx, l.opts.ClaudeMode)
	switch {
	case errors.Is(err, gaiainit.ErrUnanswered):
		// The question was never answered — an installed gaia older than
		// `--check` exits 2 for "unrecognized arguments". Reading that as "not
		// set up" ran a full multi-minute `gaia init` on every single launch.
		// Unknown is what this package already has for exactly that.
		row.State = StateUnknown
		row.Disposition = status.DispositionNotify
		row.Line = "could not be checked"
		row.Detail = err.Error()
		row.Remedy = Remedy{
			Action:  "Run setup yourself if anything below behaves oddly.",
			Command: gaiainit.RunCommand(l.opts.ClaudeMode),
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}
		row.Raw = err.Error()
		return row

	case err != nil:
		// Check only ever wraps ErrUnanswered today; anything else reaching
		// here is a bug, and must not read as a clean machine.
		row.State = StateUnknown
		row.Disposition = status.DispositionHalt
		row.Line = "could not be checked"
		row.Detail = err.Error()
		row.Raw = err.Error()
		return row

	case ready:
		row.State = StateOK
		row.Line = "downloaded"
		if l.opts.ClaudeMode {
			row.Line = "embedder downloaded — chat runs on Claude"
		}
		return row
	}

	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Line = "not downloaded yet"
	row.Detail = "Several GB on a first run. Once downloaded they are reused by every " +
		"GAIA session."
	row.Fix = FixRunSetup
	row.Remedy = Remedy{
		Action:  "Download them — press f to run setup here, or run the command.",
		Command: gaiainit.RunCommand(l.opts.ClaudeMode),
		Where:   "https://amd-gaia.ai/docs/guides/install",
	}
	return row
}

// --- fixes ------------------------------------------------------------------

func (l localRunner) Fix(ctx context.Context, _ Config, kind FixKind, onLine func(string)) FixResult {
	if kind != FixRunSetup {
		return FixResult{Err: errNoFix}
	}

	ch, cancel, err := gaiainit.Start(l.opts.ClaudeMode)
	if err != nil {
		return FixResult{Err: err, Diagnosis: Diagnosis{
			Cause:   err.Error(),
			Remedy:  "Run setup in a terminal instead, then press r to re-check.",
			Command: gaiainit.RunCommand(l.opts.ClaudeMode),
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}}
	}
	defer cancel()

	var last string
	for {
		select {
		case <-ctx.Done():
			// cancel() runs on the way out, so the child dies with the screen.
			return FixResult{Err: ctx.Err(), Final: last, Diagnosis: Diagnosis{
				Cause:   "Setup was cancelled, or ran past the time limit.",
				Remedy:  "Run it in a terminal instead, then press r to re-check.",
				Command: gaiainit.RunCommand(l.opts.ClaudeMode),
				Where:   "https://amd-gaia.ai/docs/guides/install",
			}}
		case evt, ok := <-ch:
			if !ok {
				return FixResult{Err: errFixFailed, Final: last, Diagnosis: Diagnosis{
					Cause:   "Setup ended without saying whether it worked.",
					Remedy:  "Press r to re-check whether the models landed, then retry.",
					Command: gaiainit.RunCommand(l.opts.ClaudeMode),
					Where:   "https://amd-gaia.ai/docs/guides/install",
				}}
			}
			if !evt.Done {
				last = evt.Line
				if onLine != nil {
					onLine(evt.Line)
				}
				continue
			}
			if evt.Err != nil {
				return FixResult{Err: evt.Err, Final: last, Diagnosis: Diagnosis{
					Cause:   "Setup failed: " + evt.Err.Error(),
					Remedy:  "Run it in a terminal to see the full log, then press r.",
					Command: gaiainit.RunCommand(l.opts.ClaudeMode),
					Where:   "https://amd-gaia.ai/docs/guides/install",
				}}
			}
			return FixResult{Note: "Setup complete.", Final: last}
		}
	}
}
