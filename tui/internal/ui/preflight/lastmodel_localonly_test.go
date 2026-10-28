// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLocalOnlyKeepsACloudModelOutOfTheSavedChoice(t *testing.T) {
	dir := t.TempDir()
	policy := filepath.Join(dir, "policy.json")
	if err := os.WriteFile(policy, []byte(`{"network_policy":"local_ai_only"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_POLICY_FILE", policy)
	cfg := filepath.Join(dir, "config.json")
	t.Setenv("GAIA_CONFIG_FILE", cfg)

	for _, c := range [][2]string{{"fireworks", "fireworks.deepseek-v4p1-flash"}, {"amd", "amd.gpt-oss-120b"}, {"claude", "claude-opus-5"}} {
		if _, err := WriteLastModel(c[0], c[1]); err == nil || !strings.Contains(err.Error(), "Local only is on") {
			t.Errorf("%v: %v", c, err)
		}
	}
	if _, err := os.Stat(cfg); err == nil {
		t.Error("config.json was written under Local only")
	}
	if _, err := WriteLastModel("local", "Gemma-4-E4B-it-GGUF"); err != nil {
		t.Errorf("a local model was refused: %v", err)
	}
}
