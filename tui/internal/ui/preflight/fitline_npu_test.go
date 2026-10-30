// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// An FLM chat model is judged against the NPU's system RAM, not the GPU pool.
func TestFitLineJudgesAnNPUModelOnNPUMemory(t *testing.T) {
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	// A big GPU pool but only 8 GB of NPU RAM: the GPU must not vouch for it.
	small := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 8}
	if got := fitLine(small, chat, true); !strings.Contains(got, "will not fit") || !strings.Contains(got, "NPU, system RAM") {
		t.Fatalf("got %q", got)
	}
	big := lemonade.Capacity{MemoryGB: 8, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 64}
	if got := fitLine(big, chat, true); !strings.Contains(got, "fits") || !strings.Contains(got, "64 GB") {
		t.Fatalf("got %q", got)
	}
	none := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1}
	if got := fitLine(none, chat, true); !strings.Contains(got, "reports none") {
		t.Fatalf("got %q", got)
	}
}

// The server-start window follows the NPU model: 64K for the twin, 32K for any
// other FLM build, as lemonade_client.npu_ctx_ceiling does.
func TestServerWindowFollowsTheNPUModel(t *testing.T) {
	probe := func(config string) hostProbe {
		return hostProbe{
			getenv:   func(string) string { return "" },
			homeDir:  func() (string, error) { return "/home/u", nil },
			readFile: func(string) ([]byte, error) { return []byte(config), nil },
		}
	}
	cases := map[string]int{
		`{"default_device":"npu","default_model":"gemma4-it-e4b-FLM"}`:       65536,
		`{"default_device":"npu","default_model":"gemma4-it-e2b-FLM"}`:       32768,
		`{"default_device":"npu"}`:                                           32768,
		`{"default_device":"gpu","default_model":"Gemma-4-E4B-it-GGUF"}`:     65536,
	}
	for config, want := range cases {
		if got := profileCtxSize(probe(config)); got != want {
			t.Errorf("%s: window %d, want %d", config, got, want)
		}
	}
}
