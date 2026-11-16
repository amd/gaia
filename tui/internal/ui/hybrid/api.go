// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package hybrid

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// AgentUIURL is the Agent UI server, which runs the real classifier for a
// preview and keeps the spending record the stats come from. A var so tests
// reach a stub.
var AgentUIURL = "http://localhost:4200"

// AgentUIURLEnv points the TUI at an Agent UI started on another port
// (`gaia chat --ui --ui-port N`).
const AgentUIURLEnv = "GAIA_AGENT_UI_URL"

// agentUIBase is the Agent UI address: AgentUIURLEnv when set, else
// AgentUIURL. Only a loopback http address is accepted.
func agentUIBase() (string, error) {
	raw := strings.TrimSpace(os.Getenv(AgentUIURLEnv))
	if raw == "" {
		return strings.TrimRight(AgentUIURL, "/"), nil
	}
	u, err := url.Parse(raw)
	host := ""
	if err == nil {
		host = u.Hostname()
	}
	ip := net.ParseIP(host)
	loopback := host == "localhost" || (ip != nil && ip.IsLoopback())
	if err != nil || u.Scheme != "http" || !loopback || u.Port() == "" || strings.Trim(u.Path, "/") != "" ||
		u.User != nil || u.RawQuery != "" {
		return "", fmt.Errorf("%s is %q, but it must be the Agent UI's loopback address, "+
			"like http://localhost:4200. Fix or unset it, then reopen /hybrid", AgentUIURLEnv, raw)
	}
	return strings.TrimRight(u.Scheme+"://"+u.Host, "/"), nil
}

// ServerDownError is a preview or stats read that found no Agent UI server.
type ServerDownError struct {
	Base string
	What string
	Err  error
}

func (e *ServerDownError) Error() string {
	return e.What + " needs the GAIA Agent UI server, which isn't answering at " + e.Base +
		". Start it with `gaia chat --ui` (or set " + AgentUIURLEnv + " to the address it runs on), then try again"
}

func (e *ServerDownError) Unwrap() error { return e.Err }

// RouteMissingError is an Agent UI server older than the route asked for.
type RouteMissingError struct {
	Base, Path string
}

func (e *RouteMissingError) Error() string {
	return "the Agent UI server at " + e.Base + " has no " + e.Path + " — it is older than this TUI. " +
		"Update GAIA, restart `gaia chat --ui`, then try again"
}

// Stats is GET /api/hybrid/stats: how hybrid turns were routed and what they cost.
type Stats struct {
	Since        string `json:"since"`
	Turns        int    `json:"turns"`
	CheapTurns   int    `json:"cheap_turns"`
	StrongTurns  int    `json:"strong_turns"`
	FailedClosed int    `json:"failed_closed"`
	// Cost and CostIfAllStrong are nil when a turn's price is unknown, never $0.
	Cost            *float64 `json:"cost"`
	CostIfAllStrong *float64 `json:"cost_if_all_strong"`
	SavingPct       *float64 `json:"saving_pct"`
	// UnpricedTurns ran on a model with an unknown price.
	UnpricedTurns int `json:"unpriced_turns"`
	// Scope is which chats the record covers; absent from a server that
	// records only Agent UI turns.
	Scope string `json:"scope"`
	// ScopeLabel is Scope in the server's own words, shown verbatim.
	ScopeLabel string `json:"scope_label"`
}

// Line is the stats in one line.
func (s Stats) Line() string {
	if s.Turns == 0 {
		return s.scopeLabel() + ": no hybrid turns recorded yet."
	}
	line := fmt.Sprintf("%d %s: %d on the cheap model, %d on the strong",
		s.Turns, plural(s.Turns, "turn"), s.CheapTurns, s.StrongTurns)
	if s.FailedClosed > 0 {
		line += fmt.Sprintf(" (%d unclassified)", s.FailedClosed)
	}
	line += " · " + spent(s.Cost) + ", " + allStrong(s.CostIfAllStrong)
	if s.SavingPct != nil && *s.SavingPct > 0 {
		line += fmt.Sprintf(" · %.0f%% saved", *s.SavingPct)
	}
	if s.UnpricedTurns > 0 {
		line += fmt.Sprintf(" · %d %s at an unknown price", s.UnpricedTurns, plural(s.UnpricedTurns, "turn"))
	}
	if s.Since != "" {
		line = "since " + day(s.Since) + ": " + line
	}
	return s.scopeLabel() + ", " + line
}

// scopeLabel says whose turns the record holds. Until the server reports a
// scope, only Agent UI chats are recorded, so this terminal's turns are not in it.
func (s Stats) scopeLabel() string {
	if label := strings.TrimSpace(s.ScopeLabel); label != "" {
		return label
	}
	switch s.Scope {
	case "", "agent_ui", "agent_ui_chats":
		return "Agent UI chats (this terminal's turns aren't counted yet)"
	case "all":
		return "All chats"
	}
	return "Chats in " + s.Scope
}

func spent(c *float64) string {
	if c == nil {
		return "cost unknown"
	}
	return usd(*c) + " spent"
}

func allStrong(c *float64) string {
	if c == nil {
		return "unknown if all strong"
	}
	return usd(*c) + " if all strong"
}

// usd shows a cost to the cent, or to a hundredth of a cent below one cent,
// so a small spend never reads as $0.00 beside a saving.
func usd(c float64) string {
	if c > 0 && c < 0.01 {
		return fmt.Sprintf("$%.4f", c)
	}
	return fmt.Sprintf("$%.2f", c)
}

// day is an ISO timestamp as a date, "16 Nov 2026"; anything else is shown as sent.
func day(ts string) string {
	if t, err := time.Parse(time.RFC3339Nano, ts); err == nil {
		return t.Local().Format("2 Jan 2006")
	}
	if t, err := time.Parse("2006-01-02", ts); err == nil {
		return t.Format("2 Jan 2006")
	}
	return ts
}

func plural(n int, word string) string {
	if n == 1 {
		return word
	}
	return word + "s"
}

// api talks to the Agent UI's /api/hybrid routes.
type api struct {
	base    string
	baseErr error
	http    *http.Client
}

func newAPI() api {
	base, baseErr := agentUIBase()
	return api{base: base, baseErr: baseErr, http: &http.Client{
		// The preview waits on the classifier, whose limit is at most 120 s.
		Timeout: 150 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}}
}

// Preview asks the server how it would route text under the saved settings.
func (a api) Preview(ctx context.Context, text string) (event.CanonicalRoute, error) {
	var r event.CanonicalRoute
	err := a.call(ctx, http.MethodPost, "/api/hybrid/preview", map[string]string{"text": text}, &r, "Trying a request")
	if err == nil && r.Model == "" {
		err = errors.New("the Agent UI server's preview named no model; update GAIA so the TUI and server match")
	}
	return r, err
}

// Stats reads the routing record.
func (a api) Stats(ctx context.Context) (Stats, error) {
	var s Stats
	return s, a.call(ctx, http.MethodGet, "/api/hybrid/stats", nil, &s, "Showing routing stats")
}

func (a api) call(ctx context.Context, method, path string, body any, out any, what string) error {
	if a.baseErr != nil {
		return fmt.Errorf("%s: %w", what, a.baseErr)
	}
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("could not encode the request: %w", err)
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, a.base+path, reader)
	if err != nil {
		return fmt.Errorf("invalid Agent UI address %s: %w", a.base, err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	// The Agent UI refuses a state-changing request without it.
	req.Header.Set("X-Gaia-UI", "1")
	resp, err := a.http.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		var netErr net.Error
		if errors.As(err, &netErr) && netErr.Timeout() {
			return fmt.Errorf("%s: the Agent UI server at %s took longer than %s to answer; "+
				"check its log (`gaia diagnostics`), then try again", what, a.base, a.http.Timeout)
		}
		return &ServerDownError{Base: a.base, What: what, Err: err}
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return fmt.Errorf("%s: the Agent UI server's answer was cut off: %w", what, err)
	}
	// FastAPI's own 404 is "Not Found": the route is missing, not the thing asked for.
	if resp.StatusCode == http.StatusNotFound && detail(raw) == "Not Found" {
		return &RouteMissingError{Base: a.base, Path: path}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("%s failed (HTTP %d): %s", what, resp.StatusCode, detail(raw))
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("the Agent UI server answered %s with something that isn't JSON (%v); "+
			"update GAIA so the TUI and server match", path, err)
	}
	return nil
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
	text := strings.TrimSpace(string(raw))
	if text == "" {
		return "no detail given"
	}
	return text
}

// Templates lists the server's hybrid templates, judged on its own view of
// which providers are set up.
func (a api) Templates(ctx context.Context) ([]lemonade.HybridTemplate, error) {
	var t []lemonade.HybridTemplate
	return t, a.call(ctx, http.MethodGet, "/api/hybrid/templates", nil, &t, "Listing templates")
}

// ApplyTemplate makes the template the hybrid settings.
func (a api) ApplyTemplate(ctx context.Context, id string) error {
	var out map[string]any
	return a.call(ctx, http.MethodPost, "/api/hybrid/templates/"+url.PathEscape(id)+"/apply",
		map[string]string{}, &out, "Applying the template")
}

// Entry is GET /api/hybrid/entry: hybrid's row in a model picker.
type Entry struct {
	ID          string  `json:"id"`
	DisplayName string  `json:"display_name"`
	Remote      *bool   `json:"remote"`
	Available   bool    `json:"available"`
	Reason      *string `json:"reason"`
	Shown       bool    `json:"shown"`
}

// Label is the entry's name with where it runs.
func (e Entry) Label() string {
	switch {
	case e.Remote == nil:
		return e.DisplayName
	case *e.Remote:
		return e.DisplayName + " (cloud)"
	}
	return e.DisplayName + " (local)"
}

// FetchEntry asks the Agent UI server for hybrid's picker entry.
func FetchEntry(ctx context.Context) (Entry, error) {
	var e Entry
	err := newAPI().call(ctx, http.MethodGet, "/api/hybrid/entry", nil, &e, "Naming hybrid")
	if err == nil && e.DisplayName == "" {
		err = errors.New("the Agent UI server's hybrid entry has no name; update GAIA so the TUI and server match")
	}
	return e, err
}

// IsServerAbsent reports an error that only means the Agent UI server isn't
// running, or predates the route, so the caller judges on this PC instead.
func IsServerAbsent(err error) bool {
	var down *ServerDownError
	var missing *RouteMissingError
	return errors.As(err, &down) || errors.As(err, &missing)
}

// serverNotes are the notes GET /api/hybrid/settings gives the saved pair.
type serverNotes struct {
	Measured       bool    `json:"measured"`
	MeasuredNote   *string `json:"measured_note"`
	ClassifierNote *string `json:"classifier_note"`
}

// Notes reads the server's notes for the saved settings.
func (a api) Notes(ctx context.Context) (serverNotes, error) {
	var n serverNotes
	return n, a.call(ctx, http.MethodGet, "/api/hybrid/settings", nil, &n, "Reading the hybrid notes")
}
