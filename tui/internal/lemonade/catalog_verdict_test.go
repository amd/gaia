// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"fmt"
	"strings"
	"testing"
)

// The plan's verdict decides whether an NPU model can run, in four states that
// must stay distinct. Downloaded status bypasses the download checks only: a
// model whose NPU the server says is not ready is blocked whether it is on
// disk or not, because a pull is not what would fix it.

const notReadyReason = "Locked-memory limit is too low. Raise the limit"

func verdict(present, ready, known bool, reasons ...string) NPUVerdict {
	return NPUVerdict{Present: present, Ready: ready, Known: known, Reasons: reasons}
}

// A ready NPU leaves the memory rule in charge, as before.
func TestReadyNPUIsJudgedOnMemory(t *testing.T) {
	by := entriesByID(BuildEntries("local", flmCatalog(), capacityOf(t, haloNPU), nil, verdict(true, true, true)))
	if e := by["gemma4-it-e4b-FLM"]; !e.Selectable() || !e.Fits {
		t.Fatalf("Gemma fits a ready NPU with room: %+v", e)
	}
	tight := Capacity{MemoryGB: 40, MemorySource: "AMD iGPU", DiskFreeGB: 500, NPUMemoryGB: 8}
	by = entriesByID(BuildEntries("local", flmCatalog(), tight, nil, verdict(true, true, true)))
	if e := by["gemma4-it-e4b-FLM"]; e.Selectable() || !strings.Contains(e.Reason, "NPU, system RAM") {
		t.Fatalf("9.1 GB must not fit 8 GB of NPU RAM: %+v", e)
	}
}

// The server's own reason is what the row says, and the model stays listed.
func TestNotReadyNPUBlocksWithTheServersReason(t *testing.T) {
	v := verdict(true, false, true, notReadyReason)
	for _, e := range BuildEntries("local", flmCatalog(), capacityOf(t, haloNPU), nil, v) {
		if !e.OnNPU() {
			continue
		}
		if e.Selectable() {
			t.Fatalf("%s offered on an NPU that is not ready: %+v", e.Model.ID, e)
		}
		if e.Reason != notReadyReason {
			t.Fatalf("%s want %q, got %q", e.Model.ID, notReadyReason, e.Reason)
		}
	}
}

// A downloaded model skips the download checks, never the readiness check.
func TestNotReadyNPUBlocksADownloadedFLMModel(t *testing.T) {
	models := flmCatalog()
	models[0].Downloaded = true
	v := verdict(true, false, true, notReadyReason)
	by := entriesByID(BuildEntries("local", models, capacityOf(t, haloNPU), nil, v))
	e := by["qwen3.6-moe-35b-a3b-FLM"]
	if e.Selectable() {
		t.Fatalf("a downloaded model on a not-ready NPU must be blocked: %+v", e)
	}
	if e.Reason != notReadyReason {
		t.Fatalf("want %q, got %q", notReadyReason, e.Reason)
	}
}

// Absence is a claim, and it reads as one, exactly as a missing NPU always has.
func TestAbsentNPUBlocksWithTheAbsenceReason(t *testing.T) {
	for _, e := range BuildEntries("local", flmCatalog(), capacityOf(t, npuMissing), nil, verdict(false, false, true)) {
		if !e.OnNPU() {
			continue
		}
		if e.Selectable() || !strings.Contains(e.Reason, "reports none on this PC") {
			t.Fatalf("%s: %+v", e.Model.ID, e)
		}
	}
}

// An unread plan is not absence: the row falls through to the memory rule and
// is selectable when that says it fits.
func TestUnknownVerdictJudgesAnNPUModelOnMemory(t *testing.T) {
	by := entriesByID(BuildEntries("local", flmCatalog(), capacityOf(t, haloNPU), nil, verdict(false, false, false)))
	if e := by["gemma4-it-e4b-FLM"]; !e.Selectable() || !e.Fits {
		t.Fatalf("an unknown verdict must leave the memory rule in charge: %+v", e)
	}
	tight := Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: 500, NPUMemoryGB: 8}
	by = entriesByID(BuildEntries("local", flmCatalog(), tight, nil, verdict(false, false, false)))
	e := by["gemma4-it-e4b-FLM"]
	if e.Selectable() || !strings.Contains(e.Reason, "NPU, system RAM") {
		t.Fatalf("an unknown verdict must still refuse memory that does not fit: %+v", e)
	}
	for _, claim := range []string{"reports none", "not ready", "not set up"} {
		if strings.Contains(e.Reason, claim) {
			t.Fatalf("an unread plan must not claim %q, got %q", claim, e.Reason)
		}
	}
}

// An unread plan must not take a downloaded model away: the file is already
// here, and the memory rule says it fits.
func TestUnknownVerdictKeepsADownloadedFLMModelSelectable(t *testing.T) {
	models := flmCatalog()
	models[0].Downloaded = true
	by := entriesByID(BuildEntries("local", models, capacityOf(t, haloNPU), nil, verdict(false, false, false)))
	e := by["qwen3.6-moe-35b-a3b-FLM"]
	if !e.Selectable() || e.FitUnknown {
		t.Fatalf("an unknown NPU verdict must keep a downloaded model selectable: %+v", e)
	}
}

// A ready NPU with unreadable memory is still ready: the verdict is a source of
// its own, and a downloaded model bypasses the memory check it cannot get.
// An unreadable machine keeps its GGUF rows exactly as they were, which the
// existing downloaded-model test pins; this fixes the FLM half of that pair.
func TestReadyNPUKeepsADownloadedFLMModelSelectableWhenMemoryIsUnreadable(t *testing.T) {
	models := flmCatalog()
	models[0].Downloaded = true
	by := entriesByID(BuildEntries("local", models, Capacity{}, fmt.Errorf("no system info"), verdict(true, true, true)))
	if e := by["qwen3.6-moe-35b-a3b-FLM"]; !e.Selectable() {
		t.Fatalf("a ready NPU vouches for the model even unread memory: %+v", e)
	}
}

// An NPU recommendation the catalog does not list stays visible with the
// reason when the NPU is present but not ready: a row that vanishes cannot
// explain itself.
func TestAnUnlistedNPURecommendationStaysWithItsReason(t *testing.T) {
	v := verdict(true, false, true, notReadyReason)
	by := entriesByID(BuildEntries("local", nil, capacityOf(t, haloNPU), nil, v))
	e, ok := by["gemma4-it-e4b-FLM"]
	if !ok {
		t.Fatal("an NPU recommendation on a not-ready NPU must stay listed")
	}
	if e.Selectable() || e.Reason != notReadyReason {
		t.Fatalf("want blocked with the server's reason, got %+v", e)
	}
}

// A confirmed-absent NPU is not an option to explain, so the unlisted
// recommendation goes away. A listed FLM row still shows the absence reason.
func TestAConfirmedAbsentNPUHidesAnUnlistedNPURecommendation(t *testing.T) {
	by := entriesByID(BuildEntries("local", nil, capacityOf(t, npuMissing), nil, verdict(false, false, true)))
	if e, ok := by["gemma4-it-e4b-FLM"]; ok {
		t.Fatalf("a PC with no NPU must not show the unlisted recommendation: %+v", e)
	}
}

// A downloaded GGUF is untouched: the verdict is about the NPU, not about
// every model on the PC.
func TestAVerdictLeavesNonNPUModelsAlone(t *testing.T) {
	gpu := Model{ID: "Gemma-4-E4B-it-GGUF", Size: 5.97, Labels: []string{"chat"}, Downloaded: true}
	for _, v := range []NPUVerdict{
		verdict(true, false, true, notReadyReason),
		verdict(false, false, true),
		verdict(false, false, false),
	} {
		by := entriesByID(BuildEntries("local", []Model{gpu}, capacityOf(t, haloNPU), nil, v))
		if e := by[gpu.ID]; !e.Selectable() {
			t.Fatalf("a downloaded GPU model must stay selectable on %+v: %+v", v, e)
		}
	}
}
