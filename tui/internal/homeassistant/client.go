// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package homeassistant talks to the flagship sidecar's Home Assistant routes
// (/v1/gaia/home-assistant) through the daemon relay, for the TUI's /home.
//
// The access token is sent once, in the PUT body, and never kept, logged or
// put in an error: every error here carries either the sidecar's own words or
// the operation's name, neither of which contains it.
package homeassistant

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/amd/gaia/tui/internal/daemon"
)

// AgentID is the agent whose sidecar owns the Home Assistant routes.
const AgentID = "gaia"

// Path is the sidecar route, as the daemon relays it.
const Path = "/v1/" + AgentID + "/home-assistant"

// DefaultAddress is what the address field starts with.
const DefaultAddress = "http://homeassistant.local:8123"

// maxBody bounds an answer; these are a few hundred bytes.
const maxBody = 1 << 20

// Status is GET/PUT/DELETE's answer.
type Status struct {
	Connected bool   `json:"connected"`
	URL       string `json:"url"`
	Active    bool   `json:"active"`
	// Enabled is absent when nothing is saved.
	Enabled   *bool  `json:"enabled,omitempty"`
	ToolCount int    `json:"tool_count,omitempty"`
	Detail    string `json:"detail,omitempty"`
}

// TestResult is POST /test's answer.
type TestResult struct {
	OK        bool   `json:"ok"`
	Detail    string `json:"detail"`
	ToolCount int    `json:"tool_count,omitempty"`
}

// API is what /home asks. An interface so the chat tests script it.
type API interface {
	Status(ctx context.Context) (Status, error)
	Connect(ctx context.Context, url, token string) (Status, error)
	Test(ctx context.Context) (TestResult, error)
	Disconnect(ctx context.Context) (Status, error)
}

// StatusError is a refusal from the sidecar. Detail is its own plain-language
// fix, shown to the user as written.
type StatusError struct {
	Op     string
	Status int
	Detail string
}

func (e *StatusError) Error() string {
	if e.Detail == "" {
		return fmt.Sprintf("GAIA could not %s (HTTP %d)", e.Op, e.Status)
	}
	return e.Detail
}

// ErrTooOld is returned when the running agent has no Home Assistant routes.
var ErrTooOld = errors.New(
	"this GAIA agent is older than Home Assistant support. " +
		"Type /update to get the current GAIA, then try /home again")

// Client is API over the daemon relay.
type Client struct {
	dc *daemon.Client
	// ensure returns the daemon instance with the flagship sidecar running.
	// A field so tests can point it at an httptest server.
	ensure func(ctx context.Context) (*daemon.Instance, error)
}

// New builds the API over this machine's daemon, starting it and the
// flagship sidecar when they are not up.
func New() *Client {
	dc := daemon.New(daemon.Options{})
	return &Client{dc: dc, ensure: func(ctx context.Context) (*daemon.Instance, error) {
		return dc.EnsureAgent(ctx, AgentID)
	}}
}

func (c *Client) Status(ctx context.Context) (Status, error) {
	var s Status
	err := c.call(ctx, http.MethodGet, Path, "read the Home Assistant connection", nil, &s)
	return s, err
}

func (c *Client) Connect(ctx context.Context, url, token string) (Status, error) {
	var s Status
	err := c.call(ctx, http.MethodPut, Path, "connect Home Assistant",
		map[string]string{"url": url, "token": token}, &s)
	return s, err
}

func (c *Client) Test(ctx context.Context) (TestResult, error) {
	var r TestResult
	err := c.call(ctx, http.MethodPost, Path+"/test", "test the Home Assistant connection", nil, &r)
	return r, err
}

func (c *Client) Disconnect(ctx context.Context) (Status, error) {
	var s Status
	err := c.call(ctx, http.MethodDelete, Path, "disconnect Home Assistant", nil, &s)
	return s, err
}

func (c *Client) call(ctx context.Context, method, path, op string, body, out any) error {
	inst, err := c.ensure(ctx)
	if err != nil {
		return fmt.Errorf("could not reach GAIA to %s: %w", op, err)
	}
	var raw []byte
	header := http.Header{"Accept": []string{"application/json"}}
	if body != nil {
		if raw, err = json.Marshal(body); err != nil {
			return fmt.Errorf("could not build the request to %s: %w", op, err)
		}
		header.Set("Content-Type", "application/json")
	}
	resp, _, err := c.dc.Do(ctx, inst, daemon.Request{
		Method: method, Path: path, Body: raw, Header: header, Op: op,
	})
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return fmt.Errorf("could not read GAIA's answer to %s: %w", op, err)
	}
	return decode(op, resp.StatusCode, data, out)
}

func decode(op string, status int, data []byte, out any) error {
	if status >= 200 && status < 300 {
		if err := json.Unmarshal(data, out); err != nil {
			return fmt.Errorf("GAIA's answer to %s could not be read: %w", op, err)
		}
		return nil
	}
	var body struct {
		Detail json.RawMessage `json:"detail"`
	}
	detail := ""
	if json.Unmarshal(data, &body) == nil && len(body.Detail) > 0 {
		// A string is the sidecar's message; anything else (a validation
		// list) is not written for people, so only the status is shown.
		_ = json.Unmarshal(body.Detail, &detail)
	}
	if status == http.StatusNotFound && isNotFound(detail) {
		return ErrTooOld
	}
	return &StatusError{Op: op, Status: status, Detail: detail}
}

func isNotFound(detail string) bool {
	switch strings.ToLower(strings.TrimSpace(detail)) {
	case "", "not found", "404 not found", "404: not found":
		return true
	}
	return false
}
