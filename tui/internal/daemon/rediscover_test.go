// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package daemon

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
)

// restartOnNewPort stops the fake daemon and brings it back on a different port
// with a new token, rewriting instance.json the way a real daemon restart does.
func (f *fakeDaemon) restartOnNewPort(token string) {
	f.t.Helper()
	oldPort := f.port()
	f.srv.Close()
	f.mu.Lock()
	f.token = token
	f.mu.Unlock()
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	f.t.Cleanup(f.srv.Close)
	if f.port() == oldPort {
		f.t.Fatalf("restarted fake daemon reused port %d", oldPort)
	}
	f.writeInstance(nil)
}

func portOf(t *testing.T, raw string) int {
	t.Helper()
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatalf("parse %s: %v", raw, err)
	}
	p, err := strconv.Atoi(u.Port())
	if err != nil {
		t.Fatalf("parse port of %s: %v", raw, err)
	}
	return p
}

func TestDoFollowsADaemonRestartedOnANewPort(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}

	// The caller still holds the old instance: a Cancel or Respond sent now
	// would dial a port nothing listens on.
	f.restartOnNewPort("token-B")

	resp, fresh, err := c.Do(context.Background(), inst, Request{
		Method: http.MethodPost,
		Path:   APIPrefix + "/status",
		Op:     "cancel the 'gaia' run",
	})
	if err != nil {
		t.Fatalf("Do after a daemon restart: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200 from the restarted daemon", resp.StatusCode)
	}
	if fresh.Port != f.port() || fresh.Token != "token-B" {
		t.Errorf("returned instance = port %d token %q, want the restarted daemon's (port %d, token-B)",
			fresh.Port, fresh.Token, f.port())
	}
}

func TestDoReportsTheRefusedConnectionWhenNoNewDaemonIsRecorded(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	// The daemon died and nothing replaced it: instance.json still names it.
	f.srv.Close()

	_, got, err := c.Do(context.Background(), inst, Request{
		Method: http.MethodPost,
		Path:   APIPrefix + "/status",
		Op:     "cancel the 'gaia' run",
	})
	if err == nil {
		t.Fatal("expected an error when the daemon is gone")
	}
	if got != inst {
		t.Errorf("Do returned a different instance although none was found")
	}
	msg := err.Error()
	if !strings.Contains(msg, "cancel the 'gaia' run") || !strings.Contains(msg, "gaia daemon status") {
		t.Errorf("error must name the operation and the next step: %v", err)
	}
}

// A request that reached the daemon and then lost its connection may already
// have run there, so it is never replayed anywhere, even if a new daemon is
// recorded by then.
func TestDoNeverReplaysARequestThatReachedTheDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}

	var reached atomic.Int32
	dropper := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached.Add(1)
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Errorf("hijack: %v", err)
			return
		}
		conn.Close()
	}))
	t.Cleanup(dropper.Close)
	dropping := *inst
	dropping.Port = portOf(t, dropper.URL)

	f.restartOnNewPort("token-B")

	_, _, err = c.Do(context.Background(), &dropping, Request{
		Method: http.MethodPost,
		Path:   APIPrefix + "/status",
		Op:     "cancel the 'gaia' run",
	})
	if err == nil {
		t.Fatal("expected the dropped connection to surface")
	}
	if n := reached.Load(); n != 1 {
		t.Errorf("the dropping daemon saw %d requests, want exactly 1", n)
	}
	if f.sawPath("POST " + APIPrefix + "/status") {
		t.Error("a request that already reached a daemon was replayed to the new one")
	}
}

// instance.json rewritten to a daemon that is not answering either: both the
// original failure and the reason the replacement was rejected reach the user.
func TestDoReportsBothFailuresWhenTheRecordedReplacementIsDead(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}

	dead := httptest.NewServer(http.NotFoundHandler())
	deadPort := portOf(t, dead.URL)
	dead.Close()
	f.srv.Close()
	f.writeInstance(func(i *Instance) {
		i.Port = deadPort
		i.Token = "token-B"
	})

	_, got, err := c.Do(context.Background(), inst, Request{
		Method: http.MethodPost,
		Path:   APIPrefix + "/status",
		Op:     "cancel the 'gaia' run",
	})
	if err == nil {
		t.Fatal("expected an error when the recorded replacement is dead too")
	}
	if got != inst {
		t.Errorf("Do returned the unusable replacement instead of the caller's instance")
	}
	msg := err.Error()
	if !strings.Contains(msg, "cancel the 'gaia' run") {
		t.Errorf("error must keep the original operation: %v", err)
	}
	if !strings.Contains(msg, "instance.json was rewritten, but the daemon it names is not usable either") {
		t.Errorf("error must add why the replacement was rejected: %v", err)
	}
}
