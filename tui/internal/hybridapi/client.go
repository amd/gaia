// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package hybridapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/event"
)

// Prefix is where the daemon serves the hybrid routes.
const Prefix = "/api/hybrid"

// Time limits. The preview waits on the classifier, whose own limit is at
// most 120 s; everything else reads config and asks the model server.
const (
	ReadTimeout    = 30 * time.Second
	PreviewTimeout = 150 * time.Second
	EntryTimeout   = 10 * time.Second
	// StartTimeout covers starting the daemon when it isn't running.
	StartTimeout = 95 * time.Second
	maxBody      = 4 << 20
)

// API is what the TUI asks the backend about hybrid. An interface so screens
// are tested against a scripted backend.
type API interface {
	Settings(ctx context.Context) (Settings, error)
	// Update changes the named settings; the rest keep their saved values.
	Update(ctx context.Context, changes map[string]any) (Settings, error)
	Templates(ctx context.Context) ([]Template, error)
	ApplyTemplate(ctx context.Context, id string) (Settings, error)
	Preview(ctx context.Context, text string) (event.CanonicalRoute, error)
	Stats(ctx context.Context) (Stats, error)
	Entry(ctx context.Context) (Entry, error)
}

// Client talks to the machine's GAIA daemon.
type Client struct {
	d *daemon.Client
	// attachOnly never starts a daemon, so a background read leaves nothing behind.
	attachOnly bool
	http       *http.Client
}

// New is the API over the GAIA daemon, starting it when it isn't running.
func New() *Client { return newClient(false) }

// NewAttached is the API over a daemon that is already running; a call fails
// rather than start one.
func NewAttached() *Client { return newClient(true) }

func newClient(attachOnly bool) *Client {
	return &Client{d: daemon.New(daemon.Options{}), attachOnly: attachOnly, http: &http.Client{
		// Each call carries its own deadline; see ReadTimeout and PreviewTimeout.
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}
}

// StatusError is the backend refusing a request, in its own words.
type StatusError struct {
	What   string
	Status int
	Detail string
}

func (e *StatusError) Error() string {
	if e.Detail == "" {
		return fmt.Sprintf("%s failed (HTTP %d)", e.What, e.Status)
	}
	return e.Detail
}

// IsStatus reports whether err is a backend refusal with that status code.
func IsStatus(err error, status int) bool {
	var se *StatusError
	return errors.As(err, &se) && se.Status == status
}

// UnreachableError is a call that never got an answer from the daemon.
type UnreachableError struct {
	What   string
	Base   string
	Reason string
	Err    error
}

func (e *UnreachableError) Error() string {
	where := ""
	if e.Base != "" {
		where = " at " + e.Base
	}
	return e.What + " needs GAIA's background service" + where + ", which " + e.Reason +
		". Quit and reopen GAIA, then try again; the service's log is at " + logPath() + "."
}

func (e *UnreachableError) Unwrap() error { return e.Err }

// TimeoutError is a call the daemon took too long to answer.
type TimeoutError struct {
	What  string
	Base  string
	Limit time.Duration
}

func (e *TimeoutError) Error() string {
	where := "GAIA's background service"
	if e.Base != "" {
		where += " at " + e.Base
	}
	return fmt.Sprintf("%s: %s didn't answer within %s. Try again; if it keeps happening, "+
		"the service's log at %s says why.", e.What, where, e.Limit, logPath())
}

func (e *TimeoutError) Unwrap() error { return context.DeadlineExceeded }

// OutdatedError is a daemon too old to have the hybrid routes.
type OutdatedError struct {
	What string
	Base string
}

func (e *OutdatedError) Error() string {
	return e.What + ": GAIA's background service at " + e.Base + " has no hybrid settings, so it is " +
		"older than this app. Update GAIA from https://amd-gaia.ai/#install, then try again."
}

func logPath() string {
	if p, err := daemon.LogPath(); err == nil {
		return p
	}
	return "the GAIA logs folder"
}

func (c *Client) instance(ctx context.Context) (*daemon.Instance, error) {
	if c.attachOnly {
		return c.d.Attach(ctx)
	}
	return c.d.StartOrAttach(ctx)
}

// call sends one request and decodes a 2xx answer into out.
func (c *Client) call(ctx context.Context, limit time.Duration, method, path string, body, out any, what string) error {
	parent := ctx
	// Starting the daemon has its own, longer limit; the call's limit starts
	// once there is a daemon to ask.
	startCtx, cancelStart := context.WithTimeout(parent, StartTimeout)
	inst, err := c.instance(startCtx)
	cancelStart()
	if err != nil {
		return failed(parent, startCtx, what, "", StartTimeout, err)
	}
	if deadline, ok := parent.Deadline(); ok && time.Until(deadline) < limit {
		limit = time.Until(deadline).Round(time.Millisecond)
	}
	ctx, cancel := context.WithTimeout(parent, limit)
	defer cancel()
	var raw []byte
	header := http.Header{}
	if body != nil {
		if raw, err = json.Marshal(body); err != nil {
			return fmt.Errorf("%s: could not encode the request: %w", what, err)
		}
		header.Set("Content-Type", "application/json")
	}
	resp, _, err := c.d.Do(ctx, inst, daemon.Request{
		Method: method, Path: path, Body: raw, Header: header, HTTPClient: c.http, Op: what,
	})
	if err != nil {
		return failed(parent, ctx, what, inst.BaseURL(), limit, err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return failed(parent, ctx, what, inst.BaseURL(), limit, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		d := detail(data)
		if resp.StatusCode == http.StatusNotFound && strings.EqualFold(d, "not found") {
			return &OutdatedError{What: what, Base: inst.BaseURL()}
		}
		return &StatusError{What: what, Status: resp.StatusCode, Detail: d}
	}
	if err := json.Unmarshal(data, out); err != nil {
		return fmt.Errorf("%s: GAIA's background service answered %s with something this app can't read (%v); "+
			"update GAIA so the two match", what, path, err)
	}
	return nil
}

// failed says why a call got no answer. A cancelled caller gets
// context.Canceled back as is: nobody is waiting for a message.
func failed(parent, ctx context.Context, what, base string, limit time.Duration, err error) error {
	if errors.Is(parent.Err(), context.Canceled) {
		return context.Canceled
	}
	if errors.Is(ctx.Err(), context.DeadlineExceeded) || errors.Is(err, context.DeadlineExceeded) {
		return &TimeoutError{What: what, Base: base, Limit: limit}
	}
	var (
		notRunning *daemon.NotRunningError
		stale      *daemon.StaleError
		start      *daemon.StartError
		version    *daemon.VersionError
		missing    *daemon.RouteMissingError
	)
	switch {
	case errors.As(err, &version), errors.As(err, &missing):
		return &OutdatedError{What: what, Base: base}
	case errors.As(err, &notRunning):
		return &UnreachableError{What: what, Base: base, Reason: "isn't running", Err: err}
	case errors.As(err, &stale):
		return &UnreachableError{What: what, Base: base, Reason: "stopped responding (" + stale.Reason + ")", Err: err}
	case errors.As(err, &start):
		return &UnreachableError{What: what, Base: base, Reason: "wouldn't start (" + start.Reason + ")", Err: err}
	}
	var req *daemon.RequestError
	switch {
	case errors.As(err, &req) && req.Err != nil:
		return &UnreachableError{What: what, Base: base, Reason: "didn't answer (" + req.Err.Error() + ")", Err: err}
	case errors.As(err, &req):
		// The daemon answered but refused this app's credentials.
		return &UnreachableError{What: what, Base: base, Reason: "stopped accepting this app's credentials", Err: err}
	}
	return &UnreachableError{What: what, Base: base, Reason: "couldn't be used (" + err.Error() + ")", Err: err}
}

// detail is FastAPI's error message: {"detail": "..."} or a 422 list of them.
func detail(raw []byte) string {
	var env struct {
		Detail json.RawMessage `json:"detail"`
	}
	if json.Unmarshal(raw, &env) == nil && len(env.Detail) > 0 {
		var text string
		if json.Unmarshal(env.Detail, &text) == nil {
			return text
		}
		var items []struct {
			Msg string `json:"msg"`
		}
		if json.Unmarshal(env.Detail, &items) == nil && len(items) > 0 {
			msgs := make([]string, 0, len(items))
			for _, it := range items {
				msgs = append(msgs, it.Msg)
			}
			return strings.Join(msgs, "; ")
		}
	}
	return strings.TrimSpace(string(raw))
}

// Settings reads the hybrid settings and everything the screen shows with them.
func (c *Client) Settings(ctx context.Context) (Settings, error) {
	var s Settings
	err := c.call(ctx, ReadTimeout, http.MethodGet, Prefix+"/settings", nil, &s, "Reading the hybrid settings")
	return s, err
}

// Update saves changes; the backend validates them first.
func (c *Client) Update(ctx context.Context, changes map[string]any) (Settings, error) {
	var s Settings
	err := c.call(ctx, ReadTimeout, http.MethodPut, Prefix+"/settings", changes, &s, "Saving the hybrid settings")
	return s, err
}

// Templates lists the built-in pairings as they resolve on this PC.
func (c *Client) Templates(ctx context.Context) ([]Template, error) {
	var t []Template
	err := c.call(ctx, ReadTimeout, http.MethodGet, Prefix+"/templates", nil, &t, "Listing the hybrid templates")
	return t, err
}

// ApplyTemplate makes the template the hybrid settings.
func (c *Client) ApplyTemplate(ctx context.Context, id string) (Settings, error) {
	var s Settings
	err := c.call(ctx, ReadTimeout, http.MethodPost, Prefix+"/templates/"+url.PathEscape(id)+"/apply",
		map[string]string{}, &s, "Applying the template")
	return s, err
}

// Preview asks how text would be routed under the saved settings.
func (c *Client) Preview(ctx context.Context, text string) (event.CanonicalRoute, error) {
	var r event.CanonicalRoute
	err := c.call(ctx, PreviewTimeout, http.MethodPost, Prefix+"/preview", map[string]string{"text": text}, &r,
		"Trying a request")
	if err == nil && r.Model == "" {
		err = errors.New("the preview named no model; update GAIA so this app and its background service match")
	}
	return r, err
}

// Stats reads how hybrid turns were routed and what they cost.
func (c *Client) Stats(ctx context.Context) (Stats, error) {
	var s Stats
	if err := c.call(ctx, ReadTimeout, http.MethodGet, Prefix+"/stats", nil, &s, "Reading the routing stats"); err != nil {
		return s, err
	}
	return s, s.check()
}

// Entry reads hybrid's row for a model picker.
func (c *Client) Entry(ctx context.Context) (Entry, error) {
	var e Entry
	err := c.call(ctx, EntryTimeout, http.MethodGet, Prefix+"/entry", nil, &e, "Naming hybrid")
	if err == nil && e.DisplayName == "" {
		err = errors.New("hybrid's picker entry has no name; update GAIA so this app and its background service match")
	}
	return e, err
}
