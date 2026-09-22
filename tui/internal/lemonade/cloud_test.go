// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package lemonade

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestConfigureKeepsKeyOutOfInstallAndDiscoversModels(t *testing.T) {
	var calls []string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/v1")
		calls = append(calls, path)
		if r.Header.Get("Authorization") != "Bearer local-key" {
			t.Error("missing Lemonade authorization")
		}
		var body map[string]any
		if r.Method == "POST" {
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
		}
		switch path {
		case "/install":
			if body["api_key"] != nil || body["backend"] != "cloud" || body["base_url"] != FireworksURL {
				t.Errorf("incorrect install body: %v", body)
			}
			fmt.Fprint(w, `{}`)
		case "/cloud/auth":
			if body["api_key"] != "secret-test" || body["provider"] != "fireworks" {
				t.Error("incorrect credential handoff")
			}
			fmt.Fprint(w, `{}`)
		case "/models":
			fmt.Fprint(w, `{"data":[{"id":"fireworks.gemma-4-31b-it","recipe":"cloud","downloaded":false},{"id":"local","downloaded":true},{"id":"fireworks.embed","recipe":"cloud","labels":["embeddings"]}]}`)
		}
	}))
	defer s.Close()
	t.Setenv("LEMONADE_API_KEY", "local-key")
	c := New(s.URL + "/v1")
	if err := c.Configure(context.Background(), Provider{Name: "fireworks", BaseURL: FireworksURL, Header: "Authorization", Prefix: "Bearer "}, "secret-test"); err != nil {
		t.Fatal(err)
	}
	models, err := c.Models(context.Background(), "fireworks")
	if err != nil || len(models) != 1 || models[0].ID != FireworksModel {
		t.Fatalf("models=%v err=%v", models, err)
	}
	if strings.Join(calls, ",") != "/install,/cloud/auth,/models" {
		t.Fatal(calls)
	}
}
func TestErrorsNeverReflectProviderBodyOrFollowRedirects(t *testing.T) {
	for _, status := range []int{301, 400, 401, 403, 404, 409, 500} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Location", "https://example.com/key")
				w.WriteHeader(status)
				fmt.Fprint(w, "secret-reflected-value")
			}))
			defer s.Close()
			err := New(s.URL).Clear(context.Background(), "fireworks")
			if err == nil || strings.Contains(err.Error(), "secret-reflected-value") {
				t.Fatalf("unsafe error %v", err)
			}
		})
	}
}
func TestProviderURLValidation(t *testing.T) {
	for _, u := range []string{"http://gateway.example/v1", "http://localhost/v1", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret", "file:///tmp/key"} {
		if validateURL(u, false) == nil {
			t.Errorf("accepted %s", u)
		}
	}
	if validateURL("https://gateway.example/v1", false) != nil {
		t.Fatal("HTTPS gateway rejected")
	}
	if validateURL("https://gateway.example/v1", true) == nil {
		t.Fatal("remote administration accepted")
	}
}
func TestLocalCatalogExcludesCloudAndNonChat(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"data":[{"id":"Gemma-4","downloaded":true},{"id":"fireworks.gemma","downloaded":true},{"id":"qwen","downloaded":false},{"id":"embed","downloaded":true,"labels":["embeddings"]}]}`)
	}))
	defer s.Close()
	models, err := New(s.URL).Models(context.Background(), "local")
	if err != nil || len(models) != 1 || models[0].ID != "Gemma-4" {
		t.Fatalf("%v %v", models, err)
	}
}

func TestAMDGatewayCustomHeaderAndRuntimeKey(t *testing.T) {
	var installed, authenticated, cleared bool
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if r.Method == http.MethodPost {
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
				w.WriteHeader(http.StatusBadRequest)
				return
			}
		}
		switch r.URL.Path {
		case "/api/v1/install":
			installed = body["provider"] == "amd" && body["base_url"] == "https://gateway.example/v1" && body["auth_header_name"] == "api-key" && body["auth_header_prefix"] == "" && body["wire_format"] == "openai" && body["api_key"] == nil
		case "/api/v1/cloud/auth":
			authenticated = body["provider"] == "amd" && body["api_key"] == "test-key"
		case "/api/v1/models":
			fmt.Fprint(w, `{"data":[{"id":"amd.gemma","recipe":"cloud","cloud_provider":"amd"},{"id":"fireworks.gemma","recipe":"cloud","cloud_provider":"fireworks"}]}`)
			return
		case "/api/v1/cloud/auth/amd":
			cleared = r.Method == http.MethodDelete
		default:
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		fmt.Fprint(w, `{}`)
	}))
	defer s.Close()
	c := New(s.URL)
	if err := c.Configure(context.Background(), Provider{Name: "amd", BaseURL: "https://gateway.example/v1", Header: "api-key"}, "test-key"); err != nil {
		t.Fatal(err)
	}
	models, err := c.Models(context.Background(), "amd")
	if err != nil || len(models) != 1 || models[0].ID != "amd.gemma" {
		t.Fatalf("AMD discovery failed: %v", err)
	}
	if err := c.Clear(context.Background(), "amd"); err != nil {
		t.Fatal(err)
	}
	if !installed || !authenticated || !cleared {
		t.Fatal("gateway configuration or key lifecycle failed")
	}
}
