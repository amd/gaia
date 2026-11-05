// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package voice

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"

	"github.com/amd/gaia/tui/internal/daemon"
)

// SessionPath is the daemon's voice socket. The daemon records this
// machine's microphone and plays speech itself; the socket carries events.
const SessionPath = daemon.APIPrefix + "/voice/session"

// Purposes a session can be opened for.
const (
	PurposeDictation = "dictation"
	PurposeVoice     = "voice"
)

// Speak kinds: the reply's spoken summary, or a status line before a step.
const (
	KindSpoken = "spoken"
	KindStatus = "status"
)

// Event is one JSON event from the daemon's voice socket (see
// gaia.voice.protocol for the full list).
type Event struct {
	Type    string  `json:"type"`
	Text    string  `json:"text,omitempty"`
	Kind    string  `json:"kind,omitempty"`
	Source  string  `json:"source,omitempty"`
	Value   float64 `json:"value,omitempty"`
	Code    string  `json:"code,omitempty"`
	Message string  `json:"message,omitempty"`
	Detail  string  `json:"detail,omitempty"`
	Speech  bool    `json:"speech,omitempty"`
	Seconds int     `json:"seconds,omitempty"`
}

// Conn is one open voice session. Commands never block the caller: they
// queue for a writer goroutine, so Bubble Tea's Update can send them inline.
type Conn struct {
	ws     *websocket.Conn
	events chan Event
	out    chan []byte
	cancel context.CancelFunc
	once   sync.Once
	done   chan struct{}
}

// Dialer opens sessions. Tests replace it to stand in a fake daemon.
var Dialer = Dial

// dialTimeout bounds the upgrade; the daemon is on loopback.
const dialTimeout = 15 * time.Second

// Dial opens a voice session for purpose against the running daemon, with
// the daemon client token. A token that rotated (the daemon restarted) is
// re-read from instance.json and the upgrade retried once.
func Dial(ctx context.Context, purpose string) (*Conn, error) {
	dc := daemon.New(daemon.Options{})
	dctx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	inst, err := dc.StartOrAttach(dctx)
	if err != nil {
		return nil, fmt.Errorf("could not reach the GAIA background service: %w", err)
	}
	ws, resp, err := dialOnce(dctx, inst)
	if err != nil && resp != nil && resp.StatusCode == http.StatusUnauthorized {
		fresh, rerr := dc.RefreshToken(dctx, inst)
		if rerr != nil {
			return nil, rerr
		}
		ws, resp, err = dialOnce(dctx, fresh)
	}
	if err != nil {
		return nil, handshakeError(resp, err)
	}
	return Open(ws, purpose)
}

func dialOnce(ctx context.Context, inst *daemon.Instance) (*websocket.Conn, *http.Response, error) {
	dctx, cancel := context.WithTimeout(ctx, dialTimeout)
	defer cancel()
	header := http.Header{"Authorization": []string{daemon.AuthScheme + " " + inst.Token}}
	return websocket.Dial(dctx, inst.BaseURL()+SessionPath, &websocket.DialOptions{HTTPHeader: header})
}

// handshakeError turns a refused upgrade into something a person can act on.
func handshakeError(resp *http.Response, err error) error {
	if resp == nil {
		return fmt.Errorf("could not open the voice connection to the GAIA background service: %w", err)
	}
	detail := responseDetail(resp)
	switch resp.StatusCode {
	case http.StatusNotFound:
		return &daemon.RouteMissingError{Op: "run dictation or voice mode", Path: SessionPath}
	case http.StatusUnauthorized:
		return fmt.Errorf("the GAIA background service refused this TUI's client token for voice")
	}
	if detail == "" {
		detail = err.Error()
	}
	return fmt.Errorf("the GAIA background service could not open voice (HTTP %d): %s", resp.StatusCode, detail)
}

func responseDetail(resp *http.Response) string {
	if resp.Body == nil {
		return ""
	}
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
	var body struct {
		Detail any `json:"detail"`
	}
	if json.Unmarshal(raw, &body) == nil {
		if s, ok := body.Detail.(string); ok {
			return s
		}
	}
	return strings.TrimSpace(string(raw))
}

// Open starts a session on an established socket: it sends start and begins
// reading events. Exposed so tests can run against an httptest server.
func Open(ws *websocket.Conn, purpose string) (*Conn, error) {
	ws.SetReadLimit(1 << 20)
	ctx, cancel := context.WithCancel(context.Background())
	c := &Conn{
		ws:     ws,
		events: make(chan Event, 256),
		out:    make(chan []byte, 256),
		cancel: cancel,
		done:   make(chan struct{}),
	}
	start, _ := json.Marshal(map[string]string{"type": "start", "purpose": purpose})
	if err := ws.Write(ctx, websocket.MessageText, start); err != nil {
		cancel()
		_ = ws.CloseNow()
		return nil, fmt.Errorf("could not start the voice session: %w", err)
	}
	go c.writeLoop(ctx)
	go c.readLoop(ctx)
	return c, nil
}

// Events is closed when the session ends. Its last event is "closed", or an
// "error" with code session_lost when the connection dropped.
func (c *Conn) Events() <-chan Event { return c.events }

func (c *Conn) readLoop(ctx context.Context) {
	defer close(c.events)
	defer close(c.done)
	for {
		kind, data, err := c.ws.Read(ctx)
		if err != nil {
			if ctx.Err() == nil && websocket.CloseStatus(err) != websocket.StatusNormalClosure {
				c.emit(ctx, Event{Type: "error", Code: "session_lost",
					Message: "Lost the connection to speech.", Detail: err.Error()})
			}
			return
		}
		if kind != websocket.MessageText {
			continue // the daemon plays speech itself; no audio comes here
		}
		var ev Event
		if err := json.Unmarshal(data, &ev); err != nil {
			if !c.emit(ctx, Event{Type: "error", Code: "bad_request",
				Message: "The voice service sent an unreadable event.", Detail: err.Error()}) {
				return
			}
			continue
		}
		if !c.emit(ctx, ev) || ev.Type == "closed" {
			return
		}
	}
}

// emit hands ev to the reader; false once the session was closed locally.
func (c *Conn) emit(ctx context.Context, ev Event) bool {
	select {
	case c.events <- ev:
		return true
	case <-ctx.Done():
		return false
	}
}

func (c *Conn) writeLoop(ctx context.Context) {
	for {
		select {
		case msg := <-c.out:
			if err := c.ws.Write(ctx, websocket.MessageText, msg); err != nil {
				return // readLoop reports the drop
			}
		case <-ctx.Done():
			return
		}
	}
}

func (c *Conn) send(v map[string]string) error {
	msg, err := json.Marshal(v)
	if err != nil {
		return err
	}
	select {
	case c.out <- msg:
		return nil
	case <-c.done:
		return errors.New("the voice session has ended")
	default:
		return errors.New("the voice session is not keeping up; it was closed")
	}
}

// Speak queues text to be spoken; kind is KindSpoken or KindStatus.
func (c *Conn) Speak(text, kind string) error {
	return c.send(map[string]string{"type": "speak", "text": text, "kind": kind})
}

// SpeakEnd says the spoken block is complete.
func (c *Conn) SpeakEnd() error { return c.send(map[string]string{"type": "speak_end"}) }

// Interrupt drops queued and playing speech at once.
func (c *Conn) Interrupt() error { return c.send(map[string]string{"type": "interrupt"}) }

// Commit ends the utterance now (dictation stop).
func (c *Conn) Commit() error { return c.send(map[string]string{"type": "commit"}) }

// TalkDown opens the daemon's capture in push-to-talk.
func (c *Conn) TalkDown() error { return c.send(map[string]string{"type": "ptt_down"}) }

// TalkUp closes it and ends the utterance.
func (c *Conn) TalkUp() error { return c.send(map[string]string{"type": "ptt_up"}) }

// Close asks the daemon to stop, then drops the connection. Safe to call
// more than once.
func (c *Conn) Close() {
	c.once.Do(func() {
		if msg, err := json.Marshal(map[string]string{"type": "stop"}); err == nil {
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			_ = c.ws.Write(ctx, websocket.MessageText, msg)
			cancel()
		}
		c.cancel()
		_ = c.ws.Close(websocket.StatusNormalClosure, "")
	})
}
