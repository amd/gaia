// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

// The user's network policy (~/.gaia/policy.json). Under "Local only" no GAIA
// AI request may reach a cloud model, so the TUI hands Lemonade no cloud key
// and registers no cloud provider. Mirrors Python's gaia.llm.local_only: the
// same file, the same fail-closed read.

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

// LocalOnlyHint is how to turn Local only off; the same words Python uses.
const LocalOnlyHint = "To allow cloud models again, run `gaia config set network_policy cloud_allowed`."

// PolicyPathFrom mirrors gaia.llm.local_only.policy_path: GAIA_POLICY_FILE,
// then gaia_home() (GAIA_HOME, then GAIA_CONFIG_DIR, then ~/.gaia).
// "" when no home directory resolves.
func PolicyPathFrom(getenv func(string) string, homeDir func() (string, error)) string {
	if file := strings.TrimSpace(getenv("GAIA_POLICY_FILE")); file != "" {
		return expandUser(file, homeDir)
	}
	for _, name := range []string{"GAIA_HOME", "GAIA_CONFIG_DIR"} {
		if dir := strings.TrimSpace(getenv(name)); dir != "" {
			return filepath.Join(expandUser(os.Expand(dir, getenv), homeDir), "policy.json")
		}
	}
	home, err := homeDir()
	if err != nil || home == "" {
		return ""
	}
	return filepath.Join(home, ".gaia", "policy.json")
}

// expandUser resolves a leading ~ as Python's expanduser does.
func expandUser(path string, homeDir func() (string, error)) string {
	if path != "~" && !strings.HasPrefix(path, "~/") && !strings.HasPrefix(path, `~\`) {
		return path
	}
	home, err := homeDir()
	if err != nil || home == "" {
		return path
	}
	return filepath.Join(home, path[1:])
}

// Limits every policy file must meet before it is parsed; Python's reader
// (gaia.llm.local_only) applies the same ones, so a file one JSON parser takes
// and the other rejects never reads differently in the two.
// tests/fixtures/local_only_policy_cases.json holds the cases both must pass.
const (
	policyMaxBytes       = 64 << 10
	policyMaxDepth       = 16
	policyMaxNumberChars = 20
)

// policyWithinLimits reports whether raw is small, valid UTF-8, nests no
// deeper than policyMaxDepth and has no number longer than
// policyMaxNumberChars (not counting a leading minus).
func policyWithinLimits(raw []byte) bool {
	if len(raw) > policyMaxBytes || !utf8.Valid(raw) {
		return false
	}
	depth, run := 0, 0
	var lead byte
	inString, escaped := false, false
	for _, b := range raw {
		if inString {
			switch {
			case escaped:
				escaped = false
			case b == '\\':
				escaped = true
			case b == '"':
				inString = false
			}
			continue
		}
		if strings.IndexByte("0123456789+-.eE", b) >= 0 {
			if run == 0 {
				lead = b
			}
			run++
			n := run
			if lead == '-' {
				n--
			}
			if n > policyMaxNumberChars {
				return false
			}
			continue
		}
		run = 0
		switch b {
		case '"':
			inString = true
		case '[', '{':
			depth++
			if depth > policyMaxDepth {
				return false
			}
		case ']', '}':
			depth--
		}
	}
	return true
}

// LocalOnlyAt reports whether the policy file at path blocks every cloud AI
// model. A missing file means cloud is allowed; a file outside the shared
// limits, or that isn't a JSON object, counts as Local only.
func LocalOnlyAt(path string) bool {
	if path == "" {
		// No home directory: the user's policy can't be read, so fail closed.
		return true
	}
	f, err := os.Open(path)
	if errors.Is(err, fs.ErrNotExist) {
		return false
	}
	if err != nil {
		return true
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, policyMaxBytes+1))
	if err != nil || !policyWithinLimits(raw) {
		return true
	}
	var policy map[string]json.RawMessage
	// `null` unmarshals into a nil map without error; Python refuses it.
	if err := json.Unmarshal(raw, &policy); err != nil || policy == nil {
		return true
	}
	value, present := policy["network_policy"]
	if !present {
		return false
	}
	// Anything but the string "cloud_allowed" — null, a number, an unknown
	// level — counts as Local only, as Python's read does.
	var level string
	if err := json.Unmarshal(value, &level); err != nil {
		return true
	}
	return level != "cloud_allowed"
}

// LocalOnly reads the policy for this process's environment, fresh each call
// so a change made by another front end takes effect on the next request.
func LocalOnly() bool {
	return LocalOnlyAt(PolicyPathFrom(os.Getenv, os.UserHomeDir))
}

// refuseCloudSetup is the Local only error for a request that would register
// a cloud provider or hand Lemonade a key; nil otherwise. Removing a key or a
// provider is always allowed.
func refuseCloudSetup(method, path string) error {
	if method != "POST" {
		return nil
	}
	route, _, _ := strings.Cut(strings.Trim(path, "/"), "?")
	if route != "install" && route != "cloud/auth" {
		return nil
	}
	if !LocalOnly() {
		return nil
	}
	return fmt.Errorf("Local only is on, so GAIA did not %s. %s",
		describeSetupChange(method, path), LocalOnlyHint)
}
