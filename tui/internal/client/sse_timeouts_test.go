// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
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

// The relay gives the sidecar the read budget to start answering, then reports
// why it did not. The TUI must wait long enough to show that report instead of
// cutting in with a bare header timeout of its own.
func TestRelayTimeoutReportReachesTheUserInsteadOfAHeaderTimeout(t *testing.T) {
	f := newFakeRelay(t)
	f.queryDelay = 600 * time.Millisecond
	f.queryStatus = http.StatusBadGateway
	f.queryBody = `{"detail":"sidecar for agent 'email' did not answer (ReadTimeout). Re-ensure it and retry."}`

	c := timeoutClient(t, SSEOptions{ReadTimeout: 400 * time.Millisecond})

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
