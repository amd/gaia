// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"math"
	"os"
	"path/filepath"
	"testing"
)

// A 16 GB Windows laptop with a 4 GB carve-out: 10 GB for models, all of it RAM.
const laptop16 = `{"Physical Memory":"16 GB","OS Version":"Windows 11 Pro","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":4.0,"virtual_mem_gb":6.0}]},"model_storage":{"free_bytes":500e9}}`

// A 12 GB graphics card in a 16 GB PC: the model sits in the card's memory.
const card12 = `{"Physical Memory":"16 GB","OS Version":"Windows 11 Pro","devices":{"amd_gpu":[{"available":true,"integrated":false,"vram_gb":12.0}]},"model_storage":{"free_bytes":500e9}}`

func TestInstalledRAMCountsALinuxCarveOutOnce(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		want       float64
	}{
		{"windows", laptop16, 16},
		{"linux", `{"Physical Memory":"28 GB","OS Version":"Ubuntu 24.04","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":4.0,"virtual_mem_gb":14.0}]}}`, 32},
		{"no os", `{"Physical Memory":"16 GB","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":4.0,"virtual_mem_gb":6.0}]}}`, 16},
	} {
		if got := capacityOf(t, tc.body).InstalledRAMGB; got != tc.want {
			t.Errorf("%s: installed RAM %v, want %v", tc.name, got, tc.want)
		}
	}
}

func TestAppHeadroomKeepsTenGBOrAQuarter(t *testing.T) {
	c := capacityOf(t, laptop16)
	if got := c.AppReserveGB(); got != 10 {
		t.Fatalf("16 GB laptop keeps %v GB, want 10", got)
	}
	if got := c.WithAppHeadroom().MemoryGB; math.Abs(got-6) > 1e-9 {
		t.Fatalf("16 GB laptop has %v GB for models after headroom, want 6", got)
	}
	big := Capacity{MemoryGB: 56, MemorySource: "AMD iGPU", InstalledRAMGB: 64}
	if got := big.AppReserveGB(); got != 16 {
		t.Fatalf("64 GB PC keeps %v GB, want 16 (a quarter)", got)
	}
	if got := capacityOf(t, card12).AppReserveGB(); got != 0 {
		t.Fatalf("a discrete GPU keeps %v GB of RAM back, want 0", got)
	}
}

// An 8 GB laptop has nothing left once other apps get their 10 GB, but the
// floor model is never tight: there is nothing smaller to suggest.
func TestTheFloorModelIsNeverTight(t *testing.T) {
	laptop8 := `{"Physical Memory":"8 GB","OS Version":"Windows 11 Pro","devices":{"amd_gpu":[{"available":true,"integrated":true,"vram_gb":0.5,"virtual_mem_gb":3.75}]},"model_storage":{"free_bytes":500e9}}`
	for _, e := range BuildEntries("local", []Model{{ID: "Qwen3.5-2B-GGUF", Recipe: "llamacpp"}}, capacityOf(t, laptop8), nil, NPUVerdict{}) {
		if e.Model.ID == "Qwen3.5-2B-GGUF" && (!e.Fits || e.Tight) {
			t.Errorf("floor on an 8 GB laptop: fits=%v tight=%v (%s)", e.Fits, e.Tight, e.Reason)
		}
	}
}

// On a 16 GB laptop Gemma and the 4B load but are tight; the 2B is not.
// Mirrors tests/unit/chat/ui/test_setup_plan.py's 16 GB laptop.
func TestTightModelsOnA16GBLaptop(t *testing.T) {
	models := []Model{
		{ID: "Gemma-4-E4B-it-GGUF", Recipe: "llamacpp"},
		{ID: "Qwen3.5-4B-GGUF", Recipe: "llamacpp"},
		{ID: "Qwen3.5-2B-GGUF", Recipe: "llamacpp"},
	}
	want := map[string]bool{"Gemma-4-E4B-it-GGUF": true, "Qwen3.5-4B-GGUF": true, "Qwen3.5-2B-GGUF": false}
	for _, e := range BuildEntries("local", models, capacityOf(t, laptop16), nil, NPUVerdict{}) {
		w, ok := want[e.Model.ID]
		if !ok {
			continue
		}
		if !e.Fits || e.Tight != w {
			t.Errorf("%s: fits=%v tight=%v, want fits and tight=%v (%s)", e.Model.ID, e.Fits, e.Tight, w, e.Reason)
		}
	}
	for _, e := range BuildEntries("local", models, capacityOf(t, card12), nil, NPUVerdict{}) {
		if e.Tight {
			t.Errorf("%s is tight on a 12 GB card, whose memory is its own", e.Model.ID)
		}
	}
}

// The native agent starts on GAIA's local chat model, never a cloud, gateway or
// Claude one, which Lemonade cannot load.
func TestLocalChatModelSkipsCloudAndClaude(t *testing.T) {
	for _, tc := range []struct{ config, want string }{
		{`{"default_model":"Qwen3.5-2B-GGUF"}`, "Qwen3.5-2B-GGUF"},
		{`{"last_model":"Qwen3.5-4B-GGUF","default_model":"Qwen3.5-2B-GGUF"}`, "Qwen3.5-4B-GGUF"},
		{`{"default_model":"claude-sonnet-4-6"}`, ""},
		{`{"last_model":"fireworks.glm-5p3-flash"}`, ""},
	} {
		path := filepath.Join(t.TempDir(), "config.json")
		if err := os.WriteFile(path, []byte(tc.config), 0o600); err != nil {
			t.Fatal(err)
		}
		t.Setenv("GAIA_CONFIG_FILE", path)
		if got := LocalChatModel(); got != tc.want {
			t.Errorf("%s: LocalChatModel() = %q, want %q", tc.config, got, tc.want)
		}
	}
}
