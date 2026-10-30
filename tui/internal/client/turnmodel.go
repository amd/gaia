// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"fmt"
	"strings"
)

// modelSwitchContract is the flagship contract from which a turn's `model`
// moves a retained session onto that model in place (#3698, schema 2.14).
// Below it the session keeps the model it started on.
const (
	modelSwitchContractMajor = 2
	modelSwitchContractMinor = 14
)

// modelSwitchAgentID is the only agent that switches a session's model. Every
// sidecar numbers its own contract, so the version alone says nothing elsewhere.
const modelSwitchAgentID = "gaia"

// TurnModelSwitcher is a transport that names the model on every turn — the
// daemon relay. There is no `/model` turn to send: a switch is a setting on the
// connection, and the agent applies it to the next turn.
type TurnModelSwitcher interface {
	// CheckModelSwitch returns why the agent behind this connection cannot move
	// the conversation to another model, or nil when it can. Blocking: it may
	// attach to the daemon and read the agent's contract version.
	CheckModelSwitch(ctx context.Context) error
	// SetTurnModel names the model every following turn runs on.
	SetTurnModel(model string)
	// TurnModel is the model turns are sent with now; "" means the agent's own.
	TurnModel() string
}

// ModelSwitchRefusedError is the agent refusing to move the conversation onto
// the turn's model. The session is still on the model it had.
type ModelSwitchRefusedError struct {
	Model  string
	Detail string
}

func (e *ModelSwitchRefusedError) Error() string {
	return fmt.Sprintf("the agent could not switch to %s (%s)", e.Model, e.Detail)
}

// switchRefused reports whether a /query 409 is a refused model switch rather
// than a session still busy with the previous turn. Keyed on server.py's detail.
func switchRefused(detail string) bool {
	return strings.Contains(detail, "could not switch session")
}

var _ TurnModelSwitcher = (*SSEClient)(nil)

// CheckModelSwitch implements TurnModelSwitcher.
func (s *SSEClient) CheckModelSwitch(ctx context.Context) error {
	if s.agentID != modelSwitchAgentID {
		return fmt.Errorf("the '%s' agent does not switch models", s.agentID)
	}
	inst, err := s.daemon.EnsureAgent(ctx, s.agentID)
	if err != nil {
		return err
	}
	peer := s.negotiate(ctx, inst)
	if !peer.answered {
		return fmt.Errorf(
			"the '%s' agent did not say which contract it speaks, so a model switch "+
				"cannot be trusted to take — check `gaia daemon status`, then relaunch",
			s.agentID)
	}
	if !contractAtLeast(peer.version, modelSwitchContractMajor, modelSwitchContractMinor) {
		return fmt.Errorf(
			"the installed '%s' agent speaks contract %s, and switching models in a "+
				"conversation needs %d.%d or newer — update it with `%s` then `%s`",
			s.agentID, peer.versionLabel(), modelSwitchContractMajor, modelSwitchContractMinor,
			updateCommand("uninstall", s.agentID), updateCommand("install", s.agentID))
	}
	return nil
}

// TurnModel implements TurnModelSwitcher.
func (s *SSEClient) TurnModel() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.model
}

// SetTurnModel implements TurnModelSwitcher.
func (s *SSEClient) SetTurnModel(model string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.model = model
}
