package preflight

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/amd/gaia/tui/internal/daemon"
)

// Diagnosis is what a failed call means, what to do about it, and where to look
// next. It is the unit the readiness rows are built from, and it is deliberately
// usable outside this screen: the chat view needs the same mapping for a failure
// that happens mid-run, and re-deriving it there would let the two disagree.
type Diagnosis struct {
	// Cause names what failed, in the user's terms — never an HTTP status.
	Cause string
	// Remedy names what to do about it.
	Remedy string
	// Command is the exact command that fixes it, or "" when there is none.
	Command string
	// Where names the log, file, or page to read next.
	Where string
}

// AsRemedy converts a diagnosis into a row remedy.
func (d Diagnosis) AsRemedy() Remedy {
	return Remedy{Action: d.Remedy, Command: d.Command, Where: d.Where}
}

// String is the one-line form, for logs and for chat's mid-run error line.
func (d Diagnosis) String() string {
	parts := []string{d.Cause}
	if d.Remedy != "" {
		parts = append(parts, d.Remedy)
	}
	if d.Command != "" {
		parts = append(parts, "run: "+d.Command)
	}
	if d.Where != "" {
		parts = append(parts, "look: "+d.Where)
	}
	return strings.Join(parts, " — ")
}

// Ladder maps a failed call to a Diagnosis, most specific cause first.
//
// Ported from the email agent's playground `diagnose` (playground_html.py) and
// extended with the daemon's own typed errors, which the browser never sees. The
// ordering is the point: "Lemonade is not running" and "the model is missing"
// both surface as a 502 with a body, and answering "the call timed out" to
// either sends the user down the wrong path.
type Ladder struct {
	// AgentID names the sidecar in remedies like `gaia daemon start-agent <id>`.
	AgentID string
}

func (l Ladder) agent() string {
	if l.AgentID == "" {
		return "the agent"
	}
	return l.AgentID
}

// agentCommand builds an agent-scoped command, falling back to one that needs
// no id. A remedy a user cannot copy verbatim is not a remedy.
func (l Ladder) agentCommand(verb, fallback string) string {
	if l.AgentID == "" {
		return fallback
	}
	return verb + " " + l.AgentID
}

// daemonLog is the daemon's own log path, resolved for embedding in a remedy.
func daemonLog() string {
	p, err := daemon.LogPath()
	if err != nil {
		return "~/.gaia/host/daemon.log"
	}
	return p
}

func (l Ladder) sidecarLog() string {
	return fmt.Sprintf("~/.gaia/agents/%s/logs/", l.agent())
}

// Error diagnoses a transport or client error. op names the call in the user's
// terms, e.g. "reach the background service".
func (l Ladder) Error(op string, err error) Diagnosis {
	if err == nil {
		return Diagnosis{Cause: op + " failed for an unrecorded reason", Where: daemonLog()}
	}

	// 1. The daemon's own typed errors: they already know exactly what failed.
	var notRunning *daemon.NotRunningError
	if errors.As(err, &notRunning) {
		return Diagnosis{
			Cause:   "The GAIA background service is not running.",
			Remedy:  "Start it, then the checks continue automatically.",
			Command: "gaia daemon start",
			Where:   daemonLog(),
		}
	}
	var version *daemon.VersionError
	if errors.As(err, &version) {
		return Diagnosis{
			Cause: fmt.Sprintf("The running background service speaks host API v%s, "+
				"which this build cannot use.", version.Have),
			Remedy:  "Restart it so it comes up on the version this app expects.",
			Command: "gaia daemon restart",
			Where:   daemonLog(),
		}
	}
	var stale *daemon.StaleError
	if errors.As(err, &stale) {
		switch stale.Kind {
		case daemon.StaleUnresponsive:
			return Diagnosis{
				Cause:   "The background service is running but not answering.",
				Remedy:  "Reclaim it with a restart; it will not be killed from here.",
				Command: "gaia daemon restart",
				Where:   daemonLog(),
			}
		default:
			return Diagnosis{
				Cause:   "The recorded background service cannot be trusted (" + stale.Reason + ").",
				Remedy:  "Reclaim it with a restart.",
				Command: "gaia daemon restart",
				Where:   stale.Path,
			}
		}
	}
	var start *daemon.StartError
	if errors.As(err, &start) {
		return Diagnosis{
			Cause:   "The background service would not start (" + start.Reason + ").",
			Remedy:  "Run it in a terminal to see the failure directly.",
			Command: "gaia daemon start",
			Where:   daemonLog(),
		}
	}

	// 2. Context deadlines: the caller gave up, which is not the same as refused.
	if errors.Is(err, context.DeadlineExceeded) {
		return l.transport(op, "timed out")
	}
	if errors.Is(err, context.Canceled) {
		return Diagnosis{
			Cause:  op + " was cancelled.",
			Remedy: "Press r to run the checks again.",
		}
	}

	// 3. Everything else is a Go transport error, and the TUI dials exactly one
	// host: the daemon on loopback. So "connection refused" here means the
	// DAEMON is unreachable — never Lemonade, which this process never talks to
	// directly. Routing it through the body ladder would answer
	// "start lemonade-server" to a daemon that moved to a new port.
	var reqErr *daemon.RequestError
	if errors.As(err, &reqErr) {
		return l.transport(op, reqErr.Detail)
	}
	return l.transport(op, err.Error())
}

// transport diagnoses a failure to REACH the daemon, as opposed to something a
// service told us. Kept separate from Text on purpose: the same words mean
// different things depending on who produced them.
func (l Ladder) transport(op, text string) Diagnosis {
	body := strings.ToLower(text)

	switch {
	case containsAny(body, "refused", "connection reset", "no such host", "connect:", "eof"):
		return Diagnosis{
			Cause:   "The GAIA background service stopped answering — it may have restarted on a new port.",
			Remedy:  "Check it, then press r to re-check.",
			Command: "gaia daemon status",
			Where:   daemonLog(),
		}
	case containsAny(body, "timed out", "timeout", "deadline exceeded", "context canceled"):
		return Diagnosis{
			Cause:   op + " did not finish in time.",
			Remedy:  "Check the background service is healthy, then press r to re-check.",
			Command: "gaia daemon status",
			Where:   daemonLog(),
		}
	}
	cause := op + " failed."
	if strings.TrimSpace(text) != "" {
		cause = fmt.Sprintf("%s failed: %s", op, strings.TrimSpace(text))
	}
	return Diagnosis{
		Cause:   cause,
		Remedy:  "Check the background service, then press r to re-check.",
		Command: "gaia daemon status",
		Where:   daemonLog(),
	}
}

// Status diagnoses a response the daemon (or a relayed sidecar) actually
// answered with. body is the raw response body — the sidecar's `detail` or
// `hint` is the most actionable text available, so it is preferred over any
// generic status wording.
func (l Ladder) Status(op string, status int, body string) Diagnosis {
	trimmed := strings.TrimSpace(body)

	switch status {
	case http.StatusUnauthorized:
		return Diagnosis{
			Cause:   "The background service rejected this app's token.",
			Remedy:  "Restart it to mint a fresh one — the token rotates on every restart.",
			Command: "gaia daemon restart",
			Where:   daemonLog(),
		}
	case http.StatusNotFound:
		return Diagnosis{
			Cause: fmt.Sprintf("The background service does not know the agent %q.", l.agent()),
			Remedy: "Install it, then check it is registered. " +
				detailSuffix(trimmed),
			Command: l.agentCommand("gaia hub install", "gaia daemon agents"),
			Where:   daemonLog(),
		}
	case http.StatusServiceUnavailable:
		// The sidecar's own not-ready answer. Its hint is the best remedy there
		// is, so run the text ladder over it rather than inventing wording.
		if trimmed != "" {
			return l.Text(op, trimmed)
		}
		return Diagnosis{
			Cause:   op + " is not ready yet.",
			Remedy:  "Start the agent's sidecar, then re-check.",
			Command: l.agentCommand("gaia daemon start-agent", "gaia daemon agents"),
			Where:   l.sidecarLog(),
		}
	case http.StatusBadGateway:
		// A 502 whose body says the relay itself gave up mid-response is NOT a
		// dead sidecar — it is the daemon refusing to hold a long buffered call
		// (a model pull) open. Restarting the sidecar would fix nothing.
		if containsAny(strings.ToLower(trimmed), "mid-response", "readtimeout", "read timeout") {
			return l.Text(op, trimmed)
		}
		return Diagnosis{
			Cause:   fmt.Sprintf("The background service could not reach the %s agent. %s", l.agent(), detailSuffix(trimmed)),
			Remedy:  "Restart the agent's sidecar, then re-check.",
			Command: l.agentCommand("gaia daemon start-agent", "gaia daemon agents"),
			Where:   l.sidecarLog(),
		}
	}

	if status >= 500 {
		return Diagnosis{
			Cause:   fmt.Sprintf("%s failed inside the %s agent. %s", op, l.agent(), detailSuffix(trimmed)),
			Remedy:  "Read the agent's log, then restart its sidecar.",
			Command: l.agentCommand("gaia daemon start-agent", "gaia daemon agents"),
			Where:   l.sidecarLog(),
		}
	}
	if status >= 400 {
		return Diagnosis{
			Cause:   fmt.Sprintf("%s was refused by the %s agent. %s", op, l.agent(), detailSuffix(trimmed)),
			Remedy:  "Check the agent is installed and registered.",
			Command: "gaia daemon agents",
			Where:   l.sidecarLog(),
		}
	}
	return l.Text(op, trimmed)
}

// Text is the message ladder itself: specific causes before generic ones,
// mirroring the email playground's `diagnose` so the TUI and the browser never
// disagree about what a given failure means.
func (l Ladder) Text(op, text string) Diagnosis {
	body := strings.ToLower(text)

	switch {
	case containsAny(body, "not reachable", "refused", "connection error", "connect:", "no such host"):
		return Diagnosis{
			Cause:   "The local model server (Lemonade) is not running.",
			Remedy:  "Start it, then press r to re-check.",
			Command: "lemonade-server serve",
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}
	case containsAny(body, "older than the required", "min_version", "upgrade it"):
		return Diagnosis{
			Cause:   "The local model server is older than this agent requires.",
			Remedy:  "Upgrade it, then re-check.",
			Command: "gaia init",
			Where:   "https://lemonade-server.ai",
		}
	case containsAny(body, "dropped the connection mid-response", "did not answer"):
		return Diagnosis{
			Cause: "The background service gave up relaying the agent's answer. A large " +
				"model download exceeds what it will hold open.",
			Remedy:  "Download the model in a terminal instead, then press r to re-check.",
			Command: "gaia init",
			Where:   daemonLog(),
		}
	case containsAny(body, "not downloaded", "not present", "download"):
		return Diagnosis{
			Cause:   "The AI model is not downloaded.",
			Remedy:  "Download it once — every GAIA agent reuses the same model.",
			Command: "gaia init",
			Where:   "https://amd-gaia.ai/docs/guides/install",
		}
	case containsAny(body, "timed out", "timeout", "deadline exceeded"):
		return Diagnosis{
			Cause:   "The local model server did not respond in time.",
			Remedy:  "Check it is running on the port GAIA expects.",
			Command: "lemonade-server serve",
			Where:   daemonLog(),
		}
	}

	// Fallback: something answered, but with nothing this ladder recognises.
	// The answer came from the agent's sidecar, so restarting it is the honest
	// next step — never a remedy-less dead end.
	cause := op + " failed."
	if strings.TrimSpace(text) != "" {
		cause = fmt.Sprintf("%s failed: %s", op, strings.TrimSpace(text))
	}
	return Diagnosis{
		Cause:   cause,
		Remedy:  "Restart the agent's sidecar, then press r to re-check.",
		Command: l.agentCommand("gaia daemon start-agent", "gaia daemon agents"),
		Where:   l.sidecarLog(),
	}
}

func containsAny(haystack string, needles ...string) bool {
	for _, n := range needles {
		if strings.Contains(haystack, n) {
			return true
		}
	}
	return false
}

// detailSuffix quotes an upstream detail without letting an empty body produce
// a dangling sentence.
func detailSuffix(body string) string {
	body = strings.TrimSpace(body)
	if body == "" {
		return ""
	}
	const limit = 240
	if len(body) > limit {
		body = body[:limit] + "…"
	}
	return "It said: " + body
}
