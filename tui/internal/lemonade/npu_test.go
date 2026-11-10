// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Lemonade /system-info bodies for the two NPU target PCs, plus one without.
const (
	haloNPU    = `{"OS Version":"Linux-7.0.0-31-generic","Physical Memory":"62.40 GB","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":64.0,"virtual_mem_gb":32.0}],"amd_npu":{"available":true,"family":"XDNA2"}},"model_storage":{"free_bytes":900000000000}}`
	laptopNPU  = `{"Physical Memory":"16 GB","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":0.5,"virtual_mem_gb":7.7}],"amd_npu":{"available":true}},"model_storage":{"free_bytes":200000000000}}`
	npuMissing = `{"Physical Memory":"64 GB","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":32.0}],"amd_npu":{"available":false}},"model_storage":{"free_bytes":900000000000}}`
)

func flmCatalog() []Model {
	return []Model{
		{ID: "qwen3.6-moe-35b-a3b-FLM", Recipe: "flm", Size: 22.1, Labels: []string{"vision", "tool-calling", "chat"}},
		{ID: "gemma4-it-e4b-FLM", Recipe: "flm", Size: 9.1, Labels: []string{"audio", "vision", "tool-calling", "chat"}},
		{ID: "gemma4-it-e2b-FLM", Recipe: "flm", Size: 6.0, Labels: []string{"chat"}},
	}
}

func entriesByID(entries []Entry) map[string]Entry {
	out := map[string]Entry{}
	for _, e := range entries {
		out[e.Model.ID] = e
	}
	return out
}

// Mirrors tests/unit/test_npu_twin_models.py: the NPU's memory is the RAM Linux
// sees, and the GPU pool is left as it was.
func TestNPUMemoryIsTheRAMLinuxSees(t *testing.T) {
	c := capacityOf(t, haloNPU)
	if c.NPUMemoryGB != 62.4 || c.MemorySource != "AMD iGPU" {
		t.Fatalf("got NPU %.1f GB, pool %q", c.NPUMemoryGB, c.MemorySource)
	}
	if npu, ok := c.NPU(); !ok || npu.MemoryGB != 62.4 || npu.MemorySource != "NPU, system RAM" {
		t.Fatalf("NPU view: %+v ok=%v", npu, ok)
	}
	if _, ok := capacityOf(t, npuMissing).NPU(); ok {
		t.Fatal("an unavailable NPU must have no NPU capacity")
	}
}

func TestOnlyGemmaIsRecommendedAsAnNPUModel(t *testing.T) {
	// Qwen3.6's FLM build was too slow per agent turn to recommend.
	want := map[string]bool{"gemma4-it-e4b-FLM": true}
	for _, r := range RecommendedFor("local") {
		if r.Device == "npu" && !want[r.ID] {
			t.Fatalf("unexpected NPU recommendation %s", r.ID)
		}
		delete(want, r.ID)
	}
	if len(want) != 0 {
		t.Fatalf("missing NPU recommendations: %v", want)
	}
}

func TestBigNPUPCIsOfferedGemma(t *testing.T) {
	by := entriesByID(BuildEntries("local", flmCatalog(), capacityOf(t, haloNPU), nil, verdict(true, true, true)))
	if e := by["gemma4-it-e4b-FLM"]; !e.Selectable() || !e.OnNPU() || e.Recommended == nil {
		t.Fatalf("Gemma NPU should be a selectable NPU recommendation: %+v", e)
	}
	if e := by["qwen3.6-moe-35b-a3b-FLM"]; e.Recommended != nil {
		t.Fatalf("Qwen NPU must not be recommended: %+v", e)
	}
}

func TestSmallNPULaptopIsOfferedGemmaOnly(t *testing.T) {
	by := entriesByID(BuildEntries("local", flmCatalog(), capacityOf(t, laptopNPU), nil, verdict(true, true, true)))
	if !by["gemma4-it-e4b-FLM"].Selectable() {
		t.Fatalf("Gemma NPU should fit 15.5 GB of NPU RAM: %+v", by["gemma4-it-e4b-FLM"])
	}
}

func TestNoNPUBlocksEveryFLMModelWithAReason(t *testing.T) {
	for _, e := range BuildEntries("local", flmCatalog(), capacityOf(t, npuMissing), nil, verdict(false, false, true)) {
		if !e.OnNPU() {
			continue
		}
		if e.Selectable() || e.Reason != "runs on a Ryzen AI NPU, and Lemonade reports none on this PC" {
			t.Fatalf("%s offered on a PC with no NPU: %+v", e.Model.ID, e)
		}
	}
}

// Downloaded skips the download checks, not the readiness check: on a ready
// NPU it stays selectable even when memory alone would refuse it, and the
// not-ready and unknown cases are covered in catalog_verdict_test.go.
func TestDownloadedFLMModelStaysSelectableOnAReadyNPU(t *testing.T) {
	models := flmCatalog()
	models[0].Downloaded = true
	tight := Capacity{MemoryGB: 40, MemorySource: "AMD iGPU", DiskFreeGB: 500, NPUMemoryGB: 8}
	by := entriesByID(BuildEntries("local", models, tight, nil, verdict(true, true, true)))
	if !by["qwen3.6-moe-35b-a3b-FLM"].Selectable() {
		t.Fatal("a downloaded model is never blocked by the fit check")
	}
}

// An NPU recommendation stays listed either way: on a PC with no NPU it is
// blocked with the absence reason, so the row explains itself instead of
// vanishing (the visible-when-unknown case is in catalog_verdict_test.go).
// Lemonade hides FLM models until its NPU backend is installed; Pull installs it.
func TestAnNPURecommendationIsListedWhetherOrNotThePCHasOne(t *testing.T) {
	for _, e := range BuildEntries("local", nil, capacityOf(t, npuMissing), nil, verdict(false, false, true)) {
		if e.Recommended == nil || e.Recommended.Device != "npu" {
			continue
		}
		if e.Selectable() || !strings.Contains(e.Reason, "reports none on this PC") {
			t.Fatalf("a PC with no NPU must show %s as blocked: %+v", e.Model.ID, e)
		}
	}
	by := entriesByID(BuildEntries("local", nil, capacityOf(t, haloNPU), nil, verdict(true, true, true)))
	for _, id := range []string{"gemma4-it-e4b-FLM"} {
		e, ok := by[id]
		if !ok || !e.Selectable() || !e.NeedsDownload() || e.Reason != "" {
			t.Fatalf("%s on an NPU PC without the backend: %+v", id, e)
		}
	}
}

func TestSingleTurnFLMBuildsAreNotChatOptions(t *testing.T) {
	if isChatModel(Model{ID: "gemma4e-flash-e4b-FLM", Recipe: "flm", Labels: []string{"vision", "single-turn", "chat"}}) {
		t.Fatal("a single-turn model cannot hold a conversation")
	}
	if !isChatModel(Model{ID: "gemma4-it-e4b-FLM", Recipe: "flm", Labels: []string{"audio", "vision", "chat"}}) {
		t.Fatal("the NPU twin must stay a chat option")
	}
}

// The NPU model is an option the user picks, never shown as a recommendation.
func TestNPUModelIsListedWithTheOtherModelsNotRecommended(t *testing.T) {
	gpu := Model{ID: "Gemma-4-E4B-it-GGUF", Size: 5.97, Labels: []string{"chat"}}
	entries := BuildEntries("local", append([]Model{gpu}, flmCatalog()...), capacityOf(t, haloNPU), nil, verdict(true, true, true))
	seenOther := false
	for _, e := range entries {
		if e.Model.ID == "gemma4-it-e4b-FLM" {
			if e.Featured() || !seenOther {
				t.Fatalf("Gemma NPU must sit with the other models: %+v", e)
			}
			return
		}
		if !e.Featured() {
			seenOther = true
		}
	}
	t.Fatal("Gemma NPU missing")
}

// The switch check must agree with the picker: an omni NPU model is chat.
func TestDownloadedOmniNPUModelIsALocalChatModel(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"data":[`+
			`{"id":"gemma4-it-e4b-FLM","recipe":"flm","downloaded":true,"labels":["audio","vision","tool-calling","chat"]},`+
			`{"id":"Whisper-Large-v3-Turbo","recipe":"whispercpp","downloaded":true,"labels":["audio","transcription"]},`+
			`{"id":"user.custom-GGUF","recipe":"llamacpp","downloaded":true}]}`)
	}))
	defer s.Close()
	models, err := New(s.URL+"/v1").Models(context.Background(), "local")
	if err != nil {
		t.Fatal(err)
	}
	var ids []string
	for _, m := range models {
		ids = append(ids, m.ID)
	}
	if strings.Join(ids, ",") != "gemma4-it-e4b-FLM,user.custom-GGUF" {
		t.Fatalf("local chat models = %v", ids)
	}
}

func TestNPUModelPullsByNameOnly(t *testing.T) {
	// Lemonade lists *-FLM models from `flm list` as built-ins: a recipe or
	// checkpoint turns the pull into a registration that 400s (#1655).
	for _, r := range RecommendedFor("local") {
		if r.Device != "npu" {
			continue
		}
		r := r
		body := PullRequest(Entry{Model: Model{ID: r.ID, Recipe: "flm"}, Recommended: &r})
		if len(body) != 2 || body["model_name"] != r.ID || body["stream"] != true {
			t.Fatalf("%s: pull must go by name only, got %v", r.ID, body)
		}
	}
}

// Mirrors tests/unit/test_flm_models.py: a Windows Lemonade reports installed
// RAM, so the carve-out comes off; Linux's MemTotal is already without it.
func TestNPUMemoryTakesTheCarveOutOffOnlyOnWindows(t *testing.T) {
	const apu = `{"OS Version":%q,"Physical Memory":"%s","devices":{"amd_igpu":{"available":true,"vram_gb":16.0},"amd_npu":{"available":true}}}`
	for _, tc := range []struct {
		os, physical string
		want         float64
	}{
		{"Windows 11 Pro 24H2", "64.00 GB", 48},
		{"Linux-6.14.0-ubuntu-24.04", "48.00 GB", 48},
		{"Windows 11 Pro 24H2", "16.00 GB", 0},
	} {
		if got := capacityOf(t, fmt.Sprintf(apu, tc.os, tc.physical)).NPUMemoryGB; got != tc.want {
			t.Errorf("%s, %s: NPU memory %.1f GB, want %.1f", tc.os, tc.physical, got, tc.want)
		}
	}
}
