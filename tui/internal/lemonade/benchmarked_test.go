// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"strings"
	"testing"
)

func TestBenchmarkedPicksAreValidFireworksIDsWithCopy(t *testing.T) {
	if len(Benchmarked) == 0 {
		t.Fatal("no benchmarked picks")
	}
	seen := map[string]bool{}
	for _, p := range Benchmarked {
		if !IsCloudID(p.ID) || !strings.HasPrefix(p.ID, "fireworks.") {
			t.Fatalf("%q is not a Fireworks cloud id", p.ID)
		}
		if p.ID == FireworksModel {
			t.Fatalf("%q is the suggested default, not a benchmarked pick", p.ID)
		}
		if seen[p.ID] {
			t.Fatalf("%q listed twice", p.ID)
		}
		seen[p.ID] = true
		for field, value := range map[string]string{"name": p.Name, "note": p.Note, "bench": p.Bench} {
			if strings.TrimSpace(value) == "" || strings.ContainsAny(value, "\r\n") {
				t.Fatalf("%s: %s must be one non-empty line", p.ID, field)
			}
		}
		got, ok := BenchmarkedPick(p.ID)
		if !ok || got != p {
			t.Fatalf("lookup of %q returned %+v, %v", p.ID, got, ok)
		}
	}
	if _, ok := BenchmarkedPick("fireworks.not-measured"); ok {
		t.Fatal("unmeasured id reported as benchmarked")
	}
}

func TestPickRankPutsSuggestedThenBenchmarkedThenRest(t *testing.T) {
	if PickRank(FireworksModel) != 0 {
		t.Fatal("suggested model must rank first")
	}
	last := 0
	for _, p := range Benchmarked {
		r := PickRank(p.ID)
		if r <= last {
			t.Fatalf("%q ranks %d, not after %d", p.ID, r, last)
		}
		last = r
	}
	if PickRank("fireworks.other") <= last || PickRank("amd.other") <= last {
		t.Fatal("unmeasured models must rank after every pick")
	}
}
