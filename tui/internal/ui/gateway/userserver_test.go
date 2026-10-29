// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// The gateway screen on a Lemonade the user runs: it may read the server, and
// must register, authenticate and unregister nothing there.
func TestTheGatewayScreenNeverChangesTheUsersServer(t *testing.T) {
	var (
		mu   sync.Mutex
		seen []string
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r.Method+" "+strings.TrimPrefix(r.URL.Path, "/api/v1"))
		mu.Unlock()
		switch strings.TrimPrefix(r.URL.Path, "/api/v1") {
		case "/system-info":
			fmt.Fprint(w, `{"cloud":{"providers":[{"name":"amd","base_url":"https://llm-api.amd.com/Unified/v1","runtime_key_set":true,"models_discovered":2}]}}`)
		case "/models":
			fmt.Fprint(w, `{"data":[{"id":"amd.DeepSeek-V4.1-Flash","recipe":"cloud"}]}`)
		default:
			fmt.Fprint(w, `{"models_discovered":2}`)
		}
	}))
	defer srv.Close()
	base := srv.URL + "/api/v1"

	home := t.TempDir()
	t.Setenv("GAIA_HOME", home)
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("LEMONADE_API_KEY", "")
	t.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	cfg := filepath.Join(home, "config.json")
	raw, _ := json.Marshal(map[string]any{"lemonade_mode": "external", "lemonade_base_url": base})
	if err := os.WriteFile(cfg, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GAIA_CONFIG_FILE", cfg)

	c := NewClientAt(base)
	if _, err := c.Install(DefaultBaseURL); err == nil || !strings.Contains(err.Error(), "register the AMD LLM Gateway") {
		t.Errorf("Install: %v", err)
	}
	if _, err := c.SetToken("secret", DefaultBaseURL); err == nil || strings.Contains(err.Error(), "secret") {
		t.Errorf("SetToken: %v", err)
	}
	if err := c.Uninstall(); err == nil {
		t.Error("Uninstall reached the user's server")
	}
	if err := c.ClearToken(); err == nil {
		t.Error("ClearToken reached the user's server")
	}
	// Reading still works, so an already-connected gateway is usable.
	if st, err := c.Status(); err != nil || !st.Installed {
		t.Errorf("Status: %+v %v", st, err)
	}

	mu.Lock()
	defer mu.Unlock()
	for _, s := range seen {
		method, path, _ := strings.Cut(s, " ")
		if lemonade.IsSetupChange(method, path) {
			t.Errorf("GAIA changed the user's server: %s", s)
		}
	}
}
