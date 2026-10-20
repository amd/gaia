// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"syscall"
	"time"

	"github.com/amd/gaia/tui/internal/client"
)

// ExitInterrupted is the exit code for a one-shot stopped by Ctrl+C, SIGTERM or
// SIGHUP: 128+SIGINT, what a shell reports for an interrupted command.
const ExitInterrupted = 130

const (
	// interruptCancelGrace bounds the wait for the agent to confirm a
	// cooperative stop. It matches the relay's own cancel timeout.
	interruptCancelGrace = 10 * time.Second
	// interruptAbandonWait bounds the wait for the turn's read to unwind once
	// it has been torn down.
	interruptAbandonWait = 3 * time.Second
)

// interruptSignals stop a one-shot. SIGHUP is the terminal window closing;
// on Windows it never fires and costs nothing to ask for.
var interruptSignals = []os.Signal{os.Interrupt, syscall.SIGTERM, syscall.SIGHUP}

// interruptOptions wires a one-shot to the signals that stop it.
type interruptOptions struct {
	// sigs delivers the stop signals; injected so tests need no real signal.
	sigs <-chan os.Signal
	// cancelGrace bounds the wait for a cooperative stop to be confirmed.
	cancelGrace time.Duration
	// abandonWait bounds the wait for a torn-down turn to unwind.
	abandonWait time.Duration
	// release restores the default signal disposition, so the press after the
	// forcing one still kills a process whose cleanup is wedged.
	release func()
}

// sendState is how far the turn's query got when the stop arrived.
type sendState int

const (
	notSent sendState = iota
	sending
	sent
)

var errInterruptedBeforeSend = errors.New(
	"stopped before the query was sent — the agent never received it")

// sendTracker records whether the query reached the agent, so a stop knows
// whether there is a run to cancel, and refuses a Send that starts after it.
type sendTracker struct {
	client.AgentClient
	mu          sync.Mutex
	state       sendState
	interrupted bool
}

func (t *sendTracker) Send(ctx context.Context, query string) (<-chan interface{}, error) {
	t.mu.Lock()
	if t.interrupted {
		t.mu.Unlock()
		return nil, errInterruptedBeforeSend
	}
	t.state = sending
	t.mu.Unlock()

	ch, err := t.AgentClient.Send(ctx, query)

	t.mu.Lock()
	if err == nil {
		t.state = sent
	}
	t.mu.Unlock()
	return ch, err
}

// interrupt marks the turn stopped and reports how far its query had got.
func (t *sendTracker) interrupt() sendState {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.interrupted = true
	return t.state
}

// stopped reports whether c is a turn a signal has stopped.
func stopped(c client.AgentClient) bool {
	t, ok := c.(*sendTracker)
	if !ok {
		return false
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.interrupted
}

// sealableWriter serialises writes from the turn's goroutine and the stop
// handler, and drops the tail of an abandoned turn once the result is out.
type sealableWriter struct {
	mu     *sync.Mutex
	sealed *bool
	w      io.Writer
}

func (s sealableWriter) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if *s.sealed {
		return len(p), nil
	}
	return s.w.Write(p)
}

// runOneShotInterruptible is RunOneShot with Ctrl+C wired to the same stop the
// chat view uses: the first signal asks the agent to stop the turn and waits
// up to cancelGrace for it to settle, escalating to tearing the turn down if it
// does not; a second signal stops waiting at once. Either way the result
// carries ExitInterrupted and says whether the stop was confirmed.
func runOneShotInterruptible(
	ctx context.Context,
	c client.AgentClient,
	query string,
	out, errW io.Writer,
	dev bool,
	debugf func(format string, args ...any),
	opts interruptOptions,
) OneShotResult {
	turnCtx, abandon := context.WithCancel(ctx)
	defer abandon()

	var (
		mu     sync.Mutex
		sealed bool
	)
	sOut := sealableWriter{&mu, &sealed, out}
	sErr := sealableWriter{&mu, &sealed, errW}
	seal := func() {
		mu.Lock()
		sealed = true
		mu.Unlock()
	}

	tracker := &sendTracker{AgentClient: c}
	done := make(chan OneShotResult, 1)

	// A stop already pending must win over starting the turn at all.
	var sig os.Signal
	select {
	case sig = <-opts.sigs:
		tracker.interrupt()
	default:
	}
	go func() { done <- RunOneShot(turnCtx, tracker, query, sOut, sErr, dev, debugf) }()

	if sig == nil {
		select {
		case res := <-done:
			return res
		case sig = <-opts.sigs:
		}
	}

	state := tracker.interrupt()
	local := false
	if s, ok := c.(client.LocalAgentStopper); ok {
		local = s.AbortStopsAgent()
	}
	canceler, cooperative := c.(client.AgentCanceler)
	cooperative = cooperative && state == sent

	fmt.Fprintf(sErr, "\n⏹  Stopping the turn (%v) — press Ctrl+C again to quit without waiting\n", sig)

	var (
		escalated bool
		reason    string
		cancelRes chan error
	)
	if cooperative {
		cancelRes = make(chan error, 1)
		go func() {
			cctx, cancel := context.WithTimeout(context.Background(), opts.cancelGrace)
			defer cancel()
			cancelRes <- canceler.Cancel(cctx)
		}()
	} else {
		// No run to ask yet, or no way to ask: tear the turn down directly.
		reason = "this connection has no way to ask the agent to stop"
		abandon()
		escalated = true
	}

	finish := func(res OneShotResult, settled bool) OneShotResult {
		res.ExitCode = ExitInterrupted
		res.Interrupted = true
		switch {
		case state == notSent:
			res.ErrorDetail = errInterruptedBeforeSend.Error()
			fmt.Fprintln(sErr, "⏹  Stopped before the query was sent — the agent never received it.")
		case settled && !escalated:
			res.ErrorDetail = "stopped: the agent ended the turn"
			fmt.Fprintln(sErr, "⏹  Stopped. The agent ended the turn.")
		case local:
			res.ErrorDetail = "stopped: the agent process is being killed"
			why := "the query was still being sent"
			if state == sent {
				why = reason
			}
			fmt.Fprintf(sErr, "⏹  Stopping the agent process (%s).\n", why)
		default:
			res.StillRunning = true
			why := "the query was already on its way to the agent"
			if state == sent {
				why = reason
			}
			res.ErrorDetail = "interrupted; the turn may still be running"
			fmt.Fprintf(sErr,
				"⚠️  Interrupted, but %s, so the turn may still be running in the GAIA "+
					"background service. Check `gaia daemon status` and `gaia daemon logs`.\n", why)
		}
		seal()
		return res
	}

	timer := time.NewTimer(opts.cancelGrace)
	defer timer.Stop()
	for {
		select {
		case res := <-done:
			if turnCtx.Err() != nil && !escalated {
				// The --timeout bound tore the turn down, not the agent's own stop.
				reason = "the --timeout bound ran out before the agent confirmed it stopped"
				escalated = true
			}
			return finish(res, true)

		case err := <-cancelRes:
			cancelRes = nil
			if err != nil {
				reason = fmt.Sprintf("the stop request could not be delivered: %v", err)
				if errors.Is(err, context.DeadlineExceeded) {
					reason = fmt.Sprintf("the agent did not confirm it stopped within %s", opts.cancelGrace)
				}
				abandon()
				escalated = true
				return finish(waitUnwind(done, opts.abandonWait), false)
			}

		case <-timer.C:
			reason = fmt.Sprintf("the agent did not confirm it stopped within %s", opts.cancelGrace)
			abandon()
			escalated = true
			return finish(waitUnwind(done, opts.abandonWait), false)

		case <-opts.sigs:
			if opts.release != nil {
				opts.release()
			}
			reason = "you quit without waiting for the agent to confirm it stopped"
			abandon()
			escalated = true
			return finish(waitUnwind(done, opts.abandonWait), false)
		}
	}
}

// waitUnwind gives a torn-down turn a bounded moment to report what it had,
// and returns an empty result when it does not.
func waitUnwind(done <-chan OneShotResult, wait time.Duration) OneShotResult {
	select {
	case res := <-done:
		return res
	case <-time.After(wait):
		return OneShotResult{}
	}
}
