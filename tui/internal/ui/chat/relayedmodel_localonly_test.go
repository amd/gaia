// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestARelayedSwitchToACloudModelIsRefusedUnderLocalOnly(t *testing.T) {
	policy := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(policy, []byte(`{"network_policy":"local_ai_only"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_POLICY_FILE", policy)
	// Refused before the switcher or Lemonade is asked anything, so nil is safe.
	for _, c := range [][2]string{{"fireworks", "fireworks.glm-5p3-flash"}, {"amd", "amd.gpt-oss-120b"}, {"local", "claude-opus-5"}} {
		_, err := checkRelayedSwitch(context.Background(), nil, "http://127.0.0.1:1/api/v1", c[0], c[1])
		if err == nil || !strings.Contains(err.Error(), "Local only is on") {
			t.Errorf("%v: %v", c, err)
		}
	}
}

func TestLocalOnlyRefusesARelayedSwitchToACloudHybridOnly(t *testing.T) {
	policy := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(policy, []byte(`{"network_policy":"local_ai_only"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_POLICY_FILE", policy)

	useHybridConfig(t, `{"hybrid_cheap_model":"`+localModel+`","hybrid_strong_model":"`+gatewayMdl+`"}`)
	_, err := checkRelayedSwitch(context.Background(), &relayClient{}, fakeLemonade(t), "hybrid", "hybrid")
	if err == nil || !strings.Contains(err.Error(), "Local only is on") || !strings.Contains(err.Error(), gatewayMdl) {
		t.Fatalf("err = %v, want a Local only refusal naming %s", err, gatewayMdl)
	}

	// Both models served on this PC: Local only has nothing to refuse.
	useHybridConfig(t, `{"hybrid_cheap_model":"`+localModel+`","hybrid_strong_model":"`+secondLocal+`"}`)
	base := fakeLemonadeWith(t, localModel, secondLocal)
	id, err := checkRelayedSwitch(context.Background(), &relayClient{}, base, "hybrid", "hybrid")
	if err != nil || id != "hybrid" {
		t.Fatalf("an all-local hybrid was refused under Local only: id=%q err=%v", id, err)
	}
}
