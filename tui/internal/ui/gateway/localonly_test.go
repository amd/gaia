// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

// Under Local only the gateway screen registers nothing, hands Lemonade no
// token, and leaves the default model alone.
func TestTheGatewayScreenSendsNothingUnderLocalOnly(t *testing.T) {
	dir := t.TempDir()
	policy := filepath.Join(dir, "policy.json")
	if err := os.WriteFile(policy, []byte(`{"network_policy":"local_ai_only"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_POLICY_FILE", policy)
	t.Setenv("GAIA_HOME", dir)
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("LEMONADE_API_KEY", "")
	t.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	cfg := filepath.Join(dir, "config.json")
	if err := os.WriteFile(cfg, []byte(`{"default_model":"Gemma-4-E4B-it-GGUF"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_CONFIG_FILE", cfg)

	var posts atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			posts.Add(1)
		}
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := NewClientAt(srv.URL + "/api/v1")
	if _, err := c.Install(DefaultBaseURL); err == nil || !strings.Contains(err.Error(), "Local only is on") {
		t.Errorf("Install: %v", err)
	}
	if _, err := c.SetToken("gw-secret", DefaultBaseURL); err == nil ||
		!strings.Contains(err.Error(), "Local only is on") || strings.Contains(err.Error(), "gw-secret") {
		t.Errorf("SetToken: %v", err)
	}
	if n := posts.Load(); n != 0 {
		t.Errorf("%d POSTs reached Lemonade under Local only", n)
	}
	if err := setDefaultModel("amd.DeepSeek-V4.1-Flash"); err == nil || !strings.Contains(err.Error(), "Local only is on") {
		t.Errorf("setDefaultModel: %v", err)
	}
	raw, _ := os.ReadFile(cfg)
	if !strings.Contains(string(raw), "Gemma-4-E4B-it-GGUF") {
		t.Errorf("default_model changed: %s", raw)
	}
}
