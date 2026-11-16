// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"strings"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// Hybrid config reads, vars so tests need no config file.
var (
	readHybridModels  = lemonade.ReadHybridModels
	hybridLocalRefuse = lemonade.HybridLocalOnlyRefusal
)

// withHybrid puts hybrid at the head of the gateway's list, as `gaia gateway
// use hybrid` offers it: a row for the router over two of the gateway's own
// models. It can't be chosen while its settings are broken, Local only refuses
// it, or the gateway doesn't list one of its gateway models; the row says why.
func withHybrid(gateway []lemonade.Entry) []lemonade.Entry {
	if len(gateway) == 0 {
		return gateway
	}
	return append([]lemonade.Entry{hybridEntry(gateway)}, gateway...)
}

func hybridEntry(gateway []lemonade.Entry) lemonade.Entry {
	rec := &lemonade.Recommended{ID: lemonade.HybridModelID, Provider: "amd", Label: "Hybrid"}
	e := lemonade.Entry{
		Model:       lemonade.Model{ID: lemonade.HybridModelID, Recipe: "cloud"},
		Recommended: rec,
		Listed:      true,
		Fits:        true,
	}
	h, err := readHybridModels()
	if err != nil {
		e.Listed = false
		e.Reason = strings.TrimSuffix(err.Error(), ".") + ". Fix it in /hybrid"
		return e
	}
	rec.Label = lemonade.HybridDisplayName(h) + " " + lemonade.HybridWhere(h)
	rec.Note = "Each turn runs on " + h.Cheap + " or " + h.Strong + ", picked by " + h.Classifier +
		". /hybrid changes the models and policy."
	if err := hybridLocalRefuse(); err != nil {
		e.Listed = false
		e.Reason = err.Error()
		return e
	}
	for _, r := range h.Roles() {
		if !strings.HasPrefix(r.Model, "amd.") || listedIn(gateway, r.Model) {
			continue
		}
		e.Listed = false
		e.Reason = "the gateway doesn't list " + r.Model + ", hybrid's " + r.Role +
			" model. Choose another in /hybrid"
		return e
	}
	return e
}

func listedIn(entries []lemonade.Entry, id string) bool {
	for _, e := range entries {
		if strings.EqualFold(e.Model.ID, id) {
			return true
		}
	}
	return false
}
