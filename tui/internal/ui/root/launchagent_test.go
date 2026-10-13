// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// fixtureAgent returns a subprocess-transport catalog.Agent that resolves to
// a real (never-executed) script — enough for client.ForAgent to build a
// SubprocessClient without spawning anything, since construction is lazy.
func fixtureAgent(t *testing.T, id string) catalog.Agent {
	t.Helper()
	dir := t.TempDir()
	name := "fixture-" + id
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return catalog.Agent{ID: id, Name: id, Transport: catalog.TransportSubprocess, BinaryPath: path}
}

// Deleting `m.chatClient.close()` at the top of launchAgent's commit branch
// leaks the outgoing transport on every switch — an orphaned subprocess
// child, or a live daemon SSE stream nothing ever tears down.
func TestLaunchAgentCommitClosesTheOutgoingClient(t *testing.T) {
	m, oldClient := liveChatModel(t, catalog.FlagshipID)

	updated, _ := m.launchAgent(fixtureAgent(t, "other"), true)
	m = updated.(FlagshipModel)

	m.chatClient.retiring.Wait()
	if !oldClient.closed {
		t.Fatal("the outgoing client survived the switch — it leaks")
	}
	if m.agent.ID != "other" || m.activeView != viewChat {
		t.Fatalf("test setup: the switch itself did not commit: agent=%v view=%v", m.agent.ID, m.activeView)
	}
}

// slowCloseClient is an agent still saving memories when it is asked to stop.
type slowCloseClient struct {
	nullClient
	release chan struct{}
	done    chan struct{}
}

func (c *slowCloseClient) Close() error {
	<-c.release
	close(c.done)
	return nil
}

// The outgoing agent's shutdown runs off the event loop — a switch must not
// freeze the screen while it saves memories — but quit still waits for it.
func TestSwitchDoesNotWaitForTheOutgoingAgentButQuitDoes(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	slow := &slowCloseClient{release: make(chan struct{}), done: make(chan struct{})}
	m.chatClient.set(slow)

	switched := make(chan FlagshipModel, 1)
	go func() {
		updated, _ := m.launchAgent(fixtureAgent(t, "other"), true)
		switched <- updated.(FlagshipModel)
	}()
	select {
	case m = <-switched:
	case <-time.After(5 * time.Second):
		close(slow.release)
		t.Fatal("the switch blocked on the outgoing agent's shutdown")
	}

	quit := make(chan error, 1)
	go func() { quit <- m.Close() }()
	select {
	case <-quit:
		t.Fatal("quit returned while the outgoing agent was still shutting down")
	case <-time.After(100 * time.Millisecond):
	}
	close(slow.release)
	if err := <-quit; err != nil {
		t.Fatal(err)
	}
	select {
	case <-slow.done:
	default:
		t.Fatal("quit returned before the outgoing agent finished closing")
	}
}

// Deleting the `m.pendingTranscript = nil` that follows WithMessages leaves
// the divider queued forever: the NEXT launch — including one that has
// nothing to do with this switch — carries it too.
func TestLaunchAgentCommitClearsThePendingTranscript(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	divider := "Switched from gaia to other — other does not see the conversation above this line."
	m.pendingTranscript = []chat.Message{{Role: chat.RoleStatus, Content: divider}}

	updated, _ := m.launchAgent(fixtureAgent(t, "other"), true)
	m = updated.(FlagshipModel)

	if len(m.pendingTranscript) != 0 {
		t.Fatal("pendingTranscript was consumed but not cleared")
	}
	found := false
	for _, msg := range m.chat.Messages() {
		if msg.Content == divider {
			found = true
		}
	}
	if !found {
		t.Fatal("test setup: the divider never made it into the replacement chat")
	}

	// A second, unrelated launch must not see a divider that belongs to the
	// switch already committed above.
	updated, _ = m.launchAgent(fixtureAgent(t, "another"), true)
	m = updated.(FlagshipModel)

	for _, msg := range m.chat.Messages() {
		if strings.Contains(msg.Content, "Switched from gaia to other") {
			t.Fatalf("a stale divider from the previous switch reached an unrelated launch: %+v", m.chat.Messages())
		}
	}
}

// The saved-default notice explains the launch that set it aside; repeating
// it after an /agents switch would describe a decision nobody just made.
func TestLaunchAgentShowsTheFullAccessNoticeOnce(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	const notice = "Full access is saved as your default, but this agent cannot use it."
	m = m.WithFullAccessNotice(notice)

	updated, _ := m.launchAgent(fixtureAgent(t, "other"), true)
	m = updated.(FlagshipModel)
	shown := 0
	for _, msg := range m.chat.Messages() {
		if msg.Content == notice {
			shown++
		}
	}
	if shown != 1 {
		t.Fatalf("the notice must appear once on the launch it explains, got %d: %+v", shown, m.chat.Messages())
	}

	updated, _ = m.launchAgent(fixtureAgent(t, "another"), true)
	m = updated.(FlagshipModel)
	for _, msg := range m.chat.Messages() {
		if msg.Content == notice {
			t.Fatalf("a later launch repeated the notice: %+v", m.chat.Messages())
		}
	}
}
