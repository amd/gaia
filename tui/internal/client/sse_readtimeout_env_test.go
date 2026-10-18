// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestAgentReadTimeoutFollowsTheRelaySetting(t *testing.T) {
	for _, tc := range []struct {
		env  string
		want time.Duration
	}{
		{"", 300 * time.Second},
		{"100", 300 * time.Second},                            // never shorter than the default
		{"600", 660 * time.Second},                            // plus the relay's grace for the tool's own error
		{"inf", maxAgentReadTimeout + agentToolTimeoutGrace},  // timeouts.py reads this as no limit
		{"1e30", maxAgentReadTimeout + agentToolTimeoutGrace}, // clamped, never wrapped to a short timeout
	} {
		t.Setenv("GAIA_AGENT_TOOL_TIMEOUT", tc.env)
		got, err := agentReadTimeout()
		if err != nil || got != tc.want {
			t.Errorf("GAIA_AGENT_TOOL_TIMEOUT=%q: got %v, %v; want %v", tc.env, got, err, tc.want)
		}
	}
}

func TestAnInvalidAgentTimeoutFailsTheTurnLoudly(t *testing.T) {
	newFakeRelay(t)
	t.Setenv("GAIA_AGENT_TOOL_TIMEOUT", "soon")
	c := timeoutClient(t, SSEOptions{})

	_, err := c.Send(context.Background(), "triage")
	if err == nil || !strings.Contains(err.Error(), "GAIA_AGENT_TOOL_TIMEOUT") {
		t.Fatalf("expected an error naming the bad setting, got %v", err)
	}
}
