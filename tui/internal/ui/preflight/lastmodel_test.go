// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestReadLastModelNothingSaved(t *testing.T) {
	dir := t.TempDir()
	for name, body := range map[string]string{
		"missing":    "",
		"empty":      `{}`,
		"other-keys": `{"profile":"chat","full_access":false}`,
		"cleared":    `{"last_provider":"fireworks","last_model":""}`,
		"null":       `{"last_model":null}`,
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(dir, name+".json")
			if body != "" {
				if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			got, err := readLastModel(probeWith(path))
			if err != nil || got.Set() {
				t.Fatalf("want no saved choice, got %+v err=%v", got, err)
			}
		})
	}
}

func TestReadLastModelUnreadableIsAnError(t *testing.T) {
	dir := t.TempDir()
	for name, body := range map[string]string{
		"corrupt":    `{not json`,
		"wrong-type": `{"last_model":42}`,
	} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(dir, name+".json")
			if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := readLastModel(probeWith(path)); err == nil {
				t.Fatal("a config that cannot be read must say so, not read as 'nothing saved'")
			}
		})
	}
}

func TestWriteLastModelRoundTripsAndKeepsOtherKeys(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	t.Setenv(configFileEnv, path)
	if err := os.WriteFile(path, []byte(`{"profile":"npu","full_access":true,"future_key":[1,2]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := WriteLastModel("fireworks", "fireworks.deepseek-v4p1-flash"); err != nil {
		t.Fatal(err)
	}
	got, err := readLastModel(probeWith(path))
	if err != nil || got.Provider != "fireworks" || got.Model != "fireworks.deepseek-v4p1-flash" {
		t.Fatalf("round trip = %+v, %v", got, err)
	}
	raw, _ := os.ReadFile(path)
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	if doc["profile"] != "npu" || doc["full_access"] != true || doc["future_key"] == nil {
		t.Fatalf("keys this code does not own were lost: %v", doc)
	}
}

func TestWriteLastModelRefusesToClobberACorruptConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	t.Setenv(configFileEnv, path)
	if err := os.WriteFile(path, []byte(`{not json`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := WriteLastModel("local", "Gemma-4-E4B-it-GGUF"); err == nil {
		t.Fatal("overwriting an unreadable config would lose the user's settings")
	}
	if raw, _ := os.ReadFile(path); string(raw) != `{not json` {
		t.Fatal("the corrupt config was modified")
	}
}
