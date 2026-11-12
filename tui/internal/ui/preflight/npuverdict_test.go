// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

// The plan's verdict is what the line reads: an unready NPU reaches the text,
// and the plan being readable is reported as such.
func TestReadNPUVerdictComesFromThePlan(t *testing.T) {
	notReady := setupapi.NPU{
		Present:  true,
		Ready:    false,
		Problems: []setupapi.NPUProblem{{Code: "memlock", Title: "Locked-memory limit is too low.", Fix: "Raise the limit"}},
	}
	got, known := readNPUVerdictFrom(context.Background(), &setupapitest.Fake{P: setupapi.Plan{NPU: &notReady}})
	if !known {
		t.Fatal("a plan that answered is known")
	}
	if !got.Present || got.Ready || len(got.Problems) != 1 || got.Problems[0].Code != "memlock" {
		t.Fatalf("got %+v", got)
	}
}

// A plan that could not be read is reported as unknown, not as the zero NPU:
// the two mean different things to the line that shows them.
func TestReadNPUVerdictIsUnknownWhenThePlanFails(t *testing.T) {
	if _, known := readNPUVerdictFrom(context.Background(), &setupapitest.Fake{PlanErr: errors.New("daemon down")}); known {
		t.Fatal("a plan that failed to read is not known")
	}
}

// A daemon older than v0.25.4 answers without an npu field. That is a gap in
// the answer, not a report that the PC has no NPU, so it must stay unknown and
// keep the memory verdict the user already had.
func TestReadNPUVerdictIsUnknownWhenThePlanOmitsNPU(t *testing.T) {
	npu, known := readNPUVerdictFrom(context.Background(), &setupapitest.Fake{P: setupapi.Plan{}})
	if known {
		t.Fatal("a plan without an npu field is not a confirmed absence")
	}
	size := 9.1
	chat := gaiainit.Model{ID: "gemma4-it-e4b-FLM", SizeGB: &size}
	capacity := lemonade.Capacity{MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 64}
	got := fitLine(capacity, npu, known, chat, true)
	if !strings.Contains(got, "fits:") || !strings.Contains(got, "could not be checked") {
		t.Fatalf("want the memory verdict with a readiness caveat, got %q", got)
	}
	if strings.Contains(got, "reports none") {
		t.Fatalf("a plan without an npu field must not claim absence, got %q", got)
	}
}

// Only an FLM model needs the NPU verdict. A GPU model must not pay for a
// daemon attach, let alone start a cold one, to read an answer it ignores.
func TestLocalFitReadsTheNPUVerdictOnlyForAnNPUModel(t *testing.T) {
	size := 9.1
	for _, tc := range []struct {
		name     string
		id       string
		wantRead bool
	}{
		{"GGUF", "Gemma-4-E4B-it-GGUF", false},
		{"FLM", "gemma4-it-e4b-FLM", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			lemonadeUp(t, lemonade.Capacity{
				MemoryGB: 96, MemorySource: "AMD iGPU", DiskFreeGB: -1, NPUMemoryGB: 64,
			}, nil)
			reads := 0
			restore := readNPUVerdict
			t.Cleanup(func() { readNPUVerdict = restore })
			readNPUVerdict = func(context.Context) (setupapi.NPU, bool) {
				reads++
				return setupapi.NPU{Present: true, Ready: true}, true
			}
			chat := gaiainit.Model{ID: tc.id, Role: "chat", SizeGB: &size}
			got := (localRunner{}).localFit(t.Context(),
				Report{Rows: []Row{{Key: KeyLemonade, State: StateOK}}},
				[]gaiainit.Model{chat})
			if got == "" {
				t.Fatal("no fit line")
			}
			if tc.wantRead && reads != 1 {
				t.Fatalf("verdict reads = %d, want 1", reads)
			}
			if !tc.wantRead && reads != 0 {
				t.Fatalf("read the verdict %d times for a non-NPU model", reads)
			}
		})
	}
}
