// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"context"
	"errors"
	"testing"

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
	got, known := readNPUVerdictFrom(context.Background(), &setupapitest.Fake{P: setupapi.Plan{NPU: notReady}})
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
