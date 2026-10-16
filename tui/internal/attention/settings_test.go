// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseSettings(t *testing.T) {
	cases := []struct {
		raw  string
		want Settings
	}{
		{"", Defaults()},
		{`{"reduced_motion": true}`, Defaults()},
		{`{"notify_approval": false}`, Settings{Question: true, Finished: true, KeepAwake: true}},
		{`{"notify_question": false}`, Settings{Approval: true, Finished: true, KeepAwake: true}},
		{`{"notify_finished": false}`, Settings{Approval: true, Question: true, KeepAwake: true}},
		{`{"keep_awake": false}`, Settings{Approval: true, Question: true, Finished: true}},
		{`{"notify_finished": null}`, Defaults()},
	}
	for _, tc := range cases {
		got, err := ParseSettings([]byte(tc.raw))
		if err != nil {
			t.Fatalf("%s: %v", tc.raw, err)
		}
		if got != tc.want {
			t.Errorf("%s: got %+v, want %+v", tc.raw, got, tc.want)
		}
	}
}

func TestParseSettingsNamesTheBadKey(t *testing.T) {
	got, err := ParseSettings([]byte(`{"notify_approval": "no", "keep_awake": false}`))
	if err == nil || !strings.Contains(err.Error(), "notify_approval must be true or false") {
		t.Fatalf("got %v", err)
	}
	// The readable key still applies; the broken one stays on.
	if got.KeepAwake || !got.Approval {
		t.Fatalf("got %+v", got)
	}
}

func TestParseSettingsRejectsBrokenJSON(t *testing.T) {
	if _, err := ParseSettings([]byte(`{`)); err == nil {
		t.Fatal("broken JSON was accepted")
	}
}

func TestLoadSettings(t *testing.T) {
	dir := t.TempDir()
	if s, err := LoadSettings(filepath.Join(dir, "missing.json")); err != nil || s != Defaults() {
		t.Fatalf("missing file: %+v %v", s, err)
	}
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte(`{"notify_question": 1}`), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := LoadSettings(path)
	if err == nil || !strings.Contains(err.Error(), path) {
		t.Fatalf("error should name the file: %v", err)
	}
}
