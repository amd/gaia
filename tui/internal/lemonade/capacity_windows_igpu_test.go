// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
)

// windowsIGPU is how Lemonade on Windows reports a Strix Halo: no memory fields.
func windowsIGPU(physical string) string {
	p := ""
	if physical != "" {
		p = `"Physical Memory":"` + physical + `",`
	}
	return `{` + p + `"devices":{"amd_gpu":[{"available":true,"integrated":true,"family":"gfx1151","name":"AMD Radeon(TM) 8060S Graphics"}]},"model_storage":{"free_bytes":900000000000}}`
}

func withCarveOut(t *testing.T, gb float64) {
	t.Helper()
	orig := adapterMemoryGB
	adapterMemoryGB = func(string) float64 { return gb }
	t.Cleanup(func() { adapterMemoryGB = orig })
}

func largeDefault(t *testing.T) Recommended {
	t.Helper()
	for _, r := range RecommendedFor("local") {
		if r.ID == "Qwen3.6-35B-A3B-GGUF" {
			return r
		}
	}
	t.Fatal("Qwen3.6-35B-A3B-GGUF is not a local recommendation")
	return Recommended{}
}

// #5050: the BIOS carve-out is only part of what the iGPU loads into.
func TestWindowsIGPUPoolIsCarveOutPlusSharedRAM(t *testing.T) {
	qwen := largeDefault(t)
	for name, tc := range map[string]struct {
		carveOut float64
		physical string
		pool     float64
		fitsQwen bool
	}{
		"auto 512 MB on 128 GB": {0.5, "127.28 GB", 0.5 + 127.28/2, true},
		"4 GB on 128 GB":        {4, "123.8 GB", 4 + 123.8/2, true},
		"96 GB carve-out":       {96, "32 GB", 96 + 16, true},
		"16 GB laptop":          {0.5, "15.3 GB", 0.5 + 15.3/2, false},
	} {
		t.Run(name, func(t *testing.T) {
			withCarveOut(t, tc.carveOut)
			c := capacityOf(t, windowsIGPU(tc.physical))
			if c.MemorySource != "AMD iGPU" || math.Abs(c.MemoryGB-tc.pool) > 1e-9 {
				t.Fatalf("capacity %+v, want pool %v", c, tc.pool)
			}
			if ok, why := c.Fit(qwen.SizeGB, qwen.KVCacheGB()); ok != tc.fitsQwen {
				t.Fatalf("Qwen fits=%v (%s), want %v", ok, why, tc.fitsQwen)
			}
		})
	}
}

func TestWindowsIGPUCarveOutWithoutRAMIsAnError(t *testing.T) {
	withCarveOut(t, 4)
	for _, physical := range []string{"", "unknown", "0 GB"} {
		var info systemInfo
		if err := json.Unmarshal([]byte(windowsIGPU(physical)), &info); err != nil {
			t.Fatal(err)
		}
		_, err := capacityFrom(info)
		if err == nil || !strings.Contains(err.Error(), "gaia config set default_model") {
			t.Errorf("physical %q: err = %v", physical, err)
		}
	}
}

func TestReportedVRAMNeverReadsTheRegistry(t *testing.T) {
	orig := adapterMemoryGB
	adapterMemoryGB = func(string) float64 { t.Fatal("asked the registry with vram_gb reported"); return 0 }
	t.Cleanup(func() { adapterMemoryGB = orig })
	if c := capacityOf(t, strixHalo128); math.Abs(c.MemoryGB-(96+15.8)) > 1e-9 {
		t.Fatalf("capacity %+v", c)
	}
}

func TestAPoolUnder1GBNeverReadsAs0GB(t *testing.T) {
	for gb, want := range map[float64]string{0.5: "0.5", 0: "0", 1: "1", 63.6: "64"} {
		if got := formatGB(gb); got != want {
			t.Errorf("formatGB(%v) = %q, want %q", gb, got, want)
		}
	}
	c := Capacity{MemoryGB: 0.5, MemorySource: "AMD iGPU", DiskFreeGB: -1}
	if _, why := c.Fit(20, 0); !strings.Contains(why, "this PC has 0.5 GB") {
		t.Fatalf("reason %q", why)
	}
}
