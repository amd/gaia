// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package gaiaconnectors asks the `gaia` CLI which connectors exist and which
// are connected, and builds the optional "get to know me" prompt the TUI offers
// once one is.
//
// Like gaiaslack it shells out: src/gaia/connectors/ owns what "configured"
// means, and `gaia connectors list --json` exists so this can be a thin reader.
package gaiaconnectors

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// ListTimeout bounds the probe; it spawns a Python interpreter.
const ListTimeout = 30 * time.Second

// Binary resolves the `gaia` CLI on PATH; a var so tests can stub it.
var Binary = func() (string, error) {
	bin, err := exec.LookPath("gaia")
	if err != nil {
		return "", fmt.Errorf("GAIA is not fully installed on this PC, so " +
			"connectors cannot be listed. Open Connectors in the GAIA desktop " +
			"app, or see https://amd-gaia.ai/docs/guides/install")
	}
	return bin, nil
}

// Connector is one row of `gaia connectors list --json`.
type Connector struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Type        string `json:"type"`
	Category    string `json:"category"`
	Configured  bool   `json:"configured"`
	// Enabled is false for an MCP connector that is configured but switched off.
	// Absent from older CLIs, so it defaults to true.
	Enabled   *bool  `json:"enabled"`
	AccountID string `json:"account_id"`
}

// List runs `gaia connectors list --json`.
func List(ctx context.Context) ([]Connector, error) {
	bin, err := Binary()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, ListTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "connectors", "list", "--json")
	var out, errBuf bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errBuf
	if runErr := cmd.Run(); runErr != nil {
		return nil, fmt.Errorf("`gaia connectors list --json` failed (%w): %s",
			runErr, strings.TrimSpace(errBuf.String()))
	}
	return Parse(out.Bytes())
}

// Parse decodes the CLI's JSON, skipping any log lines printed before it.
func Parse(raw []byte) ([]Connector, error) {
	if i := bytes.IndexByte(raw, '['); i > 0 {
		raw = raw[i:]
	}
	var rows []Connector
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, fmt.Errorf("`gaia connectors list --json` returned something "+
			"that is not a connector list: %w", err)
	}
	return rows, nil
}

// Usable reports whether the connector is connected and switched on.
func (c Connector) Usable() bool {
	return c.Configured && (c.Enabled == nil || *c.Enabled)
}

// FlagshipAgentID is the agent a personalization chat runs on.
const FlagshipAgentID = "installed:gaia"

// Activate lets the flagship see an MCP connector's tools. They stay hidden
// until it is activated for the agent, so a pass run without this has nothing
// to read. OAuth connectors need nothing here.
func Activate(ctx context.Context, c Connector) error {
	if c.Type != "mcp_server" {
		return nil
	}
	bin, err := Binary()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, ListTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "connectors", "activations", "activate",
		c.ID, FlagshipAgentID, "--scopes", "use")
	var errBuf bytes.Buffer
	cmd.Stderr = &errBuf
	if runErr := cmd.Run(); runErr != nil {
		return fmt.Errorf("could not let GAIA use %s (`gaia connectors activations activate %s %s` "+
			"failed: %w): %s", c.DisplayName, c.ID, FlagshipAgentID, runErr, strings.TrimSpace(errBuf.String()))
	}
	return nil
}

// Find returns the connector whose id or display name matches, ignoring case.
func Find(rows []Connector, name string) (Connector, bool) {
	for _, c := range rows {
		if strings.EqualFold(c.ID, name) || strings.EqualFold(c.DisplayName, name) {
			return c, true
		}
	}
	return Connector{}, false
}

// Render is what /connectors prints.
func Render(rows []Connector) string {
	if len(rows) == 0 {
		return "No connectors are available in this GAIA install."
	}
	var b strings.Builder
	b.WriteString("Connectors\n\n")
	anyConnected := false
	for _, c := range rows {
		status := "not connected"
		switch {
		case c.Usable():
			anyConnected = true
			status = "connected"
			if c.AccountID != "" {
				status += " as " + c.AccountID
			}
		case c.Configured:
			status = "connected but switched off"
		}
		fmt.Fprintf(&b, "  %-24s %s\n", c.DisplayName+" ("+c.ID+")", status)
	}
	b.WriteString("\nConnect one from Connectors in the Agent UI.")
	if anyConnected {
		b.WriteString("\nOptional: /connectors personalize <id> lets GAIA read recent activity, " +
			"save what it learns to memory, and suggest automations.")
	}
	return b.String()
}

var sources = []struct{ prefix, what string }{
	{"google", "my last two weeks of Gmail (and Google Calendar, if it is connected)"},
	{"microsoft", "my last two weeks of Outlook mail (and calendar, if it is connected)"},
	{"slack", "my recent Slack channels and direct messages"},
	{"github", "my recent GitHub activity: open PRs, review requests, issues and notifications"},
}

// Source names what the personalization pass reads for a connector. It mirrors
// src/gaia/apps/webui/src/utils/connectorPersonalize.ts.
func Source(c Connector) string {
	id := strings.TrimPrefix(strings.ToLower(c.ID), "mcp-")
	for _, s := range sources {
		if strings.HasPrefix(id, s.prefix) {
			return s.what
		}
	}
	return "my recent activity in " + c.DisplayName
}

// PersonalizePrompt is the chat prompt for the optional personalization pass.
func PersonalizePrompt(c Connector) string {
	return strings.Join([]string{
		"I just connected " + c.DisplayName + ". Get to know me from " + Source(c) + ".",
		"Read only — do not send, delete, label or change anything.",
		"1. Skim the recent items and work out who I deal with most, what I work on, recurring meetings or threads, and what keeps landing on my plate.",
		"2. Save the durable facts to memory (people, projects, preferences, routines). Skip one-off details and anything sensitive like passwords, codes or financial numbers.",
		"3. Recommend up to five automations you could run for me on a schedule, each with what it does, how often, and why it fits what you saw.",
		"Don't create any schedules yet — tell me what you saved and list the automations so I can pick which to set up.",
	}, "\n")
}
