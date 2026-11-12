// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"net/http"
	"strings"
	"testing"
)

// reply_style negotiation (contract 2.28): a voice turn asks the flagship for
// a spoken summary, and only a flagship that takes the field is sent it.

func TestAVoiceTurnSendsReplyStyleToTheFlagshipAt228(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.28"
	f.strictBody = true
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"<spoken>Hi.</spoken>"}`)
		flush()
	}
	c := f.clientFor(t, "gaia")
	if err := c.CheckVoiceReplies(context.Background()); err != nil {
		t.Fatalf("a 2.28 flagship was refused: %v", err)
	}
	ch, err := c.Send(WithReplyStyle(context.Background(), ReplyStyleVoice), "hello")
	if err != nil {
		t.Fatal(err)
	}
	if !hasFinal(collect(t, ch)) {
		t.Fatal("the turn did not complete")
	}
	if got := f.queries[0].ReplyStyle; got != "voice" {
		t.Errorf("reply_style = %q: %s", got, f.lastRawBody())
	}
}

func TestATypedTurnNeverSendsReplyStyle(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.28"
	sendOnce(t, f, "gaia")
	if raw := f.lastRawBody(); strings.Contains(raw, "reply_style") {
		t.Errorf("a typed turn carried reply_style: %s", raw)
	}
}

func TestAnOlderFlagshipRefusesVoiceMode(t *testing.T) {
	for _, tc := range []struct{ agent, version string }{
		{"gaia", "2.27"},
		{"email", "2.28"},
	} {
		f := newFakeRelay(t)
		f.contractVersion = tc.version
		c := f.clientFor(t, tc.agent)
		err := c.CheckVoiceReplies(context.Background())
		if err == nil || !strings.Contains(err.Error(), "Voice mode needs a newer GAIA agent") {
			t.Errorf("%s@%s: err = %v", tc.agent, tc.version, err)
		}
		if _, err := c.Send(WithReplyStyle(context.Background(), ReplyStyleVoice), "hi"); err == nil {
			t.Errorf("%s@%s: a voice turn was sent to a peer that rejects reply_style", tc.agent, tc.version)
		}
	}
}
