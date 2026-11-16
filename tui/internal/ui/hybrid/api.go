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
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

// AgentUIURL is the Agent UI server, which runs the real classifier for a
// preview and keeps the spending record the stats come from. A var so tests
// reach a stub.
var AgentUIURL = "http://localhost:4200"

// ServerDownError is a preview or stats read that found no Agent UI server.
type ServerDownError struct {
	Base string
	What string
	Err  error
}

func (e *ServerDownError) Error() string {
	return e.What + " needs the GAIA Agent UI server, which isn't answering at " + e.Base +
		" (" + e.Err.Error() + "). Start it with `gaia chat --ui`, then try again"
}

func (e *ServerDownError) Unwrap() error { return e.Err }

// Stats is GET /api/hybrid/stats: how hybrid turns were routed and what they cost.
type Stats struct {
	Since           string  `json:"since"`
	Turns           int     `json:"turns"`
	CheapTurns      int     `json:"cheap_turns"`
	StrongTurns     int     `json:"strong_turns"`
	FailedClosed    int     `json:"failed_closed"`
	Cost            float64 `json:"cost"`
	CostIfAllStrong float64 `json:"cost_if_all_strong"`
	SavingPct       float64 `json:"saving_pct"`
}

// Line is the stats in one line.
func (s Stats) Line() string {
	if s.Turns == 0 {
		return "No hybrid turns recorded yet."
	}
	line := fmt.Sprintf("%d %s: %d on the cheap model, %d on the strong",
		s.Turns, plural(s.Turns, "turn"), s.CheapTurns, s.StrongTurns)
	if s.FailedClosed > 0 {
		line += fmt.Sprintf(" (%d unclassified)", s.FailedClosed)
	}
	line += fmt.Sprintf(" · $%.2f spent, $%.2f if all strong", s.Cost, s.CostIfAllStrong)
	if s.SavingPct > 0 {
		line += fmt.Sprintf(" · %.0f%% saved", s.SavingPct)
	}
	if s.Since != "" {
		line = "Since " + s.Since + ": " + line
	}
	return line
}

func plural(n int, word string) string {
	if n == 1 {
		return word
	}
	return word + "s"
}

// api talks to the Agent UI's /api/hybrid routes.
type api struct {
	base string
	http *http.Client
}

func newAPI() api {
	return api{base: strings.TrimRight(AgentUIURL, "/"), http: &http.Client{
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
	if resp.StatusCode == http.StatusNotFound {
		return fmt.Errorf("the Agent UI server at %s has no %s — it is older than this TUI. "+
			"Update GAIA, restart `gaia chat --ui`, then try again", a.base, path)
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
