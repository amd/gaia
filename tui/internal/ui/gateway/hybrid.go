// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/filelock"
	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// newHybridAPI is the backend that names hybrid and says whether it can run.
// A var so tests reach no daemon.
var newHybridAPI = func() hybridapi.API { return hybridapi.New() }

// hybridEntryMsg is the backend's hybrid entry, or why there is none.
type hybridEntryMsg struct {
	entry hybridapi.Entry
	err   error
}

func fetchHybridEntry() tea.Cmd {
	return func() tea.Msg {
		e, err := newHybridAPI().Entry(context.Background())
		return hybridEntryMsg{entry: e, err: err}
	}
}

// rows is the model list as shown: hybrid first, then the gateway's models.
// Hybrid is offered only once the gateway lists something to route between.
func (m GatewayModel) rows() []Model {
	if len(m.models) == 0 {
		return nil
	}
	return append([]Model{{ID: lemonade.HybridModelID}}, m.models...)
}

// hybridRow is hybrid's line in the list.
func (m GatewayModel) hybridRow() string {
	switch {
	case m.hybridErr != nil:
		return "    Hybrid" + errorStyle.Render(" ("+m.hybridErr.Error()+")") + "\n"
	case m.hybrid == nil:
		return "    Hybrid" + dimStyle.Render(" (checking its settings…)") + "\n"
	}
	name := m.hybrid.Label()
	if why := m.hybrid.Why(); why != "" {
		name += errorStyle.Render(" (can't use: " + why + ")")
	}
	if m.defaultErr != nil {
		name += errorStyle.Render(" (can't tell if it is active: " + m.defaultErr.Error() + ")")
	}
	if lemonade.IsHybridID(m.defaultModel) {
		name = activeStyle.Render(name + " (active)")
	}
	return "    " + name + "\n      " +
		dimStyle.Render("routes each turn to the cheap or the strong model · /hybrid to change") + "\n"
}

// useHybrid makes hybrid GAIA's default model once the backend says every
// model it routes to can run.
func (m GatewayModel) useHybrid() GatewayModel {
	m.notice, m.errMsg = "", ""
	switch {
	case m.hybridErr != nil:
		m.errMsg = m.hybridErr.Error()
		return m
	case m.hybrid == nil:
		m.errMsg = "Still checking hybrid's settings; try again in a moment."
		return m
	}
	if why := m.hybrid.Why(); why != "" {
		m.errMsg = m.hybrid.Label() + " can't be used: " + why
		return m
	}
	if err := setDefaultModel(lemonade.HybridModelID); err != nil {
		m.errMsg = err.Error()
		return m
	}
	m.defaultModel = lemonade.HybridModelID
	m.notice = m.hybrid.Label() + " is now the default model; it routes each turn to the cheap or the strong model."
	return m
}

// readDefaultModel is config.json's default_model, "" when unset.
func readDefaultModel() (string, error) {
	path := gaiaConfigPath()
	raw, err := filelock.ReadFile(path)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("could not read GAIA config at %s: %w", path, err)
	}
	var doc struct {
		Default *string `json:"default_model"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return "", fmt.Errorf("GAIA config at %s is not valid JSON: %w. Fix or delete it", path, err)
	}
	if doc.Default == nil {
		return "", nil
	}
	return strings.TrimSpace(*doc.Default), nil
}
