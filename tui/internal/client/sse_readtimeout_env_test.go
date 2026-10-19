// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The table is generated from, and checked against, src/gaia/daemon/timeouts.py
// (tests/unit/test_agent_read_timeout_table.py), so the two parsers cannot drift.
func TestAgentReadTimeoutMatchesThePythonTable(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "daemon", "agent_read_timeout_cases.json"))
	if err != nil {
		t.Fatalf("read the shared table: %v", err)
	}
	var table struct {
		Cases []struct {
			Value   string `json:"value"`
			Seconds any    `json:"seconds"`
			Error   bool   `json:"error"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatalf("parse the shared table: %v", err)
	}
	limit := maxAgentReadTimeout + agentToolTimeoutGrace
	for _, tc := range table.Cases {
		t.Setenv("GAIA_AGENT_TOOL_TIMEOUT", tc.Value)
		got, err := agentReadTimeout()
		if tc.Error {
			if err == nil {
				t.Errorf("%q: Python rejects it, Go returned %v", tc.Value, got)
			}
			continue
		}
		if err != nil {
			t.Errorf("%q: Python accepts it, Go failed: %v", tc.Value, err)
			continue
		}
		// Past a century the duration is capped; Python's float has no such limit.
		want := limit
		if secs, ok := tc.Seconds.(float64); ok && time.Duration(secs*float64(time.Second)) < limit && secs < limit.Seconds() {
			want = time.Duration(secs * float64(time.Second))
		}
		if got != want {
			t.Errorf("%q: got %v, Python gives %v", tc.Value, got, tc.Seconds)
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
