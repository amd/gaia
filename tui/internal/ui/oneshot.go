package ui

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// OneShotResult is the outcome of one non-interactive turn. It mirrors
// gaia.daemon.agent_query.QueryOutcome so the Go and Python thin clients report
// the same thing.
type OneShotResult struct {
	// ExitCode is 0 on a terminal `final`, 1 on a terminal `error` or a stream
	// that ended without either.
	ExitCode int
	// TerminalType is "final", "error", or "" when no terminal event arrived.
	TerminalType string
	Answer       string
	ErrorDetail  string
}

// RunOneShot drives a single turn and renders it to plain streams — no alt
// screen, no TTY required.
//
// The answer goes to out and progress to errW, so `... --query X > answer.txt`
// captures exactly the answer and nothing else. This is the same split the
// `gaia <agent>` CLI uses, and it is what makes the transport testable from a
// script and from CI.
func RunOneShot(
	ctx context.Context,
	c client.AgentClient,
	query string,
	out, errW io.Writer,
) OneShotResult {
	started := time.Now()

	ch, err := c.Send(ctx, query)
	if err != nil {
		// Our own deadline is not a broken dependency: the transport error sent
		// the user to the daemon log over a --timeout they chose.
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			detail := abandonedDetail(ctx.Err(), query, time.Since(started))
			fmt.Fprintf(errW, "❌ %s\n", detail)
			return OneShotResult{ExitCode: 1, TerminalType: event.CanonicalTypeError, ErrorDetail: detail}
		}
		fmt.Fprintf(errW, "❌ %v\n", err)
		return OneShotResult{ExitCode: 1, ErrorDetail: err.Error()}
	}

	var (
		res       OneShotResult
		streamed  strings.Builder
		sawTokens bool
	)

	handle := func(evt interface{}) {
		switch e := evt.(type) {
		case event.CanonicalStatusEvent:
			if msg := strings.TrimSpace(e.Message); msg != "" {
				fmt.Fprintf(errW, "  … %s\n", msg)
			}

		case event.CanonicalTokenEvent:
			if e.Delta == "" {
				return
			}
			sawTokens = true
			streamed.WriteString(e.Delta)
			fmt.Fprint(out, e.Delta)

		case event.CanonicalToolCallEvent:
			fmt.Fprintf(errW, "  🔧 %s\n", e.Tool)

		case event.CanonicalToolResultEvent:
			// Not a tick: the canonical tool_result event carries no success
			// flag, so a failed tool looks exactly like one that worked.
			if e.Render != "" {
				fmt.Fprintf(errW, "  ← %s returned (%s)\n", e.Tool, e.Render)
			} else {
				fmt.Fprintf(errW, "  ← %s returned\n", e.Tool)
			}

		case event.CanonicalNeedsConfirmationEvent:
			line := "  ⚠️  confirmation needed: " + e.Action
			if summary := strings.TrimSpace(e.Summary); summary != "" {
				line += " — " + summary
			}
			fmt.Fprintln(errW, line)

		case event.CanonicalFinalEvent:
			res.TerminalType = event.CanonicalTypeFinal
			// `answer` is authoritative; the streamed tokens are the fallback for
			// a sidecar that streams and then closes with an empty final.
			res.Answer = e.Answer
			if res.Answer == "" {
				res.Answer = streamed.String()
			}
			if sawTokens {
				// The tokens already printed the answer live — just end the line.
				fmt.Fprintln(out)
			} else if res.Answer != "" {
				fmt.Fprintln(out, res.Answer)
			}
			if usage := event.CanonicalUsageOf(e); usage.Steps > 0 || usage.ToolsUsed > 0 {
				fmt.Fprintf(errW, "  ℹ️  %d steps, %d tools\n", usage.Steps, usage.ToolsUsed)
			}

		case event.CanonicalErrorEvent:
			res.TerminalType = event.CanonicalTypeError
			res.ErrorDetail = e.Detail
			if sawTokens {
				fmt.Fprintln(out)
			}
			fmt.Fprintf(errW, "❌ %s\n", e.Detail)

		case event.CanonicalUnsupportedEvent:
			// Contract §7: visible, never dropped.
			fmt.Fprintf(errW, "  [unsupported event %q]\n", e.EventType)

		case event.CanonicalMalformedEvent:
			fmt.Fprintf(errW, "  [unreadable event skipped: %s]\n", e.Reason)

		// The legacy in-process vocabulary, so this renderer also works when
		// pointed at a subprocess agent.
		case event.AnswerEvent:
			res.TerminalType = event.CanonicalTypeFinal
			res.Answer = e.Content
			fmt.Fprintln(out, e.Content)
		case event.AgentErrorEvent:
			res.TerminalType = event.CanonicalTypeError
			res.ErrorDetail = e.Content
			fmt.Fprintf(errW, "❌ %s\n", e.Content)
		case event.ToolStartEvent:
			fmt.Fprintf(errW, "  🔧 %s\n", e.Tool)
		case event.StatusEvent:
			if msg := strings.TrimSpace(e.Message); msg != "" {
				fmt.Fprintf(errW, "  … %s\n", msg)
			}
		case event.ChunkEvent:
			sawTokens = true
			streamed.WriteString(e.Content)
			fmt.Fprint(out, e.Content)

		default:
			fmt.Fprintf(errW, "  [unhandled event %T]\n", evt)
		}
	}

	for {
		select {
		case evt, open := <-ch:
			if !open {
				// The stream closed; the terminal-event check decides.
				return finishOneShot(res, query, errW)
			}
			handle(evt)

		case <-ctx.Done():
			// The caller's deadline is the only bound on a dependency that
			// accepts the query and then goes quiet — the relay's read watchdog
			// is reset by every heartbeat, so a wedged sidecar can keep the
			// stream alive indefinitely without ever answering.
			//
			// A plain select picks UNIFORMLY when both cases are ready, so an
			// answer that landed in the same instant as the deadline would be
			// thrown away half the time. Everything already delivered is still
			// the agent's answer: take that first, then give up. The drain is
			// bounded by what is buffered right now, so a chatty stream cannot
			// starve the deadline it is being held to.
			for i, buffered := 0, len(ch); i < buffered; i++ {
				select {
				case evt, open := <-ch:
					if !open {
						return finishOneShot(res, query, errW)
					}
					handle(evt)
				default:
				}
			}
			if res.TerminalType != "" {
				return finishOneShot(res, query, errW)
			}

			if sawTokens {
				fmt.Fprintln(out)
			}
			res.TerminalType = event.CanonicalTypeError
			res.ExitCode = 1
			res.ErrorDetail = abandonedDetail(ctx.Err(), query, time.Since(started))
			fmt.Fprintf(errW, "❌ %s\n", res.ErrorDetail)
			return res
		}
	}
}

// finishOneShot applies the terminal-event contract to a stream that closed.
func finishOneShot(res OneShotResult, query string, errW io.Writer) OneShotResult {
	if res.TerminalType == "" {
		// Exactly one terminal event is mandatory; a stream that ends without one
		// is a failure, reported loudly rather than as an empty success.
		res.ErrorDetail = fmt.Sprintf(
			"the %q query stream ended without a terminal final/error event", query)
		fmt.Fprintf(errW, "❌ %s\n", res.ErrorDetail)
	}
	if res.TerminalType != event.CanonicalTypeFinal {
		res.ExitCode = 1
	}
	return res
}

// Bounds on the non-interactive path. Every one of these replaced an unbounded
// wait: a dependency that refuses is reported by the readiness rows below, but
// one that accepts and then goes quiet is only ever caught by a deadline.
const (
	// readinessEnsureTimeout bounds start-or-attach of the background service and
	// the spawn of the agent's sidecar. It matches the gate's own ensureTimeout
	// and the daemon client's ensure budget on purpose: a first run may still
	// fetch the sidecar binary here, and a bound tighter than the hub's would
	// refuse a cold start the hub completes on the same machine.
	readinessEnsureTimeout = 15 * time.Minute
	// readinessCheckTimeout bounds the readiness probe. Same value as the gate's
	// checkTimeout, over the same call — two paths asking the same question must
	// not disagree about how long the answer may take.
	readinessCheckTimeout = 90 * time.Second
	// DefaultOneShotTimeout bounds one whole non-interactive turn. The relay
	// reads with a 300s idle timeout per chunk and an agent loop can take several
	// steps, so this sits well above a healthy-but-slow run — including one whose
	// first token waits on a cold model load — while still turning a wedged
	// stream into a reportable error instead of a silent hang. `--timeout` raises
	// or lowers it for a run that legitimately needs longer.
	DefaultOneShotTimeout = 15 * time.Minute
)

// ReportReadiness runs the readiness gate headlessly for the one-shot path and
// returns what it found.
//
// It deliberately does NOT render the interactive gate: a script has nobody to
// press a key. Everything it prints goes to errW — the answer's stream stays
// clean — and the blocked report carries the same rows and remedies the gate
// shows for the same condition, because both render the same preflight.Report.
//
// A report that is neither ready nor blocked (an indeterminate row) does not
// stop the run, matching the gate: unknown is named, not refused.
func ReportReadiness(
	ctx context.Context,
	t preflight.Transport,
	cfg preflight.Config,
	errW io.Writer,
) preflight.Report {
	// Start-or-attach the background service and spawn the sidecar first —
	// exactly what the turn does on its own. Skipping it would newly refuse a
	// scripted run on a cold machine that used to start what it needed. A
	// failure is announced, then diagnosed by the rows, which name it with a
	// remedy instead of a raw transport error.
	ensureCtx, cancelEnsure := context.WithTimeout(ctx, readinessEnsureTimeout)
	ensureErr := t.EnsureAgent(ensureCtx, cfg.AgentID)
	cancelEnsure()
	if ensureErr != nil {
		// Through the ladder, so this reads like every other failure the gate
		// reports: what failed, what to do, where to look — never a raw error.
		d := preflight.Ladder{AgentID: cfg.AgentID}.
			Error("start the "+cfg.AgentName+" agent", ensureErr)
		fmt.Fprintf(errW, "⚠️  %s\n", d.String())
	}

	checkCtx, cancelCheck := context.WithTimeout(ctx, readinessCheckTimeout)
	defer cancelCheck()

	rep := preflight.Check(checkCtx, t, cfg)
	writeReadiness(errW, rep)
	return rep
}

// writeReadiness renders a report for a terminal that nobody is watching.
func writeReadiness(errW io.Writer, rep preflight.Report) {
	blocker, blocked := rep.Blocker()
	if !blocked {
		// Nothing failed. Anything that could not be VERIFIED is still named —
		// the run proceeds, but silence here would read as "all clear" — and it
		// keeps its remedy, which is the only thing that makes it fixable.
		for _, row := range rep.Rows {
			if !row.NeedsAttention() {
				continue
			}
			fmt.Fprintf(errW, "  ⚠️  %s: %s (could not be verified — continuing)\n",
				row.Label, row.Line)
			if row.Remedy.Command != "" {
				fmt.Fprintf(errW, "      run: %s\n", row.Remedy.Command)
			}
		}
		return
	}

	fmt.Fprintf(errW, "❌ %s is not ready — %s: %s\n", rep.AgentName, blocker.Label, blocker.Line)
	if blocker.Detail != "" {
		fmt.Fprintf(errW, "   %s\n", blocker.Detail)
	}
	// Report.String is the preflight package's own headless renderer: every row,
	// every remedy. Rendering the rows here instead would let the two paths drift.
	fmt.Fprintf(errW, "\n%s\n", strings.TrimRight(rep.String(), "\n"))
	fmt.Fprintf(errW, "\nNothing was sent to the %s agent. Fix the row above and re-run.\n", rep.AgentName)
}

// abandonedDetail says what stopped the turn, how long it ran, and where to look.
func abandonedDetail(err error, query string, elapsed time.Duration) string {
	if errors.Is(err, context.DeadlineExceeded) {
		return fmt.Sprintf(
			"gave up on the %q query after %s — that is the --timeout bound, not a "+
				"failure the agent reported. Raise it (e.g. --timeout 5m) and re-run; if it "+
				"keeps happening the agent stopped answering, so read `gaia daemon logs` and "+
				"the agent's own log under ~/.gaia/agents/",
			query, elapsed.Round(time.Second))
	}
	return fmt.Sprintf("the %q query was cancelled after %s, before the agent answered",
		query, elapsed.Round(time.Second))
}
