// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

func isolatedConnection(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("GAIA_HOME", "")
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("LEMONADE_API_KEY", "")
	return filepath.Join(home, ".gaia")
}

func writeEmbeddedState(t *testing.T, dir string, port int, key string) {
	t.Helper()
	dir = filepath.Join(dir, "lemonade")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(EmbeddedState{Port: port, APIKey: key})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "state.json"), body, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestEmbeddedConnectionHonorsIsolatedRuntimeAndExplicitOverrides(t *testing.T) {
	defaultDir := isolatedConnection(t)
	writeEmbeddedState(t, defaultDir, 63207, "default-instance-key")
	if ResolveBaseURL("") != "http://localhost:63207/api/v1" || APIKeyFor("") != "default-instance-key" {
		t.Fatal("default embedded connection was not resolved")
	}
	dir := t.TempDir()
	t.Setenv("GAIA_HOME", dir)
	if ReadEmbedded() != nil || ResolveBaseURL("") != DefaultBaseURL || APIKeyFor("") != "" {
		t.Fatal("empty isolated runtime reused the user's unrelated instance")
	}
	writeEmbeddedState(t, dir, 63208, "isolated-instance-key")
	if ResolveBaseURL("") != "http://localhost:63208/api/v1" || APIKeyFor("") != "isolated-instance-key" {
		t.Fatal("GAIA_HOME was not used for the connection")
	}
	t.Setenv("LEMONADE_BASE_URL", "https://router.example/api/v1/")
	if ResolveBaseURL("") != "https://router.example/api/v1" || APIKeyFor("") != "" {
		t.Fatal("explicit server did not win, or inherited the embedded key")
	}
	t.Setenv("LEMONADE_API_KEY", " explicit-key ")
	if APIKeyFor("") != "explicit-key" {
		t.Fatal("explicit credential did not win")
	}
	if ResolveBaseURL("http://localhost:62300/api/v1/") != "http://localhost:62300/api/v1" {
		t.Fatal("the agent's selected server was replaced by environment discovery")
	}
}

func TestEmbeddedCredentialOnlyBelongsToRecordedEndpoint(t *testing.T) {
	dir := isolatedConnection(t)
	writeEmbeddedState(t, dir, 63207, "private-router-key")
	for _, base := range []string{
		"http://localhost:63207/api/v1", "http://127.0.0.1:63207/api/v1", "http://[::1]:63207/api/v1",
	} {
		if APIKeyFor(base) != "private-router-key" {
			t.Errorf("matching endpoint %q did not receive its key", base)
		}
	}
	for _, base := range []string{
		"http://localhost:13305/api/v1", "http://127.0.0.2:63207/api/v1",
		"https://localhost:63207/api/v1", "https://router.example:63207/api/v1",
		"http://localhost.attacker.example:63207/api/v1", "http://user@localhost:63207/api/v1",
		"http://localhost:63207/api/v1?key=anything", "http://localhost:63207/api/v1#other",
		"://invalid",
	} {
		if APIKeyFor(base) != "" {
			t.Errorf("unrelated endpoint %q received the embedded key", base)
		}
	}
}

func TestLemonadeOriginsNormalizeWithoutChangingExplicitAPIPaths(t *testing.T) {
	isolatedConnection(t)
	for _, tc := range []struct{ input, want string }{
		{"http://localhost:13305", DefaultBaseURL},
		{"http://localhost:13305/", DefaultBaseURL},
		{"https://router.example", "https://router.example/api/v1"},
		{"http://localhost:13305/api/v1/", DefaultBaseURL},
		{"http://localhost:13305/v1/", "http://localhost:13305/v1"},
		{"https://router.example/lemonade/api/v1", "https://router.example/lemonade/api/v1"},
	} {
		t.Run(tc.input, func(t *testing.T) {
			t.Setenv("LEMONADE_BASE_URL", tc.input)
			if ResolveBaseURL("") != tc.want || New("").BaseURL != tc.want || New(tc.input).BaseURL != tc.want {
				t.Fatalf("client and shared resolver disagree for %q", tc.input)
			}
		})
	}
}

func TestInvalidEmbeddedStateDoesNotInventConnection(t *testing.T) {
	dir := isolatedConnection(t)
	for _, port := range []int{0, -1, 65536} {
		writeEmbeddedState(t, dir, port, "private-router-key")
		if ReadEmbedded() != nil || ResolveBaseURL("") != DefaultBaseURL || APIKeyFor("") != "" {
			t.Fatalf("invalid port %d supplied a connection", port)
		}
	}
	if err := os.WriteFile(filepath.Join(dir, "lemonade", "state.json"), []byte("{invalid"), 0o600); err != nil {
		t.Fatal(err)
	}
	if ReadEmbedded() != nil {
		t.Fatal("malformed embedded state was accepted")
	}
}

func TestCloudProviderClientUsesEmbeddedConnectionWithoutExportedCredentials(t *testing.T) {
	isolatedConnection(t)
	dir := t.TempDir()
	t.Setenv("GAIA_HOME", dir)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/system-info" || r.Header.Get("Authorization") != "Bearer isolated-router-key" {
			t.Error("provider client did not use embedded URL and credential")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"cloud":{"providers":[{"name":"fireworks","runtime_key_set":true}]}}`))
	}))
	defer server.Close()
	u, _ := url.Parse(server.URL)
	port, _ := strconv.Atoi(u.Port())
	writeEmbeddedState(t, dir, port, "isolated-router-key")
	providers, err := New("").Providers(context.Background())
	if err != nil || len(providers) != 1 || providers[0].Name != "fireworks" {
		t.Fatalf("embedded provider discovery failed: %v", err)
	}
}
