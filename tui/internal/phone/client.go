// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package phone talks to the daemon's phone alerts (/daemon/v1/phone/*): the
// provider, the phone book, the rules and the alerts. See
// docs/guides/phone-alerts.mdx.
package phone

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

// Prefix is where the daemon serves phone alerts.
const Prefix = daemon.APIPrefix + "/phone"

// ErrUnavailable means the running background service has no phone routes:
// it predates phone alerts, or runs without them.
var ErrUnavailable = errors.New("phone alerts aren't in the GAIA background service running on this PC yet. They arrive when GAIA updates")

// Error is a refusal from the phone service, with its stable code.
type Error struct {
	Status  int
	Code    string
	Message string
}

func (e *Error) Error() string { return e.Message }

// Doer sends one authenticated request to the daemon and returns its status
// and body. Tests stand in a plain HTTP server.
type Doer func(ctx context.Context, method, path string, body []byte) (int, []byte, error)

// Client calls the phone routes.
type Client struct{ do Doer }

// New wraps a Doer.
func New(do Doer) *Client { return &Client{do: do} }

// NewHTTP talks to baseURL with a bearer token; for tests and fakes.
func NewHTTP(baseURL, token string) *Client {
	hc := &http.Client{Timeout: 30 * time.Second}
	return New(func(ctx context.Context, method, path string, body []byte) (int, []byte, error) {
		var rdr io.Reader
		if body != nil {
			rdr = bytes.NewReader(body)
		}
		req, err := http.NewRequestWithContext(ctx, method, baseURL+path, rdr)
		if err != nil {
			return 0, nil, err
		}
		req.Header.Set("Authorization", "Bearer "+token)
		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := hc.Do(req)
		if err != nil {
			return 0, nil, err
		}
		defer resp.Body.Close()
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		return resp.StatusCode, raw, err
	})
}

// NewDaemon talks to this machine's daemon. start=false only attaches to a
// running one, which is what a background poll wants.
func NewDaemon(start bool) *Client {
	dc := daemon.New(daemon.Options{})
	var (
		mu   sync.Mutex
		inst *daemon.Instance
	)
	return New(func(ctx context.Context, method, path string, body []byte) (int, []byte, error) {
		mu.Lock()
		cur := inst
		mu.Unlock()
		if cur == nil {
			var err error
			if start {
				cur, err = dc.StartOrAttach(ctx)
			} else {
				cur, err = dc.Attach(ctx)
			}
			if err != nil {
				return 0, nil, fmt.Errorf("could not reach the GAIA background service: %w", err)
			}
		}
		req := daemon.Request{Method: method, Path: path, Body: body, Op: "reach phone alerts"}
		if body != nil {
			req.Header = http.Header{"Content-Type": []string{"application/json"}}
		}
		resp, fresh, err := dc.Do(ctx, cur, req)
		mu.Lock()
		if err != nil {
			inst = nil
		} else {
			inst = fresh
		}
		mu.Unlock()
		if err != nil {
			return 0, nil, err
		}
		defer resp.Body.Close()
		raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		return resp.StatusCode, raw, err
	})
}

func (c *Client) call(ctx context.Context, method, path string, in, out any) error {
	var body []byte
	if in != nil {
		var err error
		if body, err = json.Marshal(in); err != nil {
			return err
		}
	}
	status, raw, err := c.do(ctx, method, Prefix+path, body)
	if err != nil {
		return err
	}
	if status < 200 || status > 299 {
		return refusal(status, raw)
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("the GAIA background service sent an unreadable phone answer: %w", err)
	}
	return nil
}

// refusal reads the daemon's {detail: {code, message}}. A 404 with no code is
// a route the daemon doesn't have.
func refusal(status int, raw []byte) error {
	var body struct {
		Detail json.RawMessage `json:"detail"`
	}
	_ = json.Unmarshal(raw, &body)
	var detail struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	}
	if json.Unmarshal(body.Detail, &detail) == nil && detail.Code != "" {
		return &Error{Status: status, Code: detail.Code, Message: detail.Message}
	}
	if status == http.StatusNotFound {
		return ErrUnavailable
	}
	var text string
	if json.Unmarshal(body.Detail, &text) != nil || text == "" {
		text = strings.TrimSpace(string(raw))
	}
	return &Error{Status: status, Code: "bad_request", Message: fmt.Sprintf("The phone service refused that (HTTP %d): %s", status, text)}
}

// ---------------------------------------------------------------- shapes

// Contact is one phone book number, masked.
type Contact struct {
	ID      int    `json:"id"`
	Label   string `json:"label"`
	Number  string `json:"number"`
	Primary bool   `json:"primary"`
}

// LastError is the provider's last failure.
type LastError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Awake is whether the PC is held awake for calls, and why not.
type Awake struct {
	Held   bool   `json:"held"`
	Reason string `json:"reason"`
}

// Status is GET /status.
type Status struct {
	Enabled      bool       `json:"enabled"`
	Provider     string     `json:"provider"`
	Configured   bool       `json:"configured"`
	FromNumber   string     `json:"from_number"`
	TextsEnabled bool       `json:"texts_enabled"`
	Contacts     []Contact  `json:"contacts"`
	OpenAlerts   int        `json:"open_alerts"`
	CallsToday   int        `json:"calls_today"`
	TextsToday   int        `json:"texts_today"`
	LastError    *LastError `json:"last_error"`
	Awake        *Awake     `json:"awake"`
}

// Rule is one alert rule.
type Rule struct {
	On           bool   `json:"on"`
	Steps        string `json:"steps"`
	Urgent       bool   `json:"urgent"`
	AfterMinutes int    `json:"after_minutes,omitempty"`
}

// Quiet is a quiet-hours window, HH:MM 24-hour.
type Quiet struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

// Limits are the daily caps.
type Limits struct {
	CallsPerDay      int `json:"calls_per_day"`
	TextsPerDay      int `json:"texts_per_day"`
	AgentCallsPerDay int `json:"agent_calls_per_day"`
}

// Settings is GET /settings.
type Settings struct {
	Enabled      bool            `json:"enabled"`
	Provider     string          `json:"provider"`
	FromNumber   string          `json:"from_number"`
	TextsEnabled bool            `json:"texts_enabled"`
	QuietHours   *Quiet          `json:"quiet_hours"`
	Limits       Limits          `json:"limits"`
	KeepAwake    bool            `json:"keep_awake"`
	Rules        map[string]Rule `json:"rules"`
	// Talking on calls: the PIN itself never comes back, only whether one is set.
	PinSet        bool   `json:"pin_set"`
	TalkAvailable bool   `json:"talk_available"`
	TalkOnCalls   bool   `json:"talk_on_calls"`
	Voice         string `json:"voice"`
}

// Delivery is one call or text an alert made.
type Delivery struct {
	Channel string `json:"channel"`
	Number  string `json:"number"`
	State   string `json:"state"`
	Outcome string `json:"outcome"`
	Reached bool   `json:"reached"`
	Error   string `json:"error"`
}

// Alert is one alert and what it did.
type Alert struct {
	ID         int        `json:"id"`
	Source     string     `json:"source"`
	Kind       string     `json:"kind"`
	Title      string     `json:"title"`
	Reason     string     `json:"reason"`
	Count      int        `json:"count"`
	State      string     `json:"state"`
	Urgent     bool       `json:"urgent"`
	Reached    bool       `json:"reached"`
	Held       string     `json:"held"`
	Deliveries []Delivery `json:"deliveries"`
}

// Started is a code request: where it went and its handle.
type Started struct {
	VerificationID   string `json:"verification_id"`
	Number           string `json:"number"`
	NeedsConfirmCode bool   `json:"needs_confirm_code"`
	ConfirmSentTo    string `json:"confirm_sent_to"`
}

// SimCall and SimText are what the Simulated phone recorded.
type SimCall struct {
	To     string `json:"to"`
	Script string `json:"script"`
}

// SimText is one recorded text.
type SimText struct {
	To   string `json:"to"`
	Body string `json:"body"`
}

// SimLog is GET /simulated/log.
type SimLog struct {
	Calls []SimCall `json:"calls"`
	Texts []SimText `json:"texts"`
}

// ---------------------------------------------------------------- calls

func (c *Client) Status(ctx context.Context) (Status, error) {
	var out Status
	return out, c.call(ctx, http.MethodGet, "/status", nil, &out)
}

func (c *Client) Settings(ctx context.Context) (Settings, error) {
	var out Settings
	return out, c.call(ctx, http.MethodGet, "/settings", nil, &out)
}

// PutSettings sends only the keys in patch.
func (c *Client) PutSettings(ctx context.Context, patch map[string]any) (Settings, error) {
	var out Settings
	return out, c.call(ctx, http.MethodPut, "/settings", patch, &out)
}

// SetPin sets the PIN a caller types on the keypad. It goes in the body only.
func (c *Client) SetPin(ctx context.Context, pin string) (Settings, error) {
	var out Settings
	return out, c.call(ctx, http.MethodPost, "/pin", map[string]string{"pin": pin}, &out)
}

// ClearPin removes the PIN, so calls can't talk.
func (c *Client) ClearPin(ctx context.Context) (Settings, error) {
	var out Settings
	return out, c.call(ctx, http.MethodDelete, "/pin", nil, &out)
}

// SetKeys checks the Twilio keys with Twilio and stores them. The token goes
// in the request body only.
func (c *Client) SetKeys(ctx context.Context, sid, token, number string) (Status, error) {
	var out Status
	in := map[string]string{"account_sid": sid, "auth_token": token, "from_number": number}
	return out, c.call(ctx, http.MethodPost, "/keys", in, &out)
}

func (c *Client) ForgetKeys(ctx context.Context) (Status, error) {
	var out Status
	return out, c.call(ctx, http.MethodDelete, "/keys", nil, &out)
}

func (c *Client) UseSimulated(ctx context.Context) (Status, error) {
	var out Status
	return out, c.call(ctx, http.MethodPost, "/simulated", nil, &out)
}

func (c *Client) SimulatedLog(ctx context.Context) (SimLog, error) {
	var out SimLog
	return out, c.call(ctx, http.MethodGet, "/simulated/log", nil, &out)
}

// AddNumber starts adding a number: a code goes to it by via ("call"/"text").
func (c *Client) AddNumber(ctx context.Context, number, label, via string) (Started, error) {
	var out Started
	in := map[string]string{"number": number, "label": label, "via": via}
	return out, c.call(ctx, http.MethodPost, "/contacts", in, &out)
}

// ConfirmAdd finishes it; confirm is the code the first number got, if asked.
func (c *Client) ConfirmAdd(ctx context.Context, vid, code, confirm string) (Contact, error) {
	in := map[string]any{"verification_id": vid, "code": code}
	if confirm != "" {
		in["confirm_code"] = confirm
	}
	var out Contact
	return out, c.call(ctx, http.MethodPost, "/contacts/confirm", in, &out)
}

func (c *Client) StartRemove(ctx context.Context, id int) (Started, error) {
	var out Started
	return out, c.call(ctx, http.MethodPost, fmt.Sprintf("/contacts/%d/remove", id), nil, &out)
}

func (c *Client) ConfirmRemove(ctx context.Context, vid, code string) (Status, error) {
	var out Status
	in := map[string]string{"verification_id": vid, "code": code}
	return out, c.call(ctx, http.MethodPost, "/contacts/remove/confirm", in, &out)
}

// Alerts lists alerts, newest first; open=true only those still open.
func (c *Client) Alerts(ctx context.Context, open bool) ([]Alert, error) {
	path := "/alerts"
	if open {
		path += "?open=true"
	}
	var out struct {
		Alerts []Alert `json:"alerts"`
	}
	return out.Alerts, c.call(ctx, http.MethodGet, path, nil, &out)
}

func (c *Client) Handled(ctx context.Context, id int) (Alert, error) {
	var out Alert
	return out, c.call(ctx, http.MethodPost, fmt.Sprintf("/alerts/%d/handled", id), nil, &out)
}

// Test raises a test alert.
func (c *Client) Test(ctx context.Context) (Alert, error) {
	var out struct {
		Alert Alert `json:"alert"`
	}
	return out.Alert, c.call(ctx, http.MethodPost, "/test", nil, &out)
}
