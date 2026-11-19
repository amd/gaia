// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
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
		`null`:                               true,
	}
	for content, want := range cases {
		setPolicy(t, content)
		if got := LocalOnly(); got != want {
			t.Errorf("policy %q: LocalOnly() = %v, want %v", content, got, want)
		}
	}
}

// The table Python's reader runs too (tests/unit/test_local_only.py): the
// two front ends must agree on every file, or one hands out a cloud key the
// other refuses.
func TestLocalOnlyMatchesPythonOnTheSharedTable(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "local_only_policy_cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Cases []struct {
			Name    string  `json:"name"`
			Content *string `json:"content"`
			Build   *struct {
				Prefix string `json:"prefix"`
				Repeat string `json:"repeat"`
				Times  int    `json:"times"`
				Suffix string `json:"suffix"`
			} `json:"build"`
			LocalOnly bool `json:"local_only"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.Cases) == 0 {
		t.Fatal("the shared policy table has no cases")
	}
	for _, c := range table.Cases {
		path := filepath.Join(t.TempDir(), "policy.json")
		decode := func(s string) []byte {
			b, err := base64.StdEncoding.DecodeString(s)
			if err != nil {
				t.Fatalf("%s: %v", c.Name, err)
			}
			return b
		}
		var data []byte
		switch {
		case c.Build != nil:
			data = append(decode(c.Build.Prefix), bytes.Repeat(decode(c.Build.Repeat), c.Build.Times)...)
			data = append(data, decode(c.Build.Suffix)...)
		case c.Content != nil:
			data = decode(*c.Content)
		}
		if c.Build != nil || c.Content != nil {
			if err := os.WriteFile(path, data, 0o600); err != nil {
				t.Fatal(err)
			}
		}
		if got := LocalOnlyAt(path); got != c.LocalOnly {
			t.Errorf("%s: LocalOnlyAt = %v, Python reads %v", c.Name, got, c.LocalOnly)
		}
	}
}

func TestLocalOnlyWithNoResolvablePolicyPathFailsClosed(t *testing.T) {
	if !LocalOnlyAt("") {
		t.Error(`LocalOnlyAt("") = false: with no home directory the user's policy can't be read, so Local only can't be confirmed off`)
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
