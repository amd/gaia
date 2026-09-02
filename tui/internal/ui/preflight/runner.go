package preflight

import "context"

// Runner is what the readiness screen probes through, and what its `f` key acts
// through.
//
// Two implementations, ONE screen. daemonRunner answers for an agent the GAIA
// daemon supervises: every row is a call through the relay. localRunner answers
// for an agent the TUI spawns itself, where there is no daemon, no port and no
// token — so the same questions have completely different probes.
//
// Sharing the screen is the point. The renderer, the key vocabulary
// (d/r/f/enter/esc), and the Report/Row/State/Disposition semantics stay
// identical, so the two paths cannot drift into two dialects of the same
// screen. A second Model would guarantee they did.
type Runner interface {
	// Label names the runner in logs and diagnostics: "daemon" or "local".
	Label() string
	// Rows is the shape of the answer before any of it is known, so the screen
	// lays out once instead of growing rows as they resolve.
	Rows(cfg Config) []Row
	// Check probes every precondition in dependency order.
	Check(ctx context.Context, cfg Config) Report
	// Fix applies a row's one-key fix. A fix with progress to report streams it
	// through onLine (see streamsProgress); one without never calls it.
	Fix(ctx context.Context, cfg Config, kind FixKind, onLine func(string)) FixResult
}

// FixResult is what a one-key fix did. A failed fix carries a Diagnosis rather
// than a bare error, so the note under the rows names a cause and a remedy.
type FixResult struct {
	// Note is shown under the rows when the fix worked.
	Note string
	// Err is set when it did not.
	Err error
	// Diagnosis explains Err. Its zero value means the runner had nothing
	// structured to say, and the caller falls back to Final or Err.
	Diagnosis Diagnosis
	// Final is a streamed fix's last progress line — the one carrying ✓ or ✗.
	Final string
}

// OK reports whether the fix succeeded.
func (r FixResult) OK() bool { return r.Err == nil }

// streamsProgress reports whether kind's fix narrates while it runs, so the
// screen shows the streaming panel rather than a spinner and a note.
func streamsProgress(kind FixKind) bool {
	return kind == FixPullModel || kind == FixRunSetup
}

// --- the daemon runner ------------------------------------------------------

// daemonRunner is the original path: every precondition probed through the
// daemon relay (GET /v1/<agent>/init and friends).
type daemonRunner struct{ t Transport }

// NewDaemonRunner wraps a daemon transport as a Runner.
func NewDaemonRunner(t Transport) Runner { return daemonRunner{t: t} }

func (d daemonRunner) Label() string { return "daemon" }

func (d daemonRunner) Rows(cfg Config) []Row { return blankRows(cfg) }

func (d daemonRunner) Check(ctx context.Context, cfg Config) Report {
	return Check(ctx, d.t, cfg)
}

func (d daemonRunner) Fix(ctx context.Context, cfg Config, kind FixKind, onLine func(string)) FixResult {
	switch kind {
	case FixStartDaemon:
		if _, err := d.t.Start(ctx); err != nil {
			return FixResult{Err: err}
		}
		return FixResult{Note: "Background service started."}

	case FixStartSidecar:
		if err := d.t.EnsureAgent(ctx, cfg.AgentID); err != nil {
			return FixResult{Err: err}
		}
		return FixResult{Note: cfg.AgentName + " agent started."}

	case FixPullModel:
		res := Provision(ctx, d.t, cfg, onLine)
		if res.OK {
			return FixResult{Note: "Download complete.", Final: res.Final}
		}
		return FixResult{
			Err:       errFixFailed,
			Diagnosis: res.Diagnosis,
			Final:     res.Final,
		}
	}
	return FixResult{Err: errNoFix}
}
