// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// usersServer is a fake Lemonade the user runs. It records every request and
// answers each one, so a guard that fails open shows up as a recorded write.
type usersServer struct {
	*httptest.Server
	mu   sync.Mutex
	seen []string
	// providers is the /system-info cloud.providers JSON array.
	providers string
}

func newUsersServer(t *testing.T, providers string) *usersServer {
	t.Helper()
	u := &usersServer{providers: providers}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u.mu.Lock()
		u.seen = append(u.seen, r.Method+" "+strings.TrimPrefix(r.URL.Path, "/api/v1"))
		u.mu.Unlock()
		switch strings.TrimPrefix(r.URL.Path, "/api/v1") {
		case "/system-info":
			fmt.Fprintf(w, `{"cloud":{"providers":%s}}`, u.providers)
		case "/models":
			fmt.Fprint(w, `{"data":[{"id":"amd.DeepSeek-V4.1-Flash","recipe":"cloud"}]}`)
		default:
			fmt.Fprint(w, `{"models_discovered":3}`)
		}
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *usersServer) base() string { return u.URL + "/api/v1" }

// setupChanges is every recorded request that changed the server's setup.
func (u *usersServer) setupChanges() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	var out []string
	for _, s := range u.seen {
		method, path, _ := strings.Cut(s, " ")
		if IsSetupChange(method, path) {
			out = append(out, s)
		}
	}
	return out
}

const registeredAMD = `[{"name":"amd","base_url":"https://llm-api.amd.com/Unified/v1",` +
	`"auth_header_name":"Ocp-Apim-Subscription-Key","auth_header_prefix":"","runtime_key_set":true}]`

func amdProvider(base string) Provider {
	return Provider{Name: "amd", BaseURL: base, Header: AMDGatewayAuthHeader}
}

func TestExternalModeNeverChangesTheUsersServer(t *testing.T) {
	cases := []struct {
		name      string
		providers string
		provider  Provider
		key       string
		wantErr   string
	}{
		{"already registered, no key: nothing to do", registeredAMD, amdProvider(AMDGatewayURL), "", ""},
		{"already registered, trailing slash", registeredAMD, amdProvider(AMDGatewayURL + "/"), "", ""},
		{"not registered", `[]`, amdProvider(AMDGatewayURL), "", "did not register AMD LLM Gateway there"},
		{"registered elsewhere", registeredAMD, amdProvider("https://other.example/v1"), "", "did not change its AMD LLM Gateway settings"},
		{"pasted key", registeredAMD, amdProvider(AMDGatewayURL), "secret", "did not hand it your AMD LLM Gateway key"},
		{"fireworks never registered", registeredAMD,
			Provider{Name: "fireworks", BaseURL: FireworksURL, Header: "Authorization", Prefix: "Bearer "}, "",
			"did not register Fireworks AI there"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newUsersServer(t, tc.providers)
			useConfig(t, external(srv.base(), false))

			err := New("").Configure(context.Background(), tc.provider, tc.key)

			if got := srv.setupChanges(); len(got) != 0 {
				t.Fatalf("GAIA changed the user's server: %v", got)
			}
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("a provider already set up the same way must just work: %v", err)
				}
				return
			}
			if err == nil {
				t.Fatal("expected a refusal, got success")
			}
			for _, want := range []string{tc.wantErr, srv.base(), "doesn't change its setup", SwitchBackHint} {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("error %q lacks %q", err, want)
				}
			}
			if strings.Contains(err.Error(), "secret") {
				t.Errorf("error echoes the key: %q", err)
			}
		})
	}
}

func TestExternalModeRefusesEveryRawSetupRequest(t *testing.T) {
	srv := newUsersServer(t, registeredAMD)
	useConfig(t, external(srv.base(), false))
	c := New("")
	ctx := context.Background()

	// Clearing leaves the server's key alone, so the caller can still forget
	// GAIA's own saved copy.
	if err := c.Clear(ctx, "amd"); err != nil {
		t.Errorf("Clear must succeed without touching the user's server: %v", err)
	}
	for _, req := range [][2]string{
		{"POST", "/install"}, {"POST", "/uninstall"}, {"POST", "/delete"}, {"POST", "/unload"},
		{"POST", "/cloud/auth"}, {"DELETE", "/cloud/auth/fireworks"}, {"POST", "/install?force=1"},
	} {
		if err := c.request(ctx, req[0], req[1], map[string]any{}, nil); err == nil {
			t.Errorf("%s %s reached the user's server", req[0], req[1])
		}
	}
	// Using the server stays allowed.
	if _, err := c.Models(ctx, "amd"); err != nil {
		t.Errorf("listing models must still work: %v", err)
	}
	if got := srv.setupChanges(); len(got) != 0 {
		t.Fatalf("GAIA changed the user's server: %v", got)
	}
}

// The guard is about the user's own server only: GAIA's server, or one named by
// LEMONADE_BASE_URL, is configured as before.
func TestOnlyExternalModeIsGuarded(t *testing.T) {
	srv := newUsersServer(t, `[]`)
	useConfig(t, map[string]any{"lemonade_mode": "gaia"})
	t.Setenv("LEMONADE_BASE_URL", srv.base())

	if err := New("").Configure(context.Background(), amdProvider(AMDGatewayURL), ""); err != nil {
		t.Fatal(err)
	}
	if got := srv.setupChanges(); len(got) != 1 || got[0] != "POST /install" {
		t.Fatalf("want the one registration, got %v", got)
	}
}

func TestAnUnreadableChoiceRefusesSetupChanges(t *testing.T) {
	useConfig(t, map[string]any{"lemonade_mode": "external"})
	if err := RefuseRequest("POST", "/install"); err == nil {
		t.Fatal("external mode with no URL must not let a setup change through")
	}
}

func TestIsSetupChange(t *testing.T) {
	for _, tc := range []struct {
		method, path string
		want         bool
	}{
		{"POST", "/install", true},
		{"POST", "install", true},
		{"POST", "/install/", true},
		{"POST", "/install?x=1", true},
		{"POST", "/uninstall", true},
		{"POST", "/delete", true},
		{"POST", "/unload", true},
		{"POST", "/cloud/auth", true},
		{"DELETE", "/cloud/auth/amd", true},
		{"GET", "/install", false},
		{"GET", "/system-info", false},
		{"POST", "/load", false},
		{"POST", "/pull", false},
		{"POST", "/chat/completions", false},
		{"POST", "/cloud/authority", false},
	} {
		if got := IsSetupChange(tc.method, tc.path); got != tc.want {
			t.Errorf("IsSetupChange(%s %s) = %v, want %v", tc.method, tc.path, got, tc.want)
		}
	}
}
