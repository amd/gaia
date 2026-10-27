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
	haloNPU    = `{"Physical Memory":"128 GB","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":64.0,"virtual_mem_gb":32.0}],"amd_npu":{"available":true,"family":"XDNA2"}},"model_storage":{"free_bytes":900000000000}}`
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

// Mirrors tests/unit/test_npu_twin_models.py: the NPU's memory is RAM less the
// GPU carve-out, and the GPU pool is left as it was.
func TestNPUMemoryIsRAMLessTheCarveOut(t *testing.T) {
	c := capacityOf(t, haloNPU)
	if c.NPUMemoryGB != 64 || c.MemorySource != "AMD iGPU" {
		t.Fatalf("got NPU %.1f GB, pool %q", c.NPUMemoryGB, c.MemorySource)
	}
	if npu, ok := c.NPU(); !ok || npu.MemoryGB != 64 || npu.MemorySource != "NPU, system RAM" {
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
	by := entriesByID(BuildEntries("local", flmCatalog(), capacityOf(t, haloNPU), nil))
	if e := by["gemma4-it-e4b-FLM"]; !e.Selectable() || !e.OnNPU() || e.Recommended == nil {
		t.Fatalf("Gemma NPU should be a selectable NPU recommendation: %+v", e)
	}
	if e := by["qwen3.6-moe-35b-a3b-FLM"]; e.Recommended != nil {
		t.Fatalf("Qwen NPU must not be recommended: %+v", e)
	}
}

func TestSmallNPULaptopIsOfferedGemmaOnly(t *testing.T) {
	by := entriesByID(BuildEntries("local", flmCatalog(), capacityOf(t, laptopNPU), nil))
	if !by["gemma4-it-e4b-FLM"].Selectable() {
		t.Fatalf("Gemma NPU should fit 15.5 GB of NPU RAM: %+v", by["gemma4-it-e4b-FLM"])
	}
}

func TestNoNPUBlocksEveryFLMModelWithAReason(t *testing.T) {
	for _, e := range BuildEntries("local", flmCatalog(), capacityOf(t, npuMissing), nil) {
		if !e.OnNPU() {
			continue
		}
		if e.Selectable() || e.Reason != "runs on a Ryzen AI NPU, and Lemonade reports none on this PC" {
			t.Fatalf("%s offered on a PC with no NPU: %+v", e.Model.ID, e)
		}
	}
}

func TestDownloadedFLMModelStaysSelectable(t *testing.T) {
	models := flmCatalog()
	models[0].Downloaded = true
	by := entriesByID(BuildEntries("local", models, capacityOf(t, npuMissing), nil))
	if !by["qwen3.6-moe-35b-a3b-FLM"].Selectable() {
		t.Fatal("a downloaded model is never blocked by the fit check")
	}
}

// Lemonade hides FLM models until its NPU backend is installed.
func TestUnlistedNPUModelsAreHiddenWithoutAnNPUAndExplainedWithOne(t *testing.T) {
	for _, e := range BuildEntries("local", nil, capacityOf(t, npuMissing), nil) {
		if e.Recommended != nil && e.Recommended.Device == "npu" {
			t.Fatalf("a PC with no NPU was shown %s", e.Model.ID)
		}
	}
	by := entriesByID(BuildEntries("local", nil, capacityOf(t, haloNPU), nil))
	for _, id := range []string{"gemma4-it-e4b-FLM"} {
		e, ok := by[id]
		if !ok || e.Selectable() || e.Reason != "needs Lemonade's NPU backend; run `gaia init`" {
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
	entries := BuildEntries("local", append([]Model{gpu}, flmCatalog()...), capacityOf(t, haloNPU), nil)
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
