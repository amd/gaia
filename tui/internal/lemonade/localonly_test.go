// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func setPolicy(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "policy.json")
	if content != "" {
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("GAIA_POLICY_FILE", path)
	return path
}

func TestPolicyPathFollowsGaiaHome(t *testing.T) {
	home := func() (string, error) { return "/home/u", nil }
	cases := []struct {
		env  map[string]string
		want string
	}{
		{map[string]string{"GAIA_POLICY_FILE": "/p/x.json", "GAIA_HOME": "/h"}, "/p/x.json"},
		// gaia_home(): GAIA_HOME wins over GAIA_CONFIG_DIR, as in Python.
		{map[string]string{"GAIA_HOME": "/h", "GAIA_CONFIG_DIR": "/c"}, filepath.Join("/h", "policy.json")},
		{map[string]string{"GAIA_CONFIG_DIR": "/c"}, filepath.Join("/c", "policy.json")},
		{map[string]string{"GAIA_HOME": "  "}, filepath.Join("/home/u", ".gaia", "policy.json")},
	}
	for _, c := range cases {
		got := PolicyPathFrom(func(k string) string { return c.env[k] }, home)
		if got != c.want {
			t.Errorf("%v: got %q, want %q", c.env, got, c.want)
		}
	}
}

func TestLocalOnlyReadsTheFileAndFailsClosed(t *testing.T) {
	cases := map[string]bool{
		"":                                   false, // no file: cloud allowed
		`{"network_policy":"cloud_allowed"}`: false,
		`{"other_key":1}`:                    false,
		`{"network_policy":"local_ai_only"}`: true,
		`{"network_policy":"offline"}`:       true,
		`{"network_policy":"sometimes"}`:     true, // unknown level: fail closed
		`{not json`:                          true,
		`{"network_policy":null}`:            true,
		`{"network_policy":1}`:               true,
		`[]`:                                 true,
	}
	for content, want := range cases {
		setPolicy(t, content)
		if got := LocalOnly(); got != want {
			t.Errorf("policy %q: LocalOnly() = %v, want %v", content, got, want)
		}
	}
}

func TestLocalOnlyRefusesCloudSetupButNotRemoval(t *testing.T) {
	setPolicy(t, `{"network_policy":"local_ai_only"}`)
	for _, req := range [][2]string{{"POST", "/install"}, {"POST", "/cloud/auth"}} {
		err := RefuseRequest(req[0], req[1])
		if err == nil || !strings.Contains(err.Error(), "Local only is on") ||
			!strings.Contains(err.Error(), "network_policy cloud_allowed") {
			t.Errorf("%v: %v", req, err)
		}
	}
	for _, req := range [][2]string{{"DELETE", "/cloud/auth/fireworks"}, {"GET", "/models"}, {"POST", "/load"}} {
		if err := RefuseRequest(req[0], req[1]); err != nil {
			t.Errorf("%v refused under Local only: %v", req, err)
		}
	}
}

func TestConfigureSendsNothingUnderLocalOnly(t *testing.T) {
	setPolicy(t, `{"network_policy":"local_ai_only"}`)
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			hits.Add(1)
		}
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	t.Setenv("LEMONADE_API_KEY", "local-key")
	c := New(srv.URL + "/v1")
	p := Provider{Name: "fireworks", BaseURL: FireworksURL, Header: "Authorization", Prefix: "Bearer "}
	err := c.Configure(context.Background(), p, "fw_secret_key")
	if err == nil || !strings.Contains(err.Error(), "Local only is on") {
		t.Fatalf("Configure: %v", err)
	}
	if strings.Contains(err.Error(), "fw_secret_key") {
		t.Error("the key was echoed in the error")
	}
	if n := hits.Load(); n != 0 {
		t.Errorf("%d POSTs reached Lemonade under Local only", n)
	}
}

func TestPolicyPathExpandsTheHomeDirectory(t *testing.T) {
	home := func() (string, error) { return "/home/u", nil }
	env := map[string]string{"GAIA_POLICY_FILE": "~/custom/policy.json"}
	got := PolicyPathFrom(func(k string) string { return env[k] }, home)
	if want := filepath.Join("/home/u", "custom", "policy.json"); got != want {
		t.Errorf("got %q, want %q", got, want)
	}
	env = map[string]string{"GAIA_HOME": "~/gaia-home"}
	got = PolicyPathFrom(func(k string) string { return env[k] }, home)
	if want := filepath.Join("/home/u", "gaia-home", "policy.json"); got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}
