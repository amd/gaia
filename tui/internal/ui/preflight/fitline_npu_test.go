// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/setupapi"
)

// An FLM chat model is judged against the NPU's system RAM, not the GPU pool.
func TestFitLineJudgesAnNPUModelOnNPUMemory(t *testing.T) {
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	// A big GPU pool but only 8 GB of NPU RAM: the GPU must not vouch for it.
	small := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 8}
	if got := fitLine(small, setUpNPU(), true, chat, true); !strings.Contains(got, "will not fit") || !strings.Contains(got, "NPU, system RAM") {
		t.Fatalf("got %q", got)
	}
	big := lemonade.Capacity{MemoryGB: 8, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 64}
	if got := fitLine(big, setUpNPU(), true, chat, true); !strings.Contains(got, "fits") || !strings.Contains(got, "68.7 GB") {
		t.Fatalf("got %q", got)
	}
	none := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1}
	if got := fitLine(none, setupapi.NPU{}, true, chat, true); !strings.Contains(got, "reports none") {
		t.Fatalf("got %q", got)
	}
}

// The server-start window follows the NPU model: 64K for the twin, 32K for any
// other FLM build, as lemonade_client.npu_default_ctx does.
func TestServerWindowFollowsTheNPUModel(t *testing.T) {
	probe := func(config string) hostProbe {
		return hostProbe{
			getenv:   func(string) string { return "" },
			homeDir:  func() (string, error) { return "/home/u", nil },
			readFile: func(string) ([]byte, error) { return []byte(config), nil },
		}
	}
	cases := map[string]int{
		`{"default_device":"npu","default_model":"gemma4-it-e4b-FLM"}`:   65536,
		`{"default_device":"npu","default_model":"gemma4-it-e2b-FLM"}`:   32768,
		`{"default_device":"npu"}`:                                       32768,
		`{"default_device":"gpu","default_model":"Gemma-4-E4B-it-GGUF"}`: 65536,
	}
	for config, want := range cases {
		if got := profileCtxSize(probe(config)); got != want {
			t.Errorf("%s: window %d, want %d", config, got, want)
		}
	}
}

// A PC whose NPU is there but not set up: fitLine must say so, with the
// server's own reasons, rather than judging the model by memory alone.
func TestFitLineReportsAnNPUThatIsNotSetUp(t *testing.T) {
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	big := lemonade.Capacity{MemoryGB: 8, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 64}
	npu := setupapi.NPU{
		Present: true,
		Ready:   false,
		Problems: []setupapi.NPUProblem{{
			Code:  "memlock",
			Title: "Locked-memory limit is too low.",
			Fix:   "Raise the limit",
		}},
	}
	got := fitLine(big, npu, true, chat, true)
	if !strings.Contains(got, "not set up yet") {
		t.Fatalf("want not set up yet, got %q", got)
	}
	if !strings.Contains(got, "Locked-memory limit is too low") || !strings.Contains(got, "Raise the limit") {
		t.Fatalf("want the server's reasons verbatim, got %q", got)
	}
	if strings.Contains(got, "fits:") {
		t.Fatalf("must not claim it fits, got %q", got)
	}
}

// An unread plan must not drop the memory verdict Lemonade already gave. It
// keeps the fit decision and says readiness was not checked.
func TestFitLineKeepsTheMemoryVerdictWhenReadinessIsUnknown(t *testing.T) {
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	big := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 64}
	got := fitLine(big, setupapi.NPU{}, false, chat, true)
	if !strings.Contains(got, "fits:") || !strings.Contains(got, "could not be checked") {
		t.Fatalf("want a memory verdict with an unreadiness caveat, got %q", got)
	}
	for _, claim := range []string{"reports none", "not set up"} {
		if strings.Contains(got, claim) {
			t.Fatalf("an unread plan must not claim %q, got %q", claim, got)
		}
	}
}

// With no memory figure either, an unread plan must not read as a confirmed
// absence: the zero NPU carries no information, so the line says it could not
// check instead.
func TestFitLineDoesNotClaimAbsenceWhenThePlanIsUnreadable(t *testing.T) {
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	none := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1}
	got := fitLine(none, setupapi.NPU{}, false, chat, true)
	if !strings.Contains(got, "could not be checked") {
		t.Fatalf("want could not be checked, got %q", got)
	}
	for _, claim := range []string{"reports none", "not set up", "fits", "will not fit"} {
		if strings.Contains(got, claim) {
			t.Fatalf("an unread plan must not claim %q, got %q", claim, got)
		}
	}
}

// No NPU at all reads as none, not as "not set up": a missing NPU is not a fix.
func TestFitLineSaysNoNPUWhenNoneIsReported(t *testing.T) {
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	none := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1}
	got := fitLine(none, setupapi.NPU{}, true, chat, true)
	if !strings.Contains(got, "reports none") {
		t.Fatalf("want reports none, got %q", got)
	}
	if strings.Contains(got, "not set up") {
		t.Fatalf("a missing NPU is not an unset one, got %q", got)
	}
}

// A ready NPU is judged on its memory as before: the readiness check must not
// turn a model that runs into one that does not.
func TestFitLineJudgesAReadyNPUOnMemory(t *testing.T) {
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	big := lemonade.Capacity{MemoryGB: 8, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 64}
	ready := setupapi.NPU{Present: true, Ready: true}
	if got := fitLine(big, ready, true, chat, true); !strings.Contains(got, "fits:") {
		t.Fatalf("want fits, got %q", got)
	}
	small := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 8}
	if got := fitLine(small, ready, true, chat, true); !strings.Contains(got, "will not fit") {
		t.Fatalf("want will not fit, got %q", got)
	}
}

func setUpNPU() setupapi.NPU { return setupapi.NPU{Present: true, Ready: true} }
