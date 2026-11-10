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

// An unread plan is neither absence nor permission: the row stays, says it
// could not be checked, and is not selectable.
func TestUnknownVerdictKeepsTheRowUnselectableAndSaysSo(t *testing.T) {
	for _, e := range BuildEntries("local", flmCatalog(), capacityOf(t, haloNPU), nil, verdict(false, false, false)) {
		if !e.OnNPU() {
			continue
		}
		if e.Selectable() {
			t.Fatalf("%s offered while NPU readiness is unknown: %+v", e.Model.ID, e)
		}
		if !strings.Contains(e.Reason, "could not be checked") {
			t.Fatalf("%s want an uncertainty reason, got %q", e.Model.ID, e.Reason)
		}
		for _, claim := range []string{"reports none", "not ready", "not set up"} {
			if strings.Contains(e.Reason, claim) {
				t.Fatalf("an unread plan must not claim %q, got %q", claim, e.Reason)
			}
		}
	}
}

// An unknown verdict blocks a downloaded FLM model even while the machine's
// memory reads fine: the two are separate answers, and a readable PC says
// nothing about whether its NPU can run a model.
func TestUnknownVerdictBlocksADownloadedFLMModelOnAReadablePC(t *testing.T) {
	models := flmCatalog()
	models[0].Downloaded = true
	by := entriesByID(BuildEntries("local", models, capacityOf(t, haloNPU), nil, verdict(false, false, false)))
	e := by["qwen3.6-moe-35b-a3b-FLM"]
	if e.Selectable() {
		t.Fatalf("an unknown NPU verdict must block a downloaded model: %+v", e)
	}
	if !e.FitUnknown || !strings.Contains(e.Reason, "could not be checked") {
		t.Fatalf("want the NPU-readiness uncertainty reason, got %+v", e)
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
// reason, rather than being dropped: a row that vanishes cannot explain itself.
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
