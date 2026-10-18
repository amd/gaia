// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// moveDaemon brings up a second listener for the same fake, points
// instance.json at it with a new token, and stops the first one accepting
// connections: a daemon restart that landed on a new port. A stream already
// open on the old listener keeps running.
func (f *fakeRelay) moveDaemon(token string) {
	f.t.Helper()
	old := f.srv
	f.mu.Lock()
	f.token = token
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

func TestCancelReachesADaemonThatRestartedOnANewPort(t *testing.T) {
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

	if err := c.Cancel(context.Background()); err != nil {
		t.Fatalf("Cancel after a daemon restart on a new port: %v", err)
	}
	f.mu.Lock()
	cancelled := append([]string(nil), f.cancelled...)
	f.mu.Unlock()
	if len(cancelled) != 1 || cancelled[0] != f.lastQuery().RunID {
		t.Fatalf("cancelled runs = %v, want the active run %q", cancelled, f.lastQuery().RunID)
	}

	// Later calls go straight to the new daemon instead of rediscovering it again.
	c.mu.Lock()
	port := c.inst.Port
	c.mu.Unlock()
	if port != f.port() {
		t.Errorf("client still holds port %d, want the restarted daemon's %d", port, f.port())
	}
}
