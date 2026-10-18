// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"fmt"
	"net/http"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

func timeoutClient(t *testing.T, opts SSEOptions) *SSEClient {
	t.Helper()
	dc := daemon.New(daemon.Options{
		ProbeTimeout:  2 * time.Second,
		EnsureTimeout: 5 * time.Second,
		StartCommand: func(context.Context) (*exec.Cmd, error) {
			return nil, &daemon.StartError{Reason: "the test daemon must already be attachable"}
		},
		Logf: t.Logf,
	})
	c := NewSSEClient("email", dc, opts)
	t.Cleanup(func() { c.Close() })
	return c
}

// The relay gives the sidecar its read budget to start answering (httpx's read
// timeout covers the wait for headers), then reports why it did not. The TUI
// must wait long enough to show that report instead of cutting in first.
func TestRelayTimeoutReportReachesTheUserInsteadOfAHeaderTimeout(t *testing.T) {
	f := newFakeRelay(t)
	f.statusExtra = `,"agent_read_timeout_s":0.4`
	f.queryDelay = 400*time.Millisecond + 50*time.Millisecond
	f.queryStatus = http.StatusBadGateway
	f.queryBody = `{"detail":"sidecar for agent 'email' did not answer (ReadTimeout). Re-ensure it and retry."}`

	c := timeoutClient(t, SSEOptions{})

	_, err := c.Send(context.Background(), "triage")
	if err == nil {
		t.Fatal("expected the relay's 502 to surface")
	}
	if strings.Contains(err.Error(), "awaiting response headers") {
		t.Fatalf("the TUI cut in with its own header timeout: %v", err)
	}
	if !strings.Contains(err.Error(), "did not answer (ReadTimeout)") {
		t.Errorf("error must carry the relay's reason: %v", err)
	}
}

// The relay runs with the daemon's environment, not the TUI's shell, so the
// budget the daemon reports wins over whatever this process has set.
func TestTheTUIUsesTheBudgetTheDaemonReports(t *testing.T) {
	f := newFakeRelay(t)
	t.Setenv("GAIA_AGENT_TOOL_TIMEOUT", "")
	f.statusExtra = `,"agent_read_timeout_s":0.4`
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"thinking"}`)
		flush()
		time.Sleep(2 * time.Second) // silent well past the daemon's budget
	}
	c := timeoutClient(t, SSEOptions{})

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	done := make(chan []interface{})
	go func() { done <- collect(t, ch) }()
	select {
	case events := <-done:
		last := fmt.Sprintf("%+v", events[len(events)-1])
		if !strings.Contains(last, "sent nothing for 400ms") {
			t.Errorf("the turn should end on the daemon's 400ms budget, got %s", last)
		}
	case <-time.After(1500 * time.Millisecond):
		t.Fatal("the turn kept waiting on this shell's 300 s default instead of the daemon's budget")
	}
}

func TestADaemonWithAnUnusableBudgetFailsTheTurnLoudly(t *testing.T) {
	f := newFakeRelay(t)
	f.statusExtra = `,"agent_read_timeout_error":"GAIA_AGENT_TOOL_TIMEOUT must be a positive number of seconds, got 'soon'."`
	c := timeoutClient(t, SSEOptions{})

	_, err := c.Send(context.Background(), "triage")
	if err == nil || !strings.Contains(err.Error(), "GAIA_AGENT_TOOL_TIMEOUT") {
		t.Fatalf("expected an error naming the daemon's bad setting, got %v", err)
	}
}
