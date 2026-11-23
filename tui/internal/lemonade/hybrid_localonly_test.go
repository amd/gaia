// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func localOnlyOn(t *testing.T) {
	t.Helper()
	policy := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(policy, []byte(`{"network_policy":"local_ai_only"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_POLICY_FILE", policy)
}

func TestHybridCloudModelsCountEveryMemberOnce(t *testing.T) {
	cases := []struct {
		name string
		doc  map[string]any
		want []string
	}{
		{"defaults", nil, []string{DefaultHybridCheapModel, DefaultHybridStrongModel}},
		{"all local", map[string]any{
			"hybrid_cheap_model": "Gemma-4-E4B-it-GGUF", "hybrid_strong_model": "Qwen3.6-35B-A3B-GGUF",
		}, nil},
		{"cloud strong only", map[string]any{
			"hybrid_cheap_model": "Gemma-4-E4B-it-GGUF", "hybrid_strong_model": "amd.Claude-Opus-5-5",
		}, []string{"amd.Claude-Opus-5-5"}},
		// Python's check_local_only counts the classifier, so a local pair
		// labelled by a cloud model still sends each request off the PC.
		{"cloud classifier", map[string]any{
			"hybrid_cheap_model": "Gemma-4-E4B-it-GGUF", "hybrid_strong_model": "Qwen3.6-35B-A3B-GGUF",
			"hybrid_classifier_model": "fireworks.glm-5p3-flash",
		}, []string{"fireworks.glm-5p3-flash"}},
		{"claude", map[string]any{
			"hybrid_cheap_model": "Gemma-4-E4B-it-GGUF", "hybrid_strong_model": "claude-opus-5",
		}, []string{"claude-opus-5"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			useConfig(t, tc.doc)
			got, err := HybridCloudModels()
			if err != nil {
				t.Fatal(err)
			}
			if strings.Join(got, ",") != strings.Join(tc.want, ",") {
				t.Fatalf("cloud models = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestLocalOnlyAllowsAnAllLocalHybrid(t *testing.T) {
	useConfig(t, map[string]any{
		"hybrid_cheap_model": "Gemma-4-E4B-it-GGUF", "hybrid_strong_model": "Qwen3.6-35B-A3B-GGUF",
	})
	localOnlyOn(t)
	if err := HybridLocalOnlyRefusal(); err != nil {
		t.Fatalf("an all-local hybrid was refused: %v", err)
	}
}

func TestLocalOnlyRefusesAHybridWithACloudMember(t *testing.T) {
	useConfig(t, map[string]any{
		"hybrid_cheap_model": "Gemma-4-E4B-it-GGUF", "hybrid_strong_model": "amd.Claude-Opus-5-5",
	})
	localOnlyOn(t)
	err := HybridLocalOnlyRefusal()
	if err == nil || !strings.Contains(err.Error(), "Local only is on") ||
		!strings.Contains(err.Error(), "amd.Claude-Opus-5-5") || strings.Contains(err.Error(), "Gemma") {
		t.Fatalf("err = %v, want a Local only refusal naming only the cloud model", err)
	}
}

func TestHybridLocalOnlyRefusalIsNilWithLocalOnlyOff(t *testing.T) {
	useConfig(t, nil)
	t.Setenv("GAIA_POLICY_FILE", filepath.Join(t.TempDir(), "none.json"))
	if err := HybridLocalOnlyRefusal(); err != nil {
		t.Fatalf("Local only is off, yet hybrid was refused: %v", err)
	}
}

func TestHybridLocalOnlyRefusalReportsABrokenConfig(t *testing.T) {
	useConfig(t, map[string]any{"hybrid_cheap_model": "amd.X", "hybrid_strong_model": "amd.X"})
	localOnlyOn(t)
	if err := HybridLocalOnlyRefusal(); err == nil || !strings.Contains(err.Error(), "nothing to route between") {
		t.Fatalf("err = %v, want the config error, not a silent allow", err)
	}
}
