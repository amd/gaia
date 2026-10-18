// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// moveDaemon brings up a second listener for the same fake, points
// instance.json at it with a new token, and stops the first one accepting
// connections: a daemon restart that landed on a new port. Like the real one,
// the restarted daemon has no sidecar until the agent is ensured again. A
// stream already open on the old listener keeps running, which is how the
// client still holds a run to cancel.
func (f *fakeRelay) moveDaemon(token string) {
	f.t.Helper()
	old := f.srv
	f.mu.Lock()
	f.token = token
	f.sidecarGone = true
	f.mu.Unlock()
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	f.t.Cleanup(f.srv.Close)
	f.writeInstance(token)
	if err := old.Listener.Close(); err != nil {
		f.t.Fatalf("close the old listener: %v", err)
	}
	// A dead daemon takes its idle keep-alive connections with it.
	http.DefaultTransport.(*http.Transport).CloseIdleConnections()
}

func TestCancelAfterADaemonRestartSaysTheRunEnded(t *testing.T) {
	f := newFakeRelay(t)
	started := make(chan struct{})
	release := make(chan struct{})
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"thinking"}`)
		flush()
		close(started)
		<-release
	}
	defer close(release)

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	if _, ok := <-ch; !ok {
		t.Fatal("expected the status event")
	}
	<-started

	f.moveDaemon("token-B")

	err = c.Cancel(context.Background())
	var restarted *daemon.DaemonRestartedError
	if !errors.As(err, &restarted) {
		t.Fatalf("Cancel after a daemon restart: want DaemonRestartedError, got %v", err)
	}
	if !strings.Contains(err.Error(), "restarted, which ended the agent session this belonged to") {
		t.Errorf("the error must say the run ended with the restart: %v", err)
	}
	f.mu.Lock()
	cancelled := append([]string(nil), f.cancelled...)
	f.mu.Unlock()
	if len(cancelled) != 0 {
		t.Errorf("a cancel was sent to a daemon that never had the run: %v", cancelled)
	}

	// Later calls go straight to the new daemon instead of rediscovering it again.
	c.mu.Lock()
	port := c.inst.Port
	c.mu.Unlock()
	if port != f.port() {
		t.Errorf("client still holds port %d, want the restarted daemon's %d", port, f.port())
	}
}
