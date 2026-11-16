// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/amd/gaia/tui/internal/filelock"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// readHybridModels is a var so tests need no hybrid config.
var readHybridModels = lemonade.ReadHybridModels

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
	h, err := m.hybrid, m.hybridErr
	if err != nil {
		return "    Hybrid" + errorStyle.Render(" (settings can't be used: "+err.Error()+" — fix them in /hybrid)") + "\n"
	}
	name := lemonade.HybridDisplayName(h) + " " + lemonade.HybridWhere(h)
	if m.defaultErr != nil {
		name += errorStyle.Render(" (can't tell if it is active: " + m.defaultErr.Error() + ")")
	}
	if lemonade.IsHybridID(m.defaultModel) {
		name = activeStyle.Render(name + " (active)")
	}
	return "    " + name + "\n      " +
		dimStyle.Render("routes each turn to the cheap or the strong model · /hybrid to change") + "\n"
}

// useHybrid is `gaia gateway use hybrid`: hybrid becomes the default model once
// every gateway model it routes to is one the gateway lists.
func (m GatewayModel) useHybrid() GatewayModel {
	m.notice, m.errMsg = "", ""
	// Read again: /hybrid may have changed them since the screen opened.
	h, err := readHybridModels()
	m.hybrid, m.hybridErr = h, err
	if err != nil {
		m.errMsg = err.Error()
		return m
	}
	var missing []string
	seen := map[string]bool{}
	for _, id := range []string{h.Cheap, h.Strong, h.Classifier} {
		if seen[id] {
			continue
		}
		seen[id] = true
		// A model from another provider is checked when a session switches to it.
		if strings.HasPrefix(id, Provider+".") && !m.lists(id) {
			missing = append(missing, id)
		}
	}
	if len(missing) > 0 {
		m.errMsg = fmt.Sprintf("Hybrid needs %s, which the gateway doesn't list. "+
			"Choose models it does list in /hybrid, then try again.", strings.Join(missing, ", "))
		return m
	}
	if err := setDefaultModel(lemonade.HybridModelID); err != nil {
		m.errMsg = err.Error()
		return m
	}
	m.defaultModel = lemonade.HybridModelID
	m.notice = fmt.Sprintf("Hybrid is now the default model: each turn runs on %s or %s, picked by %s.",
		h.Cheap, h.Strong, h.Classifier)
	return m
}

func (m GatewayModel) lists(id string) bool {
	for _, model := range m.models {
		if strings.EqualFold(model.ID, id) {
			return true
		}
	}
	return false
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
