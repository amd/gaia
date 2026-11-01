package client

import (
	"context"
	"net/http"
	"testing"
	"time"
)

// A one-shot abandons its turn at the deadline and exits straight after
// Close: the relay must have been told to drop the run by the time Close
// returns, or the sidecar keeps working for nobody.
func TestSSEClientCloseDeliversTheAbandonedRunsCancel(t *testing.T) {
	f := newFakeRelay(t)
	f.cancelDelay = 300 * time.Millisecond
	release := make(chan struct{})
	t.Cleanup(func() { close(release) })
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"thinking"}`)
		flush()
		<-release
	}

	c := f.client(t)
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	ch, err := c.Send(ctx, "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	<-ctx.Done()
	// The one-shot stops reading at its deadline; it does not drain.
	_ = ch

	if err := c.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	f.mu.Lock()
	got := append([]string(nil), f.cancelled...)
	f.mu.Unlock()
	if len(got) != 1 || got[0] != f.lastQuery().RunID {
		t.Fatalf("cancels delivered before Close returned = %v, want [%s]", got, f.lastQuery().RunID)
	}
}

// Closing with the run still live (no deadline yet) is the same exit and must
// deliver the same cancel.
func TestSSEClientCloseMidRunDeliversItsCancel(t *testing.T) {
	f := newFakeRelay(t)
	f.cancelDelay = 300 * time.Millisecond
	started := make(chan struct{})
	release := make(chan struct{})
	t.Cleanup(func() { close(release) })
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"thinking"}`)
		flush()
		close(started)
		<-release
	}

	c := f.client(t)
	if _, err := c.Send(context.Background(), "triage"); err != nil {
		t.Fatalf("Send: %v", err)
	}
	<-started
	if err := c.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	f.mu.Lock()
	n := len(f.cancelled)
	f.mu.Unlock()
	if n != 1 {
		t.Fatalf("cancels delivered before Close returned = %d, want 1", n)
	}
	// Idempotent: a second Close has nothing left to wait on.
	start := time.Now()
	if err := c.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	if d := time.Since(start); d > time.Second {
		t.Fatalf("second Close waited %s", d)
	}
}

// A run that ended on its own leaves nothing pending, so Close is immediate.
func TestSSEClientCloseAfterACompletedRunDoesNotWait(t *testing.T) {
	f := newFakeRelay(t)
	f.cancelDelay = 5 * time.Second
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"done"}`)
		flush()
	}
	c := f.client(t)
	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	collect(t, ch)
	start := time.Now()
	if err := c.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if d := time.Since(start); d > time.Second {
		t.Fatalf("Close after a completed run waited %s", d)
	}
}
