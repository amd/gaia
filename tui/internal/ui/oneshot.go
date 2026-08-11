package ui

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// ExitApprovalRequired is the exit code for a turn that stopped at a
// confirmation gate.
//
// Distinct from 1 on purpose: 1 means something went wrong, this means nothing
// went wrong and nothing was done — the run reached a deliberate safety gate it
// has no way to answer. A script that wants to route those to an approval flow
// can tell them apart; one that only checks for zero is unaffected either way.
// (0/1/130 are already taken by the contract this mirrors.)
const ExitApprovalRequired = 3

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
	// FailedTools are the tools whose last definite result was a failure. A
	// turn that ends with one is not a success, whatever the agent wrote.
	FailedTools []string
	// UndeterminedTools ran without saying whether they worked. Reported, never
	// counted either way.
	UndeterminedTools []string
	// WithheldActions are confirmation-gated actions the run stopped at and
	// could not perform. Not failures — refusals held open on purpose.
	WithheldActions []string
}

// toolLedger records what each tool call proved, in first-seen order.
//
// The exit code needs "did anything fail and stay failed", and an agent that
// retries a tool must be able to clear its own earlier failure. Only a DEFINITE
// later outcome supersedes an earlier one: an unknown result never clears a
// failure, because it is not evidence of recovery.
type toolLedger struct {
	order    []string
	outcomes map[string]event.ToolOutcome
	unknown  map[string]bool

	// pending confirmations, in the order they were asked for. A gate is
	// cleared only by a DEFINITE outcome for the same action: the tool ran, so
	// approval was given somewhere.
	gateOrder []string
	gates     map[string]string // action -> confirm URL, empty when there is none
}

func newToolLedger() *toolLedger {
	return &toolLedger{
		outcomes: map[string]event.ToolOutcome{},
		unknown:  map[string]bool{},
		gates:    map[string]string{},
	}
}

// gate records a confirmation the run stopped at.
func (l *toolLedger) gate(action, confirmURL string) {
	if action == "" {
		action = "an unnamed action"
	}
	if _, seen := l.gates[action]; !seen {
		l.gateOrder = append(l.gateOrder, action)
	}
	l.gates[action] = confirmURL
}

// withheld lists the gates nothing resolved.
func (l *toolLedger) withheld() []string {
	var out []string
	for _, action := range l.gateOrder {
		if _, ran := l.outcomes[action]; !ran {
			out = append(out, action)
		}
	}
	return out
}

// confirmURLFor is the approval link for a gate, when the agent sent one.
func (l *toolLedger) confirmURLFor(action string) string { return l.gates[action] }

func (l *toolLedger) record(tool string, outcome event.ToolOutcome) {
	if tool == "" {
		tool = "an unnamed tool"
	}
	if !containsString(l.order, tool) {
		l.order = append(l.order, tool)
	}
	if outcome == event.ToolOutcomeUnknown {
		l.unknown[tool] = true
		return
	}
	l.outcomes[tool] = outcome
}

// failed lists the tools whose last definite outcome was a failure.
func (l *toolLedger) failed() []string {
	var out []string
	for _, tool := range l.order {
		if l.outcomes[tool] == event.ToolOutcomeFailed {
			out = append(out, tool)
		}
	}
	return out
}

// undetermined lists tools that ran and never said whether they worked.
func (l *toolLedger) undetermined() []string {
	var out []string
	for _, tool := range l.order {
		if _, definite := l.outcomes[tool]; !definite && l.unknown[tool] {
			out = append(out, tool)
		}
	}
	return out
}

func containsString(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}

// RunOneShot drives a single turn and renders it to plain streams — no alt
// screen, no TTY required.
//
// The answer goes to out and progress to errW, so `... --query X > answer.txt`
// captures exactly the answer and nothing else. This is the same split the
// `gaia <agent>` CLI uses, and it is what makes the transport testable from a
// script and from CI.
//
// debugf receives every event as it arrives, raw payloads included; nil is
// silent. It is what --debug reaches for when a turn answers with nothing.
func RunOneShot(
	ctx context.Context,
	c client.AgentClient,
	query string,
	out, errW io.Writer,
	debugf func(format string, args ...any),
) OneShotResult {
	if debugf == nil {
		debugf = func(string, ...any) {}
	}
	started := time.Now()
	debugf("one-shot: query=%q", query)

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
		tools     = newToolLedger()
	)

	handle := func(evt interface{}) {
		switch e := evt.(type) {
		case event.CanonicalStatusEvent:
			debugf("status %q", e.Message)
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
			debugf("tool_call %s args=%s", e.Tool, rawOrDash(e.Args))
			fmt.Fprintf(errW, "  🔧 %s\n", e.Tool)

		case event.CanonicalToolResultEvent:
			outcome, toolErr := event.ToolOutcomeOf(e)
			tools.record(e.Tool, outcome)
			debugf("tool_result %s outcome=%s render=%q data=%s",
				e.Tool, outcome, e.Render, rawOrDash(e.Data))
			// A tool's own error is the most actionable text in the run — it is
			// the tool author's remedy, not the model's paraphrase of it.
			if outcome == event.ToolOutcomeFailed {
				writeToolError(errW, e.Tool, toolErr)
				return
			}
			// Not a tick: the canonical tool_result event carries no success
			// flag, so a result with no error signal proves nothing more.
			if e.Render != "" {
				fmt.Fprintf(errW, "  ← %s returned (%s)\n", e.Tool, e.Render)
			} else {
				fmt.Fprintf(errW, "  ← %s returned\n", e.Tool)
			}

		case event.CanonicalNeedsConfirmationEvent:
			tools.gate(e.Action, e.ConfirmURL)
			debugf("needs_confirmation %s run_id=%s confirm_url=%q", e.Action, e.RunID, e.ConfirmURL)
			line := "  ⚠️  confirmation needed: " + e.Action
			if summary := strings.TrimSpace(e.Summary); summary != "" {
				line += " — " + summary
			}
			fmt.Fprintln(errW, line)

		case event.CanonicalFinalEvent:
			debugf("final: %d chars, usage=%s", len(e.Answer), rawOrDash(e.Usage))
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
			debugf("error: status=%d source=%q", e.Status, e.Source)
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

		case event.CanonicalNoticeEvent:
			// A one-shot does not claim it can answer questions, so it should
			// never be sent one — but a notice the client raises must still land
			// somewhere rather than falling to the unhandled-event branch.
			fmt.Fprintf(errW, "  %s\n", e.Text)

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
		case event.StepEvent:
			fmt.Fprintf(errW, "  … Step %d/%d\n", e.Step, e.Total)
		case event.ThinkingEvent:
			// Reasoning is not progress a script needs; --debug keeps it.
			debugf("thinking: %s", e.Content)
		case event.ToolArgsEvent:
			debugf("tool_args %s: %s", e.Tool, rawOrDash(e.Args))
		case event.ToolEndEvent:
			// tool_result already reported the outcome.
			debugf("tool_end success=%t", e.Success)
		case event.ErrorEvent:
			res.TerminalType = event.CanonicalTypeError
			res.ErrorDetail = e.Content
			fmt.Fprintf(errW, "❌ %s\n", e.Content)
		case event.ToolResultEvent:
			// Was falling through to "[unhandled event]", so a subprocess
			// agent's tool failure never reached the user at all.
			outcome, toolErr := event.LegacyToolOutcomeOf(e)
			tools.record(e.Title, outcome)
			debugf("tool_result %s outcome=%s success=%t data=%s",
				e.Title, outcome, e.Success, rawOrDash(e.ResultData))
			if outcome == event.ToolOutcomeFailed {
				writeToolError(errW, e.Title, toolErr)
				return
			}
			if e.Summary != "" {
				fmt.Fprintf(errW, "  ← %s returned (%s)\n", e.Title, e.Summary)
			} else {
				fmt.Fprintf(errW, "  ← %s returned\n", e.Title)
			}
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
				return finishOneShot(res, tools, query, errW)
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
						return finishOneShot(res, tools, query, errW)
					}
					handle(evt)
				default:
				}
			}
			if res.TerminalType != "" {
				return finishOneShot(res, tools, query, errW)
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

// writeToolError prints a tool's own failure verbatim. The message keeps its
// line breaks: a remedy is often a command on its own line, and reflowing it
// would break the one thing the user came for.
func writeToolError(w io.Writer, tool string, te event.ToolError) {
	head := "  ✗ " + tool + " failed"
	if te.Code != "" {
		head += " — " + te.Code
	}
	message := strings.TrimRight(te.Message, "\n")
	if message == "" {
		fmt.Fprintf(w, "%s (the tool reported no detail)\n", head)
		return
	}
	lines := strings.Split(message, "\n")
	fmt.Fprintf(w, "%s: %s\n", head, lines[0])
	for _, line := range lines[1:] {
		fmt.Fprintf(w, "    %s\n", line)
	}
}

// rawOrDash renders a raw JSON payload for a debug line.
func rawOrDash(raw json.RawMessage) string {
	if len(raw) == 0 {
		return "-"
	}
	return string(raw)
}

// finishOneShot applies the terminal-event contract, then the tool-outcome one.
//
// `run --help` promises an exit code a script can act on (0 answered / 1 failed
// / 3 needs approval), and that promise is what makes the flag usable at all. A
// turn whose only tool call came back `{"ok": false, …}` and which then wrote an
// apology is not a success — exiting 0 there fired the caller's `&& next-step`
// over work that never happened.
func finishOneShot(res OneShotResult, tools *toolLedger, query string, errW io.Writer) OneShotResult {
	res.FailedTools = tools.failed()
	res.UndeterminedTools = tools.undetermined()
	res.WithheldActions = tools.withheld()

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

	// Never silent, even when it changes nothing: an outcome nobody stated is
	// not evidence the work happened, and reporting it green on the agent's
	// behalf is the failure this whole sweep has been about.
	if len(res.UndeterminedTools) > 0 {
		fmt.Fprintf(errW,
			"  ⚠️  %s did not report whether the work succeeded, so this answer is unverified\n",
			strings.Join(res.UndeterminedTools, ", "))
	}

	if len(res.FailedTools) > 0 && res.ExitCode == 0 {
		res.ExitCode = 1
		fmt.Fprintf(errW,
			"❌ %s failed and nothing recovered it, so this turn did not do what was asked "+
				"(exit 1). The agent's answer is on stdout; the tool's own error is above.\n",
			strings.Join(res.FailedTools, ", "))
	}

	// A gate is not an error, so it is reported after any real failure and only
	// decides the exit code when nothing else did. Either way the turn is
	// non-zero: the action did not happen, and `… && next-step` must not run.
	if len(res.WithheldActions) > 0 {
		if res.ExitCode == 0 {
			res.ExitCode = ExitApprovalRequired
		}
		writeWithheld(errW, res.WithheldActions, res.ExitCode, tools.confirmURLFor)
	}
	return res
}

// writeWithheld says what was not done, and does not invent a way to approve
// it. A --query run has no session for anyone to answer a gate on, full stop.
// The interactive chat does now have a real y/n/Esc modal (ui/chat/canonical.go,
// components.ConfirmationModel), but it cannot DELIVER an approval either
// against the current sidecar contract — needs_confirmation is immediately
// terminal with no confirm_url (spec §5, D1 unsigned) — so naming a command
// that does not exist would be the same mistake either surface can make; the
// agent's own final answer already explains the route for that agent.
func writeWithheld(w io.Writer, actions []string, exitCode int, confirmURL func(string) string) {
	fmt.Fprintf(w,
		"⛔ %s needed approval and a --query run has no way to give it, so it was NOT "+
			"performed (exit %d). Nothing changed. The agent's answer on stdout says how to "+
			"approve it.\n",
		strings.Join(actions, ", "), exitCode)
	for _, action := range actions {
		if url := confirmURL(action); url != "" {
			fmt.Fprintf(w, "    approve %s at: %s\n", action, url)
		}
	}
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
		// Nothing the agent needs failed. Anything that could not be VERIFIED is
		// still named — the run proceeds, but silence here would read as "all
		// clear" — and it keeps its remedy, which is the only thing that makes it
		// fixable.
		for _, row := range rep.Rows {
			if !row.NeedsAttention() {
				continue
			}
			// A row that PROVED broken is not one that "could not be verified":
			// only an optional row reaches here failed, and the ask it gates is
			// what will fail, so say that rather than softening it.
			verdict := "could not be verified"
			if row.State == preflight.StateFailed {
				verdict = "not working; only the asks that need it will fail"
			}
			fmt.Fprintf(errW, "  ⚠️  %s: %s (%s — continuing)\n",
				row.Label, row.Line, verdict)
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
