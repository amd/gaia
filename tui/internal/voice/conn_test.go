// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package voice_test

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/voice"
	"github.com/amd/gaia/tui/internal/voice/voicetest"
)

func next(t *testing.T, c *voice.Conn) voice.Event {
	t.Helper()
	select {
	case ev, ok := <-c.Events():
		if !ok {
			t.Fatal("the session ended early")
		}
		return ev
	case <-time.After(5 * time.Second):
		t.Fatal("no voice event arrived")
	}
	return voice.Event{}
}

func TestSessionCommandsReachTheDaemon(t *testing.T) {
	srv := voicetest.New(t)
	srv.CommitText = "hello there"
	conn, err := srv.Dialer()(context.Background(), voice.PurposeDictation)
	if err != nil {
		t.Fatal(err)
	}
	if ev := next(t, conn); ev.Type != "ready" || ev.Speech {
		t.Fatalf("first event = %+v, want a dictation ready", ev)
	}
	if err := conn.Commit(); err != nil {
		t.Fatal(err)
	}
	if ev := next(t, conn); ev.Type != "final" || ev.Text != "hello there" {
		t.Fatalf("after commit got %+v", ev)
	}
	if err := conn.Speak("Hi.", voice.KindStatus); err != nil {
		t.Fatal(err)
	}
	if ev := next(t, conn); ev.Type != "speaking" {
		t.Fatalf("after speak got %+v", ev)
	}
	conn.Close()
	conn.Close() // twice is fine
	deadline := time.Now().Add(5 * time.Second)
	for !srv.Saw("stop") && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	got := srv.Commands()
	if got[0].Type != "start" || !srv.Saw("commit") || !srv.Saw("stop") {
		t.Fatalf("commands = %+v", got)
	}
	if srv.Spoken(voice.KindStatus) != "Hi." {
		t.Fatalf("status spoken = %q", srv.Spoken(voice.KindStatus))
	}
}

func TestADroppedConnectionIsSessionLost(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		_, _, _ = ws.Read(context.Background()) // start
		_ = ws.CloseNow()
	}))
	defer srv.Close()
	ws, _, err := websocket.Dial(context.Background(), srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := voice.Open(ws, voice.PurposeVoice)
	if err != nil {
		t.Fatal(err)
	}
	if ev := next(t, conn); ev.Type != "error" || ev.Code != "session_lost" {
		t.Fatalf("got %+v, want session_lost", ev)
	}
}

func TestRefusedUpgradesSayWhy(t *testing.T) {
	cases := []struct {
		status int
		body   string
		want   string
	}{
		{http.StatusServiceUnavailable, `{"detail":"missing the Python package 'websockets'"}`, "websockets"},
		{http.StatusNotFound, `{"detail":"Not Found"}`, "older than this GAIA"},
	}
	for _, c := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(c.status)
			_, _ = w.Write([]byte(c.body))
		}))
		_, resp, err := websocket.Dial(context.Background(), srv.URL, nil)
		srv.Close()
		if err == nil {
			t.Fatal("the upgrade should have been refused")
		}
		got := voice.HandshakeErrorForTest(resp, err)
		if !strings.Contains(got.Error(), c.want) {
			t.Errorf("HTTP %d: %q does not mention %q", c.status, got, c.want)
		}
		var missing *daemon.RouteMissingError
		if c.status == http.StatusNotFound && !errors.As(got, &missing) {
			t.Errorf("HTTP 404 should be a RouteMissingError, got %T", got)
		}
	}
}

func TestErrorTextKeepsTheDaemonsRemedy(t *testing.T) {
	msg := "Allow it in Settings › Privacy & security › Microphone"
	if got := voice.ErrorText("mic_denied", msg); got != msg {
		t.Fatalf("mic_denied lost its remedy: %q", got)
	}
	if got := voice.ErrorText("session_lost", "raw socket error"); strings.Contains(got, "raw") {
		t.Fatalf("session_lost leaked the raw error: %q", got)
	}
}
