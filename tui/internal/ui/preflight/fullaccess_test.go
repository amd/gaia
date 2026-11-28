// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func probeWith(path string) hostProbe {
	p := realHostProbe()
	p.getenv = nil
	p.lookupEnv = func(k string) (string, bool) {
		if k == configFileEnv {
			return path, true
		}
		return "", false
	}
	return p
}

func TestReadFullAccessOffWhenNothingSaysOtherwise(t *testing.T) {
	dir := t.TempDir()
	for name, body := range map[string]string{
		"missing":     "",
		"empty":       `{}`,
		"other-keys":  `{"profile":"chat","default_device":"gpu"}`,
		"explicitoff": `{"full_access":false}`,
		"corrupt":     `{not json`,
		"wrong-type":  `{"full_access":"yes"}`,
		"string-true": `{"full_access":"true"}`,
		"number":      `{"full_access":1}`,
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(dir, name+".json")
			if body != "" {
				if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			if got := readFullAccess(probeWith(path)); got.Enabled {
				t.Errorf("%s must not read as permission, got Enabled=true", name)
			}
		})
	}
}

func TestReadFullAccessReportsAConfigReadFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	p := probeWith(path)
	p.readConfig = func(string) ([]byte, error) { return nil, errors.New("lock timed out") }
	got := readFullAccess(p)
	if got.Enabled {
		t.Fatal("a config read failure must not enable full access")
	}
	if got.Err == nil || !strings.Contains(got.Err.Error(), "lock timed out") {
		t.Fatalf("read error was not preserved: %v", got.Err)
	}
}

func TestReadFullAccessOnlyWhenTrue(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"full_access":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	got := readFullAccess(probeWith(path))
	if !got.Enabled {
		t.Fatal("full_access:true must read as enabled")
	}
	if got.Path != path {
		t.Errorf("the config path must come back so the UI can name it: %q", got.Path)
	}
}

// The write path shares this file with Python, which owns most of its keys.
func TestWriteFullAccessKeepsEveryOtherKey(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	t.Setenv(configFileEnv, path)

	original := `{"profile":"npu","default_device":"npu","default_model":"Gemma-4-E4B-it-GGUF"}`
	if err := os.WriteFile(path, []byte(original), 0o600); err != nil {
		t.Fatal(err)
	}

	if _, err := WriteFullAccess(true); err != nil {
		t.Fatalf("write: %v", err)
	}

	var doc map[string]any
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("wrote invalid JSON: %v", err)
	}
	for key, want := range map[string]any{
		"profile":        "npu",
		"default_device": "npu",
		"default_model":  "Gemma-4-E4B-it-GGUF",
		"full_access":    true,
	} {
		if doc[key] != want {
			t.Errorf("%s = %v, want %v — a write from the TUI must not drop Python's keys", key, doc[key], want)
		}
	}
}

func TestWriteFullAccessRoundTrips(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	t.Setenv(configFileEnv, path)

	for _, want := range []bool{true, false, true} {
		if _, err := WriteFullAccess(want); err != nil {
			t.Fatalf("write %v: %v", want, err)
		}
		if got := readFullAccess(probeWith(path)); got.Enabled != want {
			t.Errorf("wrote %v, read back %v", want, got.Enabled)
		}
	}
}

// Refusing beats overwriting: the file holds settings the user put there.
func TestWriteFullAccessRefusesToClobberCorruptConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	t.Setenv(configFileEnv, path)
	corrupt := `{"profile": "chat"` // truncated by a crash or a bad edit
	if err := os.WriteFile(path, []byte(corrupt), 0o600); err != nil {
		t.Fatal(err)
	}

	if _, err := WriteFullAccess(true); err == nil {
		t.Fatal("a corrupt config must be reported, not replaced")
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != corrupt {
		t.Errorf("the file was modified: %q", raw)
	}
}

func TestWriteFullAccessCreatesAMissingConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "config.json")
	t.Setenv(configFileEnv, path)

	if _, err := WriteFullAccess(true); err != nil {
		t.Fatalf("write: %v", err)
	}
	if got := readFullAccess(probeWith(path)); !got.Enabled {
		t.Error("a fresh install must be able to save the preference")
	}
}

func TestProbeWithIgnoresProcessEnv(t *testing.T) {
	other := filepath.Join(t.TempDir(), "other.json")
	if err := os.WriteFile(other, []byte(`{"full_access":false}`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(configFileEnv, other)

	path := filepath.Join(t.TempDir(), "target.json")
	if err := os.WriteFile(path, []byte(`{"full_access":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	p := probeWith(path)
	if got := configPath(p); got != path {
		t.Fatalf("configPath(probeWith) = %q, want %q", got, path)
	}
	if got := readFullAccess(p); !got.Enabled {
		t.Error("probeWith must stay isolated from process env")
	}

	copied := realHostProbe()
	copied.getenv = func(k string) string {
		if k == configFileEnv {
			return path
		}
		return ""
	}
	if got := configPath(copied); got != path {
		t.Fatalf("configPath(copied probe with getenv) = %q, want %q", got, path)
	}
}

func TestConfigPathUsesPresenceAwareLookup(t *testing.T) {
	home := filepath.Join("fake", "home")
	p := hostProbe{
		homeDir: func() (string, error) { return home, nil },
		lookupEnv: func(k string) (string, bool) {
			switch k {
			case "GAIA_HOME":
				return "~/$EMPTY/g", true
			case "EMPTY":
				return "", true
			default:
				return "", false
			}
		},
	}
	if got, want := filepath.Clean(configPath(p)), filepath.Join(home, "g", "config.json"); got != want {
		t.Fatalf("empty-defined var: configPath = %q, want %q", got, want)
	}
	p.lookupEnv = func(k string) (string, bool) {
		if k == "GAIA_HOME" {
			return "~/$UNSET_9099/g", true
		}
		return "", false
	}
	if got, want := filepath.Clean(configPath(p)), filepath.Join(home, "$UNSET_9099", "g", "config.json"); got != want {
		t.Fatalf("unset var: configPath = %q, want %q", got, want)
	}
}
