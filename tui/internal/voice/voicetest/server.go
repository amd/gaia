// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package voicetest is a scripted stand-in for the daemon's voice socket, so
// UI tests can drive dictation and voice mode without a microphone.
package voicetest

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/coder/websocket"

	"github.com/amd/gaia/tui/internal/voice"
)

// Command is one message the client sent.
type Command struct {
	Type string `json:"type"`
	Text string `json:"text,omitempty"`
	Kind string `json:"kind,omitempty"`
}

// Server answers like the daemon: ready on start, a final on commit, the
// speaking events for speak/speak_end, and closed on stop.
type Server struct {
	// CommitText is the transcript a commit produces.
	CommitText string
	// Token is the bearer the server requires; empty accepts anything.
	Token string

	srv      *httptest.Server
	mu       sync.Mutex
	commands []Command
	conns    []*websocket.Conn
	speaking bool
}

// New starts a server; it stops with the test.
func New(t *testing.T) *Server {
	t.Helper()
	s := &Server{}
	s.srv = httptest.NewServer(http.HandlerFunc(s.handle))
	t.Cleanup(s.srv.Close)
	return s
}

// URL is the socket's address.
func (s *Server) URL() string { return s.srv.URL + voice.SessionPath }

// Dialer is a voice.Dialer that reaches this server.
func (s *Server) Dialer() func(context.Context, string) (*voice.Conn, error) {
	return func(ctx context.Context, purpose string) (*voice.Conn, error) {
		ws, _, err := websocket.Dial(ctx, s.URL(), nil)
		if err != nil {
			return nil, err
		}
		return voice.Open(ws, purpose)
	}
}

// Commands is everything received so far.
func (s *Server) Commands() []Command {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Command(nil), s.commands...)
}

// Saw reports whether a command of type kind arrived.
func (s *Server) Saw(kind string) bool {
	for _, c := range s.Commands() {
		if c.Type == kind {
			return true
		}
	}
	return false
}

// Spoken joins the text of every speak of kind.
func (s *Server) Spoken(kind string) string {
	var parts []string
	for _, c := range s.Commands() {
		if c.Type == "speak" && c.Kind == kind {
			parts = append(parts, c.Text)
		}
	}
	return strings.Join(parts, "")
}

// Push sends ev on the newest connection.
func (s *Server) Push(ev voice.Event) {
	s.mu.Lock()
	var ws *websocket.Conn
	if n := len(s.conns); n > 0 {
		ws = s.conns[n-1]
	}
	s.mu.Unlock()
	if ws != nil {
		send(ws, ev)
	}
}

func send(ws *websocket.Conn, ev voice.Event) {
	data, _ := json.Marshal(ev)
	_ = ws.Write(context.Background(), websocket.MessageText, data)
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	if s.Token != "" && r.Header.Get("Authorization") != "Bearer "+s.Token {
		http.Error(w, `{"detail":"bad token"}`, http.StatusUnauthorized)
		return
	}
	ws, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	s.mu.Lock()
	s.conns = append(s.conns, ws)
	s.mu.Unlock()
	ctx := context.Background()
	for {
		_, data, err := ws.Read(ctx)
		if err != nil {
			return
		}
		var c Command
		var start struct {
			Purpose string `json:"purpose"`
		}
		_ = json.Unmarshal(data, &c)
		_ = json.Unmarshal(data, &start)
		s.mu.Lock()
		s.commands = append(s.commands, c)
		first := !s.speaking
		s.mu.Unlock()
		switch c.Type {
		case "start":
			send(ws, voice.Event{Type: "ready", Speech: start.Purpose == voice.PurposeVoice})
		case "commit":
			send(ws, voice.Event{Type: "final", Text: s.CommitText})
		case "speak":
			if first {
				s.setSpeaking(true)
				send(ws, voice.Event{Type: "speaking", Kind: c.Kind})
			}
			send(ws, voice.Event{Type: "spoken_piece", Kind: c.Kind, Text: c.Text})
		case "speak_end":
			s.setSpeaking(false)
			send(ws, voice.Event{Type: "speaking_done"})
		case "interrupt":
			s.setSpeaking(false)
			send(ws, voice.Event{Type: "speech_stopped_by_user"})
		case "stop":
			send(ws, voice.Event{Type: "closed"})
			_ = ws.Close(websocket.StatusNormalClosure, "")
			return
		}
	}
}

func (s *Server) setSpeaking(v bool) {
	s.mu.Lock()
	s.speaking = v
	s.mu.Unlock()
}
