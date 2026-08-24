// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"fmt"
	"strings"
)

// The Claude ids this TUI will hand to an agent, and nothing else.
//
// It lives beside ClaudeModelFlag because it is part of the same argv
// contract: whatever goes after --claude-model reaches the child verbatim,
// and the child's provider accepts ANY string starting with "claude-" (see
// src/gaia/llm/providers/claude.py). So a typo like "claude-haiku-45" is not
// caught anywhere downstream — it becomes a 404 from Anthropic several
// seconds into the first turn, long after the user could connect it to what
// they typed. Refusing it here, before a UI even opens, is the only place the
// message can still name the flag the user got wrong.
//
// This list MUST stay identical to CLAUDE_MODELS in
// hub/agents/gaia/python/gaia_agent/stdio.py — the agent validates `/model`
// against its own copy, and two lists that disagree means an id the launch
// flag accepts and `/model` refuses (or worse, the reverse). TestGoAndPython
// ClaudeModelListsAgree (claudemodels_test.go) reads that file and fails the
// build if they drift.
//
// Ordered, not a map, because it is rendered: the palette and every refusal
// message list these in a fixed order, and Go map iteration would reshuffle
// them on every run.
var ClaudeModels = []ClaudeModel{
	{ID: "claude-opus-5", Label: "Opus 5"},
	{ID: "claude-sonnet-5", Label: "Sonnet 5"},
	{ID: "claude-haiku-4-5", Label: "Haiku 4.5"},
	{ID: "claude-fable-5", Label: "Fable 5"},
}

// ExampleClaudeModelID is the id every piece of help text and every refusal
// names when it needs to show one. A constant rather than a literal repeated
// at each site: a message that advertises an id ValidateClaudeModel rejects is
// worse than one that shows none. TestTheExampleModelIsOneWeAccept holds it to
// that.
const ExampleClaudeModelID = "claude-haiku-4-5"

// ClaudeModel is one selectable remote model: the wire id and the name a
// human reads.
type ClaudeModel struct {
	ID    string
	Label string
}

// ClaudeModelPrefix is what makes an id a REMOTE one. The split matters
// because the two backends validate differently: the Claude side is a closed
// set known at compile time, while the local side is whatever Lemonade has
// downloaded — only the agent can answer that, so local ids are never
// refused here.
const ClaudeModelPrefix = "claude-"

// IsClaudeModelID reports whether an id addresses the Claude backend at all.
func IsClaudeModelID(id string) bool {
	return strings.HasPrefix(id, ClaudeModelPrefix)
}

// KnownClaudeModel returns the entry for id, and whether it is one this build
// accepts.
func KnownClaudeModel(id string) (ClaudeModel, bool) {
	for _, m := range ClaudeModels {
		if m.ID == id {
			return m, true
		}
	}
	return ClaudeModel{}, false
}

// ClaudeModelIDs lists every accepted id, in display order.
func ClaudeModelIDs() []string {
	ids := make([]string, 0, len(ClaudeModels))
	for _, m := range ClaudeModels {
		ids = append(ids, m.ID)
	}
	return ids
}

// ValidateClaudeModel refuses an id this build does not know.
//
// The empty string is accepted and means "let the agent pick its own
// default" — that is what --claude-model "" is documented to do, and it is a
// real choice, not a missing value.
func ValidateClaudeModel(id string) error {
	if id == "" {
		return nil
	}
	if _, ok := KnownClaudeModel(id); ok {
		return nil
	}
	return fmt.Errorf(
		"unknown Claude model %q. Accepted ids: %s. Note there is no date "+
			"suffix — it is `%s`, not `%s-20250101`",
		id, strings.Join(ClaudeModelIDs(), ", "),
		ExampleClaudeModelID, ExampleClaudeModelID)
}

// ClaudeModelShortName turns a wire id into the name the header chip shows:
// "claude-haiku-4-5" -> "haiku-4.5".
//
// Derived from the id rather than read off ClaudeModels' Label, so it still
// produces something honest for an id this build has never heard of — which
// is exactly the case a header has to render (the agent may be newer than the
// TUI, and its model-state ping is authoritative over this list).
func ClaudeModelShortName(id string) string {
	short := strings.TrimPrefix(id, ClaudeModelPrefix)
	if short == "" {
		return id
	}
	// Anthropic spells the minor version with a dash on the wire
	// ("haiku-4-5") and a dot everywhere a human reads it ("Haiku 4.5").
	// Only the version tail is rewritten: the family name keeps its dashes.
	parts := strings.Split(short, "-")
	for i := len(parts) - 1; i > 0; i-- {
		if !isDigits(parts[i]) || !isDigits(parts[i-1]) {
			break
		}
		parts[i-1] = parts[i-1] + "." + parts[i]
		parts = parts[:i]
	}
	return strings.Join(parts, "-")
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
