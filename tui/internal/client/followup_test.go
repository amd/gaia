// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"testing"
)

// startedRun holds a turn open so a follow-up has something live to land on,
// and hands back the stopper. The real case is a five-minute agentic turn; here
// one channel does the same job without the wait.
func startedRun(t *testing.T, f *fakeRelay) (*SSEClient, <-chan interface{}, func()) {
	t.Helper()
	release := make(chan struct{})
	var once sync.Once
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"working"}`)
		flush()
		<-release
		frame(w, `{"type":"final","answer":"done"}`)
		flush()
	}

	c := f.client(t)
	ch, err := c.Send(context.Background(), "triage my inbox")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	stop := func() { once.Do(func() { close(release) }) }
	t.Cleanup(stop)
	return c, ch, stop
}

// The feature: a message typed during a turn reaches THAT turn, at its own
// run_id, without a second /query the session lock would refuse anyway.
func TestSendFollowUpPostsToTheRunningTurn(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c, ch, stop := startedRun(t, f)

	if err := c.SendFollowUp(context.Background(), "only the unread ones"); err != nil {
		t.Fatalf("SendFollowUp: %v", err)
	}

	f.mu.Lock()
	got := append([]followUpCall(nil), f.followUps...)
	f.mu.Unlock()
	if len(got) != 1 {
		t.Fatalf("got %d follow-up posts, want 1", len(got))
	}
	if got[0].text != "only the unread ones" {
		t.Errorf("follow-up text = %q", got[0].text)
	}
	if got[0].runID != f.lastQuery().RunID {
		t.Errorf("follow-up went to run %q, want the running turn %q", got[0].runID, f.lastQuery().RunID)
	}

	stop()
	collect(t, ch)
}

// /query is stateless: the sidecar's copy of the conversation is overwritten by
// the context pushed next turn. A follow-up the host does not record is a
// message the agent demonstrably saw and the conversation then forgets.
func TestADeliveredFollowUpSurvivesIntoTheNextTurn(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c, ch, stop := startedRun(t, f)

	if err := c.SendFollowUp(context.Background(), "only the unread ones"); err != nil {
		t.Fatalf("SendFollowUp: %v", err)
	}
	stop()
	collect(t, ch)

	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"second"}`)
		flush()
	}
	ch2, err := c.Send(context.Background(), "and now?")
	if err != nil {
		t.Fatalf("second Send: %v", err)
	}
	collect(t, ch2)

	var roles, contents []string
	for _, turn := range f.lastQuery().Context {
		roles = append(roles, turn.Role)
		contents = append(contents, turn.Content)
	}
	// Between the question and the answer: that is where it was said, and the
	// only order in which the answer reads as a reply to both.
	want := []string{"user", "user", "assistant"}
	if strings.Join(roles, ",") != strings.Join(want, ",") {
		t.Fatalf("pushed context roles = %v, want %v (%v)", roles, want, contents)
	}
	if contents[0] != "triage my inbox" || contents[1] != "only the unread ones" {
		t.Errorf("pushed context lost or reordered the follow-up: %v", contents)
	}
}

// A follow-up the sidecar refused must not appear in the conversation the next
// turn pushes — that would put words in the agent's mouth it never received.
func TestARefusedFollowUpIsNotRecorded(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.followUpStatus = http.StatusNotFound
	c, ch, stop := startedRun(t, f)

	err := c.SendFollowUp(context.Background(), "only the unread ones")
	if err == nil {
		t.Fatal("a 404 was reported as a successful delivery")
	}
	if !strings.Contains(err.Error(), "not delivered") {
		t.Errorf("the error does not say the message did not land: %v", err)
	}

	stop()
	collect(t, ch)

	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"second"}`)
		flush()
	}
	ch2, sendErr := c.Send(context.Background(), "and now?")
	if sendErr != nil {
		t.Fatalf("second Send: %v", sendErr)
	}
	collect(t, ch2)

	for _, turn := range f.lastQuery().Context {
		if turn.Content == "only the unread ones" {
			t.Fatalf("an undelivered follow-up entered the pushed context: %v", f.lastQuery().Context)
		}
	}
}

// An older sidecar 404s the path itself, which is indistinguishable from "your
// run ended" if the client just posts and reads the status. Ask first.
func TestAnOlderPeerIsNeverSentAFollowUp(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.12"
	c, ch, stop := startedRun(t, f)

	if c.FollowUpSupported() {
		t.Error("a 2.12 peer was reported as taking mid-turn input")
	}
	err := c.SendFollowUp(context.Background(), "only the unread ones")
	if err == nil {
		t.Fatal("the POST was sent to a peer with no such route")
	}
	// Naming the floor and the fix is the difference between "broken" and
	// "update the agent" — and the sentence has to end with where the message
	// went, because the user is still holding it.
	for _, want := range []string{"2.13", "gaia hub", "queued"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not mention %q: %v", want, err)
		}
	}

	f.mu.Lock()
	n := len(f.followUps)
	f.mu.Unlock()
	if n != 0 {
		t.Errorf("%d follow-ups were posted to a peer that cannot take them", n)
	}

	stop()
	collect(t, ch)
}

// Between turns there is no run to add to, and saying so beats a 404 the user
// has to decode.
func TestAFollowUpWithNoRunInFlightIsRefused(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"done"}`)
		flush()
	}

	c := f.client(t)
	ch, err := c.Send(context.Background(), "hi")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	collect(t, ch)

	if err := c.SendFollowUp(context.Background(), "and the calendar"); err == nil {
		t.Fatal("a follow-up was accepted with no run in flight")
	}
}

// Whitespace in a composer is not a message, and it must not become a user turn
// in the model's context.
func TestAnEmptyFollowUpIsRefusedBeforeTheNetwork(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c, ch, stop := startedRun(t, f)

	if err := c.SendFollowUp(context.Background(), "   "); err == nil {
		t.Fatal("an empty follow-up was accepted")
	}
	f.mu.Lock()
	n := len(f.followUps)
	f.mu.Unlock()
	if n != 0 {
		t.Errorf("an empty follow-up reached the network: %d posts", n)
	}

	stop()
	collect(t, ch)
}
