package client

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

var (
	_ ToolPermissionResponder = (*SubprocessClient)(nil)
	_ PermissionBypasser      = (*SubprocessClient)(nil)
	_ AgentCanceler           = (*SubprocessClient)(nil)
	_ LocalAgentStopper       = (*SubprocessClient)(nil)
)

// closeGrace bounds how long Close() waits for an in-flight turn's reader to
// finish before giving up on a clean reap.
const closeGrace = 2 * time.Second

// detectLemonadeURL probes common Lemonade Server ports and returns the first reachable URL.
func detectLemonadeURL() string {
	ports := []string{"13305", "8000"}
	client := &http.Client{Timeout: 2 * time.Second}

	for _, port := range ports {
		url := "http://localhost:" + port + "/api/v1"
		resp, err := client.Get(url + "/models")
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				return url
			}
		}
	}
	return ""
}

// procHandle owns one child process AND every process that child started.
//
// Reaping is the READER's job: os/exec forbids calling Wait before all reads
// from a pipe have completed, so a kill from elsewhere must not also reap — it
// would close the stdout pipe under the reader and turn a deliberate kill into a
// spurious "file already closed" read error.
type procHandle struct {
	cmd      *exec.Cmd
	waitOnce sync.Once
	state    *os.ProcessState
	// served is set once the child has finished a turn, i.e. it now holds
	// session state (skills, grants, history) a replacement would not have.
	served atomic.Bool

	mu sync.Mutex
	// group is nil once released: a job handle is a reusable integer, so a
	// kill racing the reap must never terminate by a handle already closed.
	group   *processGroup
	killErr error
	// groupKilled: macOS refuses (EPERM) a second group kill once only the
	// unreaped leader is left, so a repeat kill must not reach the kernel.
	groupKilled bool
}

// reap waits for the child and returns its final state. Safe to call more than
// once; only the first call waits. Call it only once reads are done.
func (p *procHandle) reap() *os.ProcessState {
	p.waitOnce.Do(func() {
		_ = p.cmd.Wait()
		p.state = p.cmd.ProcessState
		p.mu.Lock()
		if p.group != nil {
			p.group.close()
			p.group = nil
		}
		p.mu.Unlock()
	})
	return p.state
}

// killError is the most recent kill's failure, or nil.
func (p *procHandle) killError() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.killErr
}

// kill terminates the child AND its descendants, without reaping it.
//
// The whole tree, not just cmd.Process: the released agent is a PyInstaller
// one-file binary, so cmd.Process is the bootloader and the interpreter that
// runs the turn is its child, holding both ends of the pipe. Killing the
// bootloader alone left the cancelled tool call running to completion, and the
// surviving child then consumed the user's next message.
//
// A group kill that fails must never be reported as a stopped agent: the
// direct kill then stops at least the process we started, and both failures
// are returned.
func (p *procHandle) kill() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.killErr = p.killLocked()
	return p.killErr
}

func (p *procHandle) killLocked() error {
	if p.group != nil {
		if p.groupKilled {
			return nil
		}
		gerr := p.group.terminate()
		if gerr == nil {
			p.groupKilled = true
			// The group includes the process we started. Killing it again races
			// its exit, and Windows answers TerminateProcess on a dying process
			// with "Access is denied" — a failure report for a kill that worked.
			return nil
		}
		if kerr := p.killDirect(); kerr != nil {
			return errors.Join(gerr, kerr)
		}
		return gerr
	}
	// The group is released only at reap, so the process has been waited for.
	return p.killDirect()
}

func (p *procHandle) killDirect() error {
	if p.cmd.Process == nil {
		return nil
	}
	if err := p.cmd.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
		return fmt.Errorf("could not stop agent process %d: %w", p.cmd.Process.Pid, err)
	}
	return nil
}

// SubprocessClient communicates with a local agent binary via stdin/stdout JSONL.
// Send() calls must be serialized — do not overlap two Send() calls.
type SubprocessClient struct {
	path  string
	args  []string
	debug bool
	// canonical selects the event dialect read off the pipe: the frozen legacy
	// vocabulary (false) or the canonical one (true).
	canonical bool
	// trace records every event line this client reads, verbatim. Nil means
	// tracing is off (--trace not passed).
	trace *event.TraceWriter

	mu      sync.Mutex
	proc    *procHandle
	stdin   io.WriteCloser
	stdout  *bufio.Scanner
	stderr  *bytes.Buffer
	started bool
	// turnDone is closed by the in-flight turn's reader when it exits. nil when
	// no turn is running.
	turnDone chan struct{}
	// bypass is the permission mode the SESSION is in, which is not necessarily
	// the one the child was launched with. A respawn rebuilds argv from this, so
	// a `/bypass off` typed before a hard cancel cannot come back on by itself.
	bypass bool
	// respawned records that the child now backing this client is a REPLACEMENT
	// for one that was killed. Read and cleared by the next Send, which reports
	// it: the replacement has no loaded skills, no "always" grants and no prompt
	// history, and a user who is not told that is reasoning about a session the
	// agent no longer has.
	respawned string
	// turnCtx is the running turn's context. Once the caller has cancelled it
	// the turn is being torn down, and the next Send waits for that to finish.
	turnCtx context.Context
}

// NewSubprocessClient creates a client for an agent binary and its arguments.
//
// argv is taken pre-split: a single command string would have to be re-split on
// whitespace, which corrupts any path containing a space. Callers holding one
// string (e.g. `gaia tui chat --subprocess "..."`) split it with
// SplitCommandLine, which honours quoting.
func NewSubprocessClient(path string, args []string, debug bool) *SubprocessClient {
	c := &SubprocessClient{
		path:  path,
		args:  args,
		debug: debug,
	}
	c.bypass = c.BypassAtLaunch()
	return c
}

// spawnArgs is argv for the NEXT child: the launch arguments with the bypass
// flag forced to match the session's current permission mode.
//
// Respawning from s.args verbatim silently reverted `/bypass off` — the killed
// child had prompts back on, its replacement did not, and the banner that is
// supposed to make unattended mode impossible to miss was gone. Deriving argv
// from the live mode means the flag cannot disagree with it; the control line
// SetBypassPermissions writes stays the mechanism for a LIVE child.
func (s *SubprocessClient) spawnArgs(bypass bool) []string {
	out := make([]string, 0, len(s.args)+1)
	for _, a := range s.args {
		if a == BypassPermissionsFlag {
			continue
		}
		out = append(out, a)
	}
	if bypass {
		out = append(out, BypassPermissionsFlag)
	}
	return out
}

// NewCanonicalSubprocessClient is NewSubprocessClient for an agent that speaks
// the CANONICAL event vocabulary over the pipe rather than the frozen legacy one.
//
// Same transport, different dialect. Canonical events carry the tool narration
// and result previews the activity log renders; the legacy vocabulary has
// nowhere to put them, so an agent moved onto this transport and parsed as
// legacy would silently lose its progress reporting.
func NewCanonicalSubprocessClient(path string, args []string, debug bool) *SubprocessClient {
	c := NewSubprocessClient(path, args, debug)
	c.canonical = true
	return c
}

// WithTrace records every event line this client reads to w, verbatim. A nil w
// leaves tracing off. Returns the client so it can be chained onto a constructor.
func (s *SubprocessClient) WithTrace(w *event.TraceWriter) *SubprocessClient {
	s.trace = w
	return s
}

// turnState is everything one turn needs, captured under a single lock so it can
// never be read while a concurrent cancel is clearing the client's fields.
type turnState struct {
	stdin    io.WriteCloser
	scanner  *bufio.Scanner
	proc     *procHandle
	stderr   *bytes.Buffer
	turnDone chan struct{}
	// notice is non-empty when this turn is the first against a REPLACEMENT
	// child, and says what the replacement no longer knows.
	notice string
}

// startLocked spawns the subprocess if needed and returns the turn's handles.
// The caller MUST hold s.mu.
func (s *SubprocessClient) startLocked() (turnState, error) {
	if s.started {
		// Serialization is a contract, not a hope: two turns sharing one
		// bufio.Scanner means two goroutines reading the same pipe, and the
		// first one to finish closes it under the second ("file already
		// closed"). A caller that got here overlapped its Send calls.
		if s.turnDone != nil {
			select {
			case <-s.turnDone:
			default:
				if kerr := s.proc.killError(); kerr != nil {
					return turnState{}, fmt.Errorf(
						"the previous message is still running because stopping the agent failed "+
							"(%v) — end the leftover gaia-agent process in Task Manager, then restart the TUI", kerr)
				}
				return turnState{}, fmt.Errorf(
					"the previous message is still running, so this one cannot be sent — " +
						"press Esc to stop it first")
			}
		}
		done := make(chan struct{})
		s.turnDone = done
		notice := s.respawned
		s.respawned = ""
		return turnState{s.stdin, s.stdout, s.proc, s.stderr, done, notice}, nil
	}
	if s.path == "" {
		return turnState{}, fmt.Errorf("no agent binary was given, so nothing can be launched")
	}

	cmd := exec.Command(s.path, s.spawnArgs(s.bypass)...)
	stderr := &bytes.Buffer{}
	cmd.Stderr = stderr

	// Created before Start so a POSIX child is forked straight into its own
	// process group; on Windows it starts suspended and joins the job before it runs.
	group, err := newProcessGroup()
	if err != nil {
		return turnState{}, err
	}
	group.prepare(cmd)

	// Auto-detect Lemonade URL if not set in environment
	if os.Getenv("LEMONADE_BASE_URL") == "" {
		if url := detectLemonadeURL(); url != "" {
			cmd.Env = append(os.Environ(), "LEMONADE_BASE_URL="+url)
			if s.debug {
				fmt.Fprintf(os.Stderr, "[DEBUG] Auto-detected Lemonade at %s\n", url)
			}
		}
	}

	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		group.close()
		return turnState{}, fmt.Errorf("failed to create stdin pipe: %w", err)
	}
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		group.close()
		return turnState{}, fmt.Errorf("failed to create stdout pipe: %w", err)
	}

	scanner := bufio.NewScanner(stdoutPipe)
	// 1MB buffer for large tool outputs
	scanner.Buffer(make([]byte, 0, 1024*1024), 1024*1024)

	if err := cmd.Start(); err != nil {
		group.close()
		return turnState{}, fmt.Errorf("failed to start agent %q: %w", s.path, err)
	}
	// A grouping failure is fatal, not a warning: without it a later cancel
	// would kill only the bootloader and leave the real agent running the tool
	// call the user asked to stop.
	if err := group.attach(cmd); err != nil {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		group.close()
		return turnState{}, fmt.Errorf(
			"started agent %q but could not take ownership of its child processes, "+
				"so a cancelled turn could not be stopped — refusing to run it: %w", s.path, err)
	}

	done := make(chan struct{})
	s.stdin = stdinPipe
	s.stdout = scanner
	s.stderr = stderr
	s.proc = &procHandle{cmd: cmd, group: group}
	s.started = true
	s.turnDone = done
	notice := s.respawned
	s.respawned = ""
	return turnState{stdinPipe, scanner, s.proc, stderr, done, notice}, nil
}

// awaitAbandonedTurn lets a turn whose caller has already given up finish
// tearing down, so a message typed straight after a hard stop starts the
// replacement agent instead of being refused as overlapping. Bounded: a
// teardown that never finishes still reaches startLocked's refusal.
func (s *SubprocessClient) awaitAbandonedTurn() {
	s.mu.Lock()
	done, ctx := s.turnDone, s.turnCtx
	s.mu.Unlock()
	if done == nil || ctx == nil || ctx.Err() == nil {
		return
	}
	select {
	case <-done:
	case <-time.After(closeGrace):
	}
}

// Send writes a query to stdin and returns a channel of parsed events.
func (s *SubprocessClient) Send(ctx context.Context, query string) (<-chan interface{}, error) {
	s.awaitAbandonedTurn()
	s.mu.Lock()
	st, err := s.startLocked()
	if err == nil {
		s.turnCtx = ctx
	}
	debug := s.debug
	s.mu.Unlock()
	if err != nil {
		return nil, err
	}

	// JSON-wrapped, never raw: the agent reads stdin a LINE at a time, so a
	// query written verbatim is split at every newline and each fragment
	// becomes its own turn. A five-line paste asked five questions, and the
	// agent answered the first one insisting it was all it had been sent.
	line, err := json.Marshal(map[string]string{queryKey: query})
	if err != nil {
		return nil, fmt.Errorf("failed to encode query: %w", err)
	}
	if _, err := fmt.Fprintf(st.stdin, "%s\n", line); err != nil {
		// The child is dead or its stdin is gone (the common case: agent
		// construction failed — Lemonade down — it printed its error and
		// exited, and the reader returned at that terminal event without
		// resetting). Keeping the state marks the corpse as "started" and
		// every later Send would fail exactly like this one, telling the
		// user to retry the one thing that can never work.
		s.resetDeadChild(st.proc)
		close(st.turnDone)
		return nil, fmt.Errorf(
			"failed to write to the agent (it will be restarted on your next message): %w", err)
	}

	ch := make(chan interface{}, 32)
	if st.notice != "" {
		ch <- event.CanonicalNoticeEvent{Text: st.notice}
	}

	// An ABANDONED turn (the caller's context; Cancel is the cooperative path)
	// must stop the child: abandoning the read while the agent keeps writing
	// leaves the tail of this turn's output in the pipe, which the NEXT turn
	// would read as its own. Kill only — the reader reaps. A failed kill is
	// recorded on the handle and reported by whichever path meets it next.
	go func() {
		select {
		case <-ctx.Done():
			// select picks at random when both are ready; a turn that already
			// ended has nothing to abandon, and killing it would lose the session.
			select {
			case <-st.turnDone:
				return
			default:
			}
			_ = st.proc.kill()
		case <-st.turnDone:
		}
	}()

	go func() {
		defer close(ch)
		defer close(st.turnDone)

		// Registered last so it runs FIRST: every exit path from this goroutine
		// — including the early returns mid-loop — must reset the client when the
		// turn was cancelled, or the next Send reuses the child we just killed
		// and reads nothing.
		defer func() {
			if ctx.Err() != nil {
				// resetDeadChild, not a hand-rolled subset: kill() is
				// idempotent on the already-killed child, and one shared
				// sequence means a future reset change cannot miss the
				// cancellation path.
				s.resetDeadChild(st.proc)
			}
		}()

		// Deterministic: once the turn is cancelled nothing is pushed into the
		// abandoned channel. Selecting on both a ready send and a ready
		// ctx.Done() would pick randomly and leak events into the next turn.
		emit := func(evt interface{}) bool {
			if ctx.Err() != nil {
				return false
			}
			select {
			case ch <- evt:
				return true
			case <-ctx.Done():
				return false
			}
		}

		for st.scanner.Scan() {
			line := st.scanner.Bytes()
			if len(line) == 0 {
				continue
			}

			// Traced BEFORE parsing, so the file keeps the bytes that actually
			// arrived and an unreadable line is recorded rather than lost.
			//
			// A failure is not printed HERE: the alt screen owns the terminal
			// mid-turn, so a raw stderr write would land on top of the UI. The
			// writer keeps the first failure and Close() reports it once the
			// event loop has stopped.
			if terr := s.trace.Write(line); terr != nil && debug {
				fmt.Fprintf(os.Stderr, "[DEBUG] trace: %v\n", terr)
			}

			var evt interface{}
			var perr error
			if s.canonical {
				evt = event.ParseCanonicalEvent(line)
			} else {
				evt, perr = event.ParseEvent(line)
			}
			if perr != nil {
				// Visible, not dropped: a status warning keeps the turn alive
				// while making a bad producer obvious.
				if debug {
					fmt.Fprintf(os.Stderr, "[DEBUG] parse error: %v (line: %s)\n", perr, string(line))
				}
				if !emit(event.StatusEvent{
					Type:    "status",
					Status:  "warning",
					Message: fmt.Sprintf("unreadable agent event (%v): %s", perr, truncateLine(string(line))),
				}) {
					return
				}
				continue
			}

			// Skip stale "complete" status from a previous turn's trailing event
			if se, ok := evt.(event.StatusEvent); ok && se.Status == "complete" {
				continue
			}

			if !emit(evt) {
				return
			}

			// Turn boundary — stop reading after terminal events. Both dialects
			// are listed because the check runs before we know which one this
			// agent speaks: a canonical agent never sends AnswerEvent, so a
			// legacy-only check reads past the end of the turn and blocks until
			// something kills the child (a one-shot `run --query` sat for its
			// whole timeout before being reaped).
			if isTerminalEvent(evt) {
				st.proc.served.Store(true)
				return
			}
		}

		// The read is over, so reaping is safe from here on. A cancelled turn
		// killed the child on purpose: a dead child is the expected outcome, not
		// an error to report, and the deferred reset above respawns next time.
		if ctx.Err() != nil {
			return
		}

		if err := st.scanner.Err(); err != nil {
			emit(event.AgentErrorEvent{
				Type:    "agent_error",
				Content: fmt.Sprintf("agent stdout read error: %v", err),
			})
			// A scanner error (e.g. a line over the 1MB cap) is permanent on
			// this scanner — without a reset, every later turn re-emits this
			// same error without ever reading again.
			s.resetDeadChild(st.proc)
			return
		}

		// The child exited on its own — reap it for the exit code and report a
		// non-zero one. The next Send respawns.
		state := st.proc.reap()
		s.discard(st.proc, nil)
		if state != nil && !state.Success() {
			stderrContent := st.stderr.String()
			msg := describeAgentExit(state.ExitCode())
			if stderrContent != "" {
				msg += "\n" + stderrContent
			}
			emit(event.AgentErrorEvent{
				Type:    "agent_error",
				Content: msg,
			})
		}
	}()

	return ch, nil
}

// isTerminalEvent reports whether evt ends a turn in either dialect.
func isTerminalEvent(evt interface{}) bool {
	switch evt.(type) {
	case event.AnswerEvent, event.AgentErrorEvent, event.DoneEvent,
		event.CanonicalFinalEvent, event.CanonicalErrorEvent:
		return true
	}
	return false
}

// windowsTerminated is what Windows reports for a force-terminated process:
// 0xFFFFFFFF, which Go's ExitCode() hands back as this decimal.
const windowsTerminated = 4294967295

// describeAgentExit turns a raw exit status into a line a user can act on.
//
// The raw form was "agent process exited with code 4294967295" — observed after
// killing the agent mid-turn. That number is 0xFFFFFFFF, it is not a code the
// agent chose, and to a reader it looks like memory corruption rather than "it
// was killed".
//
// Both branches end with what actually happens next. The transport respawns the
// child on the following Send, so recovery needs no action — and a user staring
// at an error box has no way to know that unless it says so.
func describeAgentExit(code int) string {
	if code == windowsTerminated || code == -1 {
		return "The agent process was stopped. Your next message will start it again."
	}
	return fmt.Sprintf(
		"The agent process exited unexpectedly (code %d). "+
			"Your next message will start it again.", code)
}

// controlKey marks a stdin line as a control message rather than a query. Must
// match gaia_agent.stdio.CONTROL_KEY — the agent only treats a line as control
// if it parses as a JSON object carrying exactly this key, so a question that
// merely looks like JSON is still a question.
const controlKey = "gaia_control"

// queryKey wraps a user's question so its newlines survive the trip. Must match
// gaia_agent.stdio.QUERY_KEY. The agent still accepts a bare line as a query, so
// an older child paired with this build keeps working — it just cannot carry a
// multi-line question.
const queryKey = "gaia_query"

// writeControl sends one control message to the child's stdin.
//
// Safe to call DURING a turn, which is the entire point: a permission decision
// is worth nothing after the prompt it answers has expired. stdin and stdout
// are independent directions of the pipe, and the agent reads stdin on its own
// thread, so this does not contend with the turn's reader.
//
// A control message for a child that was never started is an error, not a
// silent no-op: it means the caller thinks it is talking to an agent that does
// not exist, and swallowing that produces a UI that looks like it worked.
//
// IMPORTANT for anything built on this channel later: it is fire-and-forget
// ONLY. Nothing reads stdout except the goroutine Send spawns below, and that
// goroutine exists only for the duration of one turn — between turns nobody is
// scanning the pipe at all. A control message answered by writing a reply
// event (rather than resolving state already parked in-process, the way
// RespondToolPermission/SetBypassPermissions do) would sit unread in the OS
// pipe buffer until some LATER, unrelated Send() call started scanning again —
// at which point it would be misread as the first event of THAT turn. This is
// why live model switching (`/model`, gaia_agent.stdio.run_model_command) does
// NOT use this channel despite looking like a natural fit: it needs an actual
// response (the switched-to model, or why the switch was refused), so it rides
// the ordinary query channel (Send) like a real turn instead, guaranteeing a
// reader is actually listening when the answer comes back.
func (s *SubprocessClient) writeControl(fields map[string]interface{}) error {
	s.mu.Lock()
	stdin, started := s.stdin, s.started
	s.mu.Unlock()

	if !started || stdin == nil {
		return fmt.Errorf("the agent process is not running, so it cannot be told %q", fields[controlKey])
	}
	line, err := json.Marshal(fields)
	if err != nil {
		return fmt.Errorf("could not encode the %q control message: %w", fields[controlKey], err)
	}
	if _, err := fmt.Fprintf(stdin, "%s\n", line); err != nil {
		return fmt.Errorf("could not reach the agent to send %q: %w", fields[controlKey], err)
	}
	return nil
}

// RespondToolPermission delivers the user's yes/no/always decision to the
// agent thread parked on the prompt.
func (s *SubprocessClient) RespondToolPermission(confirmID string, decision PermissionDecision) error {
	fields := map[string]interface{}{
		controlKey: "tool_decision",
		"decision": string(decision),
	}
	if confirmID != "" {
		fields["confirm_id"] = confirmID
	}
	return s.writeControl(fields)
}

// SetBypassPermissions turns unattended approval on or off for the session.
//
// Recorded on the client as well as sent, because the client is what outlives
// a respawn: the next child's argv is built from it. With no child running
// there is nobody to tell, and recording it IS the whole change.
func (s *SubprocessClient) SetBypassPermissions(enabled bool) error {
	s.mu.Lock()
	started := s.started
	// A failed disable must never restore bypass on respawn. A failed enable
	// must keep the prior mode, because the UI reports that enabling failed.
	if !enabled || !started {
		s.bypass = enabled
	}
	s.mu.Unlock()
	if !started {
		return nil
	}

	if err := s.writeControl(map[string]interface{}{
		controlKey: "bypass",
		"enabled":  enabled,
	}); err != nil {
		return err
	}
	if enabled {
		s.mu.Lock()
		s.bypass = true
		s.mu.Unlock()
	}
	return nil
}

// Cancel asks the child to stop the running turn WITHOUT killing it.
//
// Killing throws away everything the child holds in memory: loaded skills,
// "always" grants, the prompt history, a /bypass toggle. A cooperative stop
// keeps the process, and the turn ends through its normal terminal event,
// which is what the caller's still-open read settles on. Killing stays the
// escalation for a turn that does not stop: the caller's context cancel.
//
// An agent too old to know the verb logs and ignores it, so the turn runs on
// until the caller escalates — which is why the caller keeps its read and its
// CancelFunc instead of treating this call as the end of the turn.
func (s *SubprocessClient) Cancel(context.Context) error {
	s.mu.Lock()
	started, turnDone := s.started, s.turnDone
	s.mu.Unlock()
	if !started || turnDone == nil {
		return nil
	}
	select {
	case <-turnDone:
		return nil
	default:
	}
	return s.writeControl(map[string]interface{}{controlKey: "cancel"})
}

// AbortStopsAgent implements LocalAgentStopper: the agent is this process's
// child, so abandoning a turn kills it rather than leaving it running
// somewhere this client cannot reach.
func (s *SubprocessClient) AbortStopsAgent() bool { return true }

// BypassAtLaunch reports whether the child was spawned with bypass already on,
// so the UI can show the warning from the very first frame rather than only
// after a toggle.
func (s *SubprocessClient) BypassAtLaunch() bool {
	for _, a := range s.args {
		if a == "--bypass-permissions" {
			return true
		}
	}
	return false
}

// ClaudeAtLaunch reports whether the child was spawned with --use-claude, so
// the UI's "claude" chip is driven by what actually reached the child's argv
// rather than by a second bool that could disagree with it.
func (s *SubprocessClient) ClaudeAtLaunch() bool {
	for _, a := range s.args {
		if a == UseClaudeFlag {
			return true
		}
	}
	return false
}

// ClaudeModelAtLaunch reports which Claude model the child was spawned with,
// or "" when none was named (--use-claude alone, or a local launch).
//
// Read back off argv for the same reason ClaudeAtLaunch is: the header must
// name what actually reached the child, never a second copy of the flag that
// could disagree. It is what lets the chip say "claude · haiku-4.5" from the
// first frame instead of a bare "claude" -- the agent's own model-state ping
// is authoritative, but it is not read until the first turn (see
// gaia_agent.stdio.main), which on a session that opens and waits is never.
func (s *SubprocessClient) ClaudeModelAtLaunch() string {
	for i, a := range s.args {
		if a == ClaudeModelFlag && i+1 < len(s.args) {
			return s.args[i+1]
		}
	}
	return ""
}

// resetDeadChild kills, reaps, and discards a child the client can no longer
// talk to. One helper, because the sequence is easy to get subtly wrong: a
// missed discard leaves a corpse marked "started" and every later Send fails
// against it. Closing the turn's done channel stays at the call sites — only
// the pre-reader failure path owns an unclosed one.
func (s *SubprocessClient) resetDeadChild(proc *procHandle) {
	killErr := proc.kill()
	proc.reap()
	s.discard(proc, killErr)
}

// discard clears the client's process state, but only if it still refers to
// proc — a newer Send may already have respawned.
//
// When the lost child had served a turn, the next Send says so: its
// replacement starts empty, and the user should hear that from the transport
// rather than find out when a follow-up resolves against nothing.
func (s *SubprocessClient) discard(proc *procHandle, killErr error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.proc != proc {
		return
	}
	s.proc = nil
	s.stdin = nil
	s.stdout = nil
	s.stderr = nil
	s.started = false
	s.turnDone = nil
	s.turnCtx = nil
	if proc.served.Load() || killErr != nil {
		s.respawned = respawnNotice(s.bypass, killErr)
	}
}

// respawnNotice says what a replacement child no longer has. The permission
// mode is always stated: it is the state a user acts on without looking.
func respawnNotice(bypass bool, killErr error) string {
	var b strings.Builder
	b.WriteString("The agent was restarted. The new process does not have this session's " +
		"loaded skills, \"always allow\" grants or earlier messages — repeat anything it needs.")
	if bypass {
		b.WriteString(" Bypass permissions is still ON: it runs tools without asking.")
	} else {
		b.WriteString(" Permission prompts are on.")
	}
	if killErr != nil {
		fmt.Fprintf(&b, " Stopping the previous process failed, so it may still be running "+
			"(end any leftover gaia-agent process in Task Manager): %v", killErr)
	}
	return b.String()
}

// Close terminates the subprocess.
func (s *SubprocessClient) Close() error {
	s.mu.Lock()
	if !s.started {
		s.mu.Unlock()
		return nil
	}
	proc, stdin, turnDone := s.proc, s.stdin, s.turnDone
	s.proc = nil
	s.stdin = nil
	s.stdout = nil
	s.stderr = nil
	s.started = false
	s.turnDone = nil
	s.mu.Unlock()

	// Closing stdin is how a well-behaved agent is asked to exit.
	if stdin != nil {
		stdin.Close()
	}
	if proc == nil {
		return nil
	}

	// If a turn's reader is still in flight it owns the reap (os/exec forbids
	// Wait before reads complete), so wait for it rather than racing it.
	if turnDone != nil {
		select {
		case <-turnDone:
		case <-time.After(closeGrace):
			// The agent ignored EOF. Kill it and let the reader finish.
			killErr := proc.kill()
			select {
			case <-turnDone:
			case <-time.After(closeGrace):
				// The reader is wedged; leave the child to the OS rather than
				// calling Wait underneath an active read.
			}
			return killErr
		}
		return nil
	}

	proc.reap()
	return nil
}

func truncateLine(s string) string {
	const limit = 200
	if len(s) <= limit {
		return s
	}
	return s[:limit] + "…"
}
