// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
)

// claudeTransport is a transport whose child was spawned with --use-claude on
// its argv — what SubprocessClient.ClaudeAtLaunch reports for a real launch.
type claudeTransport struct {
	nullClient
	launchClaude bool
}

func (c *claudeTransport) ClaudeAtLaunch() bool { return c.launchClaude }

// A --use-claude launch shows the header chip from the very first frame: the
// user must be able to tell at a glance that inference is NOT local.
func TestLaunchFlagShowsTheClaudeChipImmediately(t *testing.T) {
	c := &claudeTransport{launchClaude: true}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30

	if !m.claudeMode {
		t.Fatal("--use-claude must be reflected at launch")
	}
	if !strings.Contains(m.View(), "│ claude") {
		t.Error("the header chip must be up from the very first frame")
	}
}

// Without the flag there is no chip — a local session must never look remote.
func TestNoClaudeChipOnALocalLaunch(t *testing.T) {
	m := NewChatModel(&claudeTransport{}, "gaia", "", false)
	m.width, m.height = 100, 30

	if m.claudeMode {
		t.Fatal("Claude mode claimed without the launch flag")
	}
	if strings.Contains(m.View(), "│ claude") {
		t.Error("the chip must be absent when inference runs locally")
	}
}

// A transport that cannot report its argv (no ClaudeAtLaunch) never triggers
// the chip either — absence of evidence is a local session.
func TestNoClaudeChipOnATransportWithoutArgv(t *testing.T) {
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = 100, 30

	if m.claudeMode || strings.Contains(m.View(), "│ claude") {
		t.Error("a transport without launch argv must render as local")
	}
}
