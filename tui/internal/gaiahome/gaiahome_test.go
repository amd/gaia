// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiahome

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

type expandFixture struct {
	PosixHome   string            `json:"posix_home"`
	WindowsHome string            `json:"windows_home"`
	Env         map[string]string `json:"env"`
	Cases       []struct {
		Name    string `json:"name"`
		Raw     string `json:"raw"`
		Posix   string `json:"posix"`
		Windows string `json:"windows"`
	} `json:"cases"`
}

func TestExpandPathMatchesPythonTable(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "gaia_home_expand_cases.json"))
	if err != nil {
		t.Fatalf("read shared expansion fixture: %v", err)
	}
	var fx expandFixture
	if err := json.Unmarshal(raw, &fx); err != nil {
		t.Fatalf("decode shared expansion fixture: %v", err)
	}

	lookup := func(k string) (string, bool) {
		v, ok := fx.Env[k]
		return v, ok
	}
	posixHome := func() (string, error) { return fx.PosixHome, nil }
	winHome := func() (string, error) { return fx.WindowsHome, nil }

	for _, tc := range fx.Cases {
		t.Run("posix/"+tc.Name, func(t *testing.T) {
			if got := ExpandPathForOS(tc.Raw, lookup, posixHome, false); got != tc.Posix {
				t.Errorf("ExpandPathForOS(%q, posix) = %q, want %q", tc.Raw, got, tc.Posix)
			}
		})
		t.Run("windows/"+tc.Name, func(t *testing.T) {
			if got := ExpandPathForOS(tc.Raw, lookup, winHome, true); got != tc.Windows {
				t.Errorf("ExpandPathForOS(%q, windows) = %q, want %q", tc.Raw, got, tc.Windows)
			}
		})
	}
}

func TestExpandPathPreservesEmptyAndUndefinedEnv(t *testing.T) {
	home := ExpandUser("~", os.UserHomeDir)
	t.Setenv("GAIA_TEST_DEFINED", "sub")
	t.Setenv("GAIA_TEST_EMPTY", "")

	if got, want := ExpandPath("~/$GAIA_TEST_DEFINED"), home+"/sub"; got != want {
		t.Errorf("defined var: got %q, want %q", got, want)
	}
	if got, want := ExpandPath("~/$GAIA_TEST_EMPTY/x"), home+"//x"; got != want {
		t.Errorf("empty var: got %q, want %q", got, want)
	}
	if got, want := ExpandPath("~/$GAIA_DEFINITELY_UNSET_9099/x"), home+"/$GAIA_DEFINITELY_UNSET_9099/x"; got != want {
		t.Errorf("undefined var: got %q, want %q", got, want)
	}
	if runtime.GOOS == "windows" {
		if got, want := ExpandPath("~/%GAIA_TEST_DEFINED%"), home+"/sub"; got != want {
			t.Errorf("windows percent var: got %q, want %q", got, want)
		}
	}
}
