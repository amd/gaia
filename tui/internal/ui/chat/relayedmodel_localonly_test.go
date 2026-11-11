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
