// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

// The catalog reads the plan's verdict, with the server's words for each
// problem, and a plan that cannot be read is unknown rather than absent.
func TestTheCatalogReadsThePlansVerdict(t *testing.T) {
	plan := setupapi.Plan{NPU: setupapi.NPU{
		Present: true,
		Problems: []setupapi.NPUProblem{{
			Code:  "memlock",
			Title: "Locked-memory limit is too low.",
			Fix:   "Raise the limit.",
		}},
	}}
	got := readNPUVerdictFrom(context.Background(), &setupapitest.Fake{P: plan})
	want := lemonade.NPUVerdict{Present: true, Known: true, Reasons: []string{"Locked-memory limit is too low. Raise the limit"}}
	if !got.Known || got.Present != want.Present || got.Ready != want.Ready || len(got.Reasons) != 1 || got.Reasons[0] != want.Reasons[0] {
		t.Fatalf("got %+v", got)
	}
}

// An unreadable plan must not read as an absent NPU: the verdict it produces
// says unknown, and a model it blocks must not say "reports none".
func TestAnUnreadablePlanIsUnknownNotAbsent(t *testing.T) {
	got := readNPUVerdictFrom(context.Background(), &setupapitest.Fake{PlanErr: errors.New("daemon down")})
	if got.Known {
		t.Fatal("a plan that failed to read is not known")
	}
	models := []lemonade.Model{{ID: "gemma4-it-e4b-FLM", Recipe: "flm", Size: 9.1, Labels: []string{"chat"}}}
	var row *lemonade.Entry
	entries := lemonade.BuildEntries("local", models, lemonade.Capacity{MemoryGB: 96, NPUMemoryGB: 64}, nil, got)
	for i, e := range entries {
		if e.Model.ID == "gemma4-it-e4b-FLM" {
			row = &entries[i]
		}
	}
	if row == nil || row.Selectable() {
		t.Fatalf("an unread plan must block the NPU model: %+v", row)
	}
	if strings.Contains(row.Reason, "reports none") || !strings.Contains(row.Reason, "could not be checked") {
		t.Fatalf("want an uncertainty reason, got %q", row.Reason)
	}
}
