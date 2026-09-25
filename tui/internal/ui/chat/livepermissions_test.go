// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
)

// reportingClient can answer a prompt by Go type, and says at runtime whether
// the peer behind it actually can — what the daemon relay does.
type reportingClient struct {
	permissionClient
	live bool
}

func (c *reportingClient) SupportsLivePermissions() bool { return c.live }

func TestAPromptIsLiveOnlyWhenThePeerCanAnswerIt(t *testing.T) {
	// Implementing the interface is a fact about the Go type. The email agent
	// over the daemon is the same type as gaia and serves neither route, so
	// the modal must not promise a decision that will never be delivered.
	for _, tc := range []struct {
		live bool
		want bool
	}{{true, true}, {false, false}} {
		m := NewChatModel(&reportingClient{live: tc.live}, "gaia", "", false)
		if got := m.canRespondToPermission(); got != tc.want {
			t.Errorf("peer live=%t: canRespondToPermission() = %t, want %t", tc.live, got, tc.want)
		}
	}
}

func TestATransportThatDoesNotReportIsStillAble(t *testing.T) {
	// The stdio child always has its control channel.
	m := NewChatModel(&permissionClient{}, "gaia", "", false)
	if !m.canRespondToPermission() {
		t.Error("a responder that does not report must keep answering live")
	}
}

func TestBypassIsRefusedWhenThePeerCannotCarryIt(t *testing.T) {
	c := &reportingClient{live: false}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30

	updated, _ := m.setBypass(true)
	after := updated.(ChatModel)

	if len(c.bypassCalls) != 0 {
		t.Errorf("bypass must not be sent to a peer without the route: %v", c.bypassCalls)
	}
	last := after.messages[len(after.messages)-1].Content
	if !strings.Contains(last, "cannot change permission mode") {
		t.Errorf("the user must be told prompts stay on, got: %q", last)
	}
}
