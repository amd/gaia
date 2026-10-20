// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"bytes"
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
)

// syncBuffer is read by the test while the turn writes it.
type syncBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

// stoppableAgent is a turn that stays open until something stops it.
type stoppableAgent struct {
	events []interface{}
	// honourCancel ends the turn with a "stopped" final when Cancel lands.
	honourCancel bool
	// cancelErr fails Cancel outright, as an unreachable daemon does.
	cancelErr error
	// cancelHangs makes Cancel wait out its context, as a daemon that never
	// answers does.
	cancelHangs bool
	// sendBlocks holds Send until its context ends, as a slow dial does.
	sendBlocks bool

	sendCalls   atomic.Int32
	cancelCalls atomic.Int32
	// cancelHadDeadline records whether Cancel was given a bound.
	cancelHadDeadline atomic.Bool
	sendCtx           atomic.Value

	mu       sync.Mutex
	cancelCh chan struct{}
}

func (a *stoppableAgent) Send(ctx context.Context, _ string) (<-chan interface{}, error) {
	a.sendCalls.Add(1)
	a.sendCtx.Store(ctx)
	if a.sendBlocks {
		<-ctx.Done()
		return nil, ctx.Err()
	}
	a.mu.Lock()
	a.cancelCh = make(chan struct{})
	cancelCh := a.cancelCh
	a.mu.Unlock()

	ch := make(chan interface{}, len(a.events)+1)
	for _, e := range a.events {
		ch <- e
	}
	go func() {
		defer close(ch)
		select {
		case <-ctx.Done():
		case <-cancelCh:
			ch <- event.CanonicalFinalEvent{Type: "final", Answer: "stopped"}
		}
	}()
	return ch, nil
}

func (a *stoppableAgent) Cancel(ctx context.Context) error {
	a.cancelCalls.Add(1)
	_, has := ctx.Deadline()
	a.cancelHadDeadline.Store(has)
	if a.cancelHangs {
		<-ctx.Done()
		return ctx.Err()
	}
	if a.cancelErr != nil {
		return a.cancelErr
	}
	if a.honourCancel {
		a.mu.Lock()
		if a.cancelCh != nil {
			close(a.cancelCh)
			a.cancelCh = nil
		}
		a.mu.Unlock()
	}
	return nil
}

func (a *stoppableAgent) Close() error { return nil }

// tornDown reports whether the turn's context was cancelled — what kills a
// local agent and drops a remote run's stream.
func (a *stoppableAgent) tornDown() bool {
	ctx, ok := a.sendCtx.Load().(context.Context)
	return ok && ctx.Err() != nil
}

// localAgent is the same turn, run by a child this process can kill.
type localAgent struct{ *stoppableAgent }

func (localAgent) AbortStopsAgent() bool { return true }

var (
	_ client.AgentCanceler     = (*stoppableAgent)(nil)
	_ client.LocalAgentStopper = localAgent{}
)

type interruptRun struct {
	sigs     chan os.Signal
	released atomic.Bool
	out, err *syncBuffer
	done     chan OneShotResult
	started  time.Time
}

// startInterruptible runs a one-shot against c with an injected signal feed.
func startInterruptible(t *testing.T, c client.AgentClient, grace time.Duration, preSignal bool) *interruptRun {
	t.Helper()
	r := &interruptRun{
		sigs: make(chan os.Signal, 2),
		out:  &syncBuffer{},
		err:  &syncBuffer{},
		done: make(chan OneShotResult, 1),
	}
	if preSignal {
		r.sigs <- os.Interrupt
	}
	r.started = time.Now()
	go func() {
		r.done <- runOneShotInterruptible(context.Background(), c, "q", r.out, r.err, false, nil,
			interruptOptions{
				sigs:        r.sigs,
				cancelGrace: grace,
				abandonWait: 500 * time.Millisecond,
				release:     func() { r.released.Store(true) },
			})
	}()
	return r
}

// waitStderr blocks until the turn has printed want, so the stop lands mid-turn.
func (r *interruptRun) waitStderr(t *testing.T, want string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !strings.Contains(r.err.String(), want) {
		if time.Now().After(deadline) {
			t.Fatalf("the turn never printed %q:\n%s", want, r.err.String())
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func (r *interruptRun) result(t *testing.T, within time.Duration) OneShotResult {
	t.Helper()
	select {
	case res := <-r.done:
		return res
	case <-time.After(within):
		t.Fatalf("the one-shot did not exit within %s of the stop:\n%s", within, r.err.String())
		return OneShotResult{}
	}
}

var toolCall = event.CanonicalToolCallEvent{Type: "tool_call", Tool: "bash_execute"}

// Ctrl+C mid-tool-call asks the agent to stop, the way Esc does, and exits 130
// once the agent says the turn is over.
func TestCtrlCMidToolCallStopsTheTurnThroughCancel(t *testing.T) {
	agent := &stoppableAgent{events: []interface{}{toolCall}, honourCancel: true}
	r := startInterruptible(t, agent, 5*time.Second, false)
	r.waitStderr(t, "bash_execute")

	r.sigs <- os.Interrupt
	res := r.result(t, 2*time.Second)

	if res.ExitCode != ExitInterrupted || !res.Interrupted {
		t.Fatalf("exit = %d interrupted=%t, want %d: a stopped turn is not a success or a failure",
			res.ExitCode, res.Interrupted, ExitInterrupted)
	}
	if got := agent.cancelCalls.Load(); got != 1 {
		t.Errorf("Cancel called %d times, want 1: the stop must reach the agent", got)
	}
	if !agent.cancelHadDeadline.Load() {
		t.Error("Cancel was given no deadline, so a silent agent could hold the exit forever")
	}
	if strings.TrimSpace(r.out.String()) != "stopped" {
		t.Errorf("stdout = %q, want the agent's own answer to the stop", r.out.String())
	}
	if res.StillRunning {
		t.Error("a confirmed stop was reported as possibly still running")
	}
	if !strings.Contains(r.err.String(), "The agent ended the turn") {
		t.Errorf("stderr does not confirm the stop:\n%s", r.err.String())
	}
}

// A stop pressed before the turn starts must not send the query at all.
func TestCtrlCBeforeTheTurnStartsSendsNothing(t *testing.T) {
	agent := &stoppableAgent{honourCancel: true}
	r := startInterruptible(t, agent, 5*time.Second, true)
	res := r.result(t, 2*time.Second)

	if got := agent.sendCalls.Load(); got != 0 {
		t.Fatalf("Send called %d times after the stop: the query reached the agent anyway", got)
	}
	if res.ExitCode != ExitInterrupted {
		t.Errorf("exit = %d, want %d", res.ExitCode, ExitInterrupted)
	}
	if res.StillRunning {
		t.Error("nothing was sent, yet the turn was reported as possibly running")
	}
	errW := r.err.String()
	if !strings.Contains(errW, "never received it") {
		t.Errorf("stderr does not say the query was never sent:\n%s", errW)
	}
	if strings.Count(errW, "never received it") != 1 {
		t.Errorf("the same stop was reported more than once:\n%s", errW)
	}
}

// A stop while the query is still in flight to a remote agent cannot know
// whether the run started, and must say so rather than claim it stopped.
func TestCtrlCWhileTheQueryIsInFlightIsNotClaimedStopped(t *testing.T) {
	agent := &stoppableAgent{sendBlocks: true}
	r := startInterruptible(t, agent, 5*time.Second, false)
	deadline := time.Now().Add(5 * time.Second)
	for agent.sendCalls.Load() == 0 {
		if time.Now().After(deadline) {
			t.Fatal("Send was never called")
		}
		time.Sleep(5 * time.Millisecond)
	}

	r.sigs <- os.Interrupt
	res := r.result(t, 2*time.Second)

	if !agent.tornDown() {
		t.Error("the in-flight send was not torn down")
	}
	if agent.cancelCalls.Load() != 0 {
		t.Error("Cancel was asked of a run the client never registered")
	}
	if res.ExitCode != ExitInterrupted || !res.StillRunning {
		t.Fatalf("exit=%d stillRunning=%t, want %d and true", res.ExitCode, res.StillRunning, ExitInterrupted)
	}
	if !strings.Contains(r.err.String(), "may still be running") {
		t.Errorf("stderr does not warn the turn may still be running:\n%s", r.err.String())
	}
}

// A stop while the run is parked on an approval stops it the same way, and
// the exit code is the stop's, not the approval gate's.
func TestCtrlCDuringAnApprovalPromptStopsTheTurn(t *testing.T) {
	agent := &stoppableAgent{
		events: []interface{}{
			toolCall,
			event.CanonicalNeedsConfirmationEvent{Type: "needs_confirmation", Action: "send_email", Summary: "to boss"},
		},
		honourCancel: true,
	}
	r := startInterruptible(t, agent, 5*time.Second, false)
	r.waitStderr(t, "confirmation needed: send_email")

	r.sigs <- os.Interrupt
	res := r.result(t, 2*time.Second)

	if res.ExitCode != ExitInterrupted {
		t.Fatalf("exit = %d, want %d: the stop decides the code, not the gate (%d)",
			res.ExitCode, ExitInterrupted, ExitApprovalRequired)
	}
	if agent.cancelCalls.Load() != 1 {
		t.Errorf("Cancel called %d times, want 1", agent.cancelCalls.Load())
	}
	errW := r.err.String()
	if strings.Contains(errW, "exit 3") {
		t.Errorf("stderr names the approval exit code the process will not exit with:\n%s", errW)
	}
	if !strings.Contains(errW, "send_email") || !strings.Contains(errW, "NOT performed") {
		t.Errorf("stderr no longer says the gated action was not performed:\n%s", errW)
	}
}

// The second Ctrl+C quits without waiting for an agent that has not stopped,
// restores the default disposition, and says the turn may still be running.
func TestSecondCtrlCQuitsWithoutWaiting(t *testing.T) {
	agent := &stoppableAgent{events: []interface{}{toolCall}} // ignores Cancel
	r := startInterruptible(t, agent, time.Minute, false)
	r.waitStderr(t, "bash_execute")

	r.sigs <- os.Interrupt
	r.waitStderr(t, "press Ctrl+C again")
	r.sigs <- os.Interrupt
	res := r.result(t, 2*time.Second)

	if !r.released.Load() {
		t.Error("the default signal disposition was not restored, so a wedged exit cannot be killed")
	}
	if res.ExitCode != ExitInterrupted || !res.StillRunning {
		t.Fatalf("exit=%d stillRunning=%t, want %d and true", res.ExitCode, res.StillRunning, ExitInterrupted)
	}
	if !agent.tornDown() {
		t.Error("the forced quit left the turn's read running")
	}
	if !strings.Contains(r.err.String(), "you quit without waiting") {
		t.Errorf("stderr does not say why the stop is unconfirmed:\n%s", r.err.String())
	}
}

// A daemon that never answers the stop request must not hold the exit: the
// run gives up in bounded time and says the turn may still be running.
func TestADaemonThatNeverAnswersTheStopStillExitsInBoundedTime(t *testing.T) {
	agent := &stoppableAgent{events: []interface{}{toolCall}, cancelHangs: true}
	const grace = 200 * time.Millisecond
	r := startInterruptible(t, agent, grace, false)
	r.waitStderr(t, "bash_execute")

	r.sigs <- os.Interrupt
	stopAt := time.Now()
	res := r.result(t, 3*time.Second)

	if took := time.Since(stopAt); took > grace+time.Second {
		t.Errorf("exit took %s after the stop, want about the %s grace", took, grace)
	}
	if res.ExitCode != ExitInterrupted || !res.StillRunning {
		t.Fatalf("exit=%d stillRunning=%t, want %d and true", res.ExitCode, res.StillRunning, ExitInterrupted)
	}
	errW := r.err.String()
	for _, want := range []string{"may still be running", "did not confirm it stopped", "gaia daemon status"} {
		if !strings.Contains(errW, want) {
			t.Errorf("stderr is missing %q:\n%s", want, errW)
		}
	}
}

// A stop request that fails outright escalates at once, naming the failure,
// instead of waiting out the grace for an answer that cannot come.
func TestAStopRequestThatFailsIsReportedAndEscalates(t *testing.T) {
	agent := &stoppableAgent{
		events:    []interface{}{toolCall},
		cancelErr: errors.New("connection refused"),
	}
	r := startInterruptible(t, agent, time.Minute, false)
	r.waitStderr(t, "bash_execute")

	r.sigs <- os.Interrupt
	res := r.result(t, 2*time.Second)

	if !res.StillRunning || res.ExitCode != ExitInterrupted {
		t.Fatalf("exit=%d stillRunning=%t, want %d and true", res.ExitCode, res.StillRunning, ExitInterrupted)
	}
	if !strings.Contains(r.err.String(), "connection refused") {
		t.Errorf("stderr swallowed why the stop failed:\n%s", r.err.String())
	}
}

// An agent this process runs and that ignores the stop is torn down after the
// grace — its process is killed — rather than reported as maybe running.
func TestALocalAgentThatIgnoresTheStopIsKilled(t *testing.T) {
	agent := localAgent{&stoppableAgent{events: []interface{}{toolCall}}}
	r := startInterruptible(t, agent, 200*time.Millisecond, false)
	r.waitStderr(t, "bash_execute")

	r.sigs <- os.Interrupt
	res := r.result(t, 3*time.Second)

	if !agent.tornDown() {
		t.Fatal("the turn was never torn down, so the agent process is never killed")
	}
	if res.ExitCode != ExitInterrupted {
		t.Errorf("exit = %d, want %d", res.ExitCode, ExitInterrupted)
	}
	if res.StillRunning {
		t.Error("a local agent being killed was reported as maybe still running")
	}
	if !strings.Contains(r.err.String(), "Stopping the agent process") {
		t.Errorf("stderr does not say the agent process is being stopped:\n%s", r.err.String())
	}
}

// Wiring the stop in must not change a turn nobody interrupted.
func TestAnUninterruptedTurnIsUnchanged(t *testing.T) {
	r := startInterruptible(t, &scriptedClient{events: []interface{}{
		event.CanonicalFinalEvent{Type: "final", Answer: "done"},
	}}, time.Second, false)
	res := r.result(t, 2*time.Second)

	if res.ExitCode != 0 || res.Interrupted {
		t.Fatalf("exit=%d interrupted=%t, want 0 and false", res.ExitCode, res.Interrupted)
	}
	if strings.TrimSpace(r.out.String()) != "done" {
		t.Errorf("stdout = %q, want the answer", r.out.String())
	}
}

// SIGTERM or SIGHUP has no Ctrl+C behind it — after SIGHUP no terminal at all —
// so the hint must not tell anyone to press it.
func TestAStopFromSIGTERMDoesNotSayPressCtrlC(t *testing.T) {
	agent := &stoppableAgent{events: []interface{}{toolCall}, honourCancel: true}
	r := startInterruptible(t, agent, 5*time.Second, false)
	r.waitStderr(t, "bash_execute")

	r.sigs <- syscall.SIGTERM
	res := r.result(t, 2*time.Second)

	if res.ExitCode != ExitInterrupted {
		t.Fatalf("exit = %d, want %d", res.ExitCode, ExitInterrupted)
	}
	if errW := r.err.String(); strings.Contains(errW, "Ctrl+C") {
		t.Errorf("a SIGTERM stop told the user to press Ctrl+C:\n%s", errW)
	}
}
