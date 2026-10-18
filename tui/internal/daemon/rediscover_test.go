// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package daemon

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
)

// restartOnNewPort stops the fake daemon and brings it back with a new token,
// usually on a new port, rewriting instance.json the way a real daemon restart
// does. The token alone is enough to make it a different daemon to the client.
func (f *fakeDaemon) restartOnNewPort(token string) {
	f.t.Helper()
	f.srv.Close()
	f.mu.Lock()
	f.token = token
	f.mu.Unlock()
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	f.t.Cleanup(f.srv.Close)
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

// The real daemon stops its sidecars on shutdown and the next one never adopts
// them, so a run-scoped call cannot be carried over: it names the restart.
func TestDoDoesNotCarryARunScopedCallToARestartedDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	f.restartOnNewPort("token-B")

	cancelPath := "/v1/email/query/run-1/cancel"
	_, got, err := c.Do(context.Background(), inst, Request{
		Method: http.MethodPost, Path: cancelPath, Op: "cancel the 'email' run", RunScoped: true,
	})
	var restarted *DaemonRestartedError
	if !errors.As(err, &restarted) {
		t.Fatalf("want a DaemonRestartedError, got %v", err)
	}
	if !strings.Contains(err.Error(), "restarted") || !strings.Contains(err.Error(), "cancel the 'email' run") {
		t.Errorf("error must name the restart and the operation: %v", err)
	}
	if f.sawPath("POST " + cancelPath) {
		t.Error("a run-scoped call was sent to a daemon that never had the run")
	}
	if got.Token != "token-B" {
		t.Errorf("the caller should learn the restarted daemon so later calls go there")
	}
}

// A call that is not tied to a run is replayed, after the agent is ensured on
// the restarted daemon, since its sidecar did not survive the restart.
func TestDoEnsuresTheAgentBeforeReplayingARelayCallToARestartedDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	f.restartOnNewPort("token-B")

	resp, _, err := c.Do(context.Background(), inst, Request{Method: http.MethodGet, Path: "/v1/email/memory"})
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	resp.Body.Close()
	if !f.sawPath("POST " + APIPrefix + "/agents/email/ensure") {
		t.Error("the agent was not ensured on the restarted daemon before the replay")
	}
	if !f.sawPath("GET /v1/email/memory") {
		t.Error("the call was not replayed")
	}
}

// A replacement that speaks another contract keeps its typed error, so callers
// that check for a version mismatch still see one.
func TestDoKeepsTheReplacementsVersionErrorInTheChain(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	f.restartOnNewPort("token-B")
	f.writeInstance(func(i *Instance) { i.APIVersion = "9.0" })

	_, _, err = c.Do(context.Background(), inst, Request{Method: http.MethodGet, Path: APIPrefix + "/status"})
	var verr *VersionError
	if !errors.As(err, &verr) {
		t.Fatalf("the replacement's VersionError was dropped from the chain: %v", err)
	}
	var rerr *RequestError
	if !errors.As(err, &rerr) {
		t.Errorf("the original connection failure was dropped from the chain: %v", err)
	}
}

// A daemon that restarts on the same port shows up as a rotated token rather
// than a refused connection. It is still a new daemon with fresh sidecars.
func TestDoDoesNotCarryARunScopedCallAcrossASamePortRestart(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	f.rotateToken("token-B")

	cancelPath := "/v1/email/query/run-1/cancel"
	_, got, err := c.Do(context.Background(), inst, Request{
		Method: http.MethodPost, Path: cancelPath, Op: "cancel the 'email' run", RunScoped: true,
	})
	var restarted *DaemonRestartedError
	if !errors.As(err, &restarted) {
		t.Fatalf("want a DaemonRestartedError after a same-port restart, got %v", err)
	}
	f.mu.Lock()
	var replayed bool
	for i, p := range f.paths {
		if p == "POST "+cancelPath && f.authSeen[i] == AuthScheme+" token-B" {
			replayed = true
		}
	}
	f.mu.Unlock()
	if replayed {
		t.Error("a run-scoped call was replayed to the restarted daemon")
	}
	if got.Token != "token-B" {
		t.Error("the caller should learn the restarted daemon")
	}
}

func TestDoEnsuresTheAgentBeforeReplayingAcrossASamePortRestart(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	f.rotateToken("token-B")

	resp, _, err := c.Do(context.Background(), inst, Request{Method: http.MethodGet, Path: "/v1/email/memory"})
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	resp.Body.Close()
	if !f.sawPath("POST " + APIPrefix + "/agents/email/ensure") {
		t.Error("the agent was not ensured on the restarted daemon before the replay")
	}
}
