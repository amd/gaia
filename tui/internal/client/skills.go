// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/event"
)

// SkillLister is implemented by a transport that has to be asked which skills
// the agent has. The subprocess transport is not one: its agent names them in
// its startup ping.
type SkillLister interface {
	ListSkills(ctx context.Context) ([]event.SkillCommand, error)
}

var _ SkillLister = (*SSEClient)(nil)

// ListSkills reads GET /v1/<agent>/skills through the daemon relay: every
// skill the agent can load, the ones built into it included. The result is
// never nil on success, so "no skills" and "not asked" stay different.
func (s *SSEClient) ListSkills(ctx context.Context) ([]event.SkillCommand, error) {
	inst, err := s.daemon.EnsureAgent(ctx, s.agentID)
	if err != nil {
		return nil, err
	}
	resp, inst, err := s.daemon.Do(ctx, inst, daemon.Request{
		Method: http.MethodGet,
		Path:   fmt.Sprintf("/v1/%s/skills", url.PathEscape(s.agentID)),
		Header: http.Header{"Accept": []string{"application/json"}},
		Op:     fmt.Sprintf("list the '%s' agent's skills through the daemon relay", s.agentID),
	})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	s.mu.Lock()
	s.inst = inst
	s.mu.Unlock()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, fmt.Errorf("could not read the '%s' skill list: %w", s.agentID, err)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("the daemon relay refused the '%s' skill list (%s)",
			s.agentID, errorDetailFromBody(resp.StatusCode, raw))
	}
	var body struct {
		Skills []event.SkillCommand `json:"skills"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		return nil, fmt.Errorf("could not decode the '%s' skill list: %w", s.agentID, err)
	}
	if body.Skills == nil {
		return []event.SkillCommand{}, nil
	}
	return body.Skills, nil
}
