// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

func TestFlagshipAgentIsUnsetWithoutAConfigOrKey(t *testing.T) {
	useConfig(t, "")
	got, err := ReadFlagshipAgent()
	if err != nil || got.Choice != "" || got.Program() != "" {
		t.Fatalf("no config file: got %+v, %v", got, err)
	}

	useConfig(t, `{"default_model": "x", "flagship_agent": null}`)
	got, err = ReadFlagshipAgent()
	if err != nil || got.Choice != "" {
		t.Fatalf("no key: got %+v, %v", got, err)
	}
}

func TestFlagshipAgentRoundTripKeepsOtherKeys(t *testing.T) {
	path := useConfig(t, `{"default_model": "Gemma-4-E4B-it-GGUF", "full_access": false, "future_key": {"a": 1}}`)

	// A slice, not a map: the check below needs the Python agent written last.
	for _, c := range []struct{ choice, program string }{
		{FlagshipAgentNative, catalog.NativeAgentBinary},
		{FlagshipAgentPython, catalog.PythonAgentBinary},
	} {
		choice, program := c.choice, c.program
		wrote, err := WriteFlagshipAgent(choice)
		if err != nil || wrote != path {
			t.Fatalf("write %s: path %q, %v", choice, wrote, err)
		}
		got, err := ReadFlagshipAgent()
		if err != nil || got.Choice != choice || got.Program() != program || got.Path != path {
			t.Fatalf("read back %+v, %v; want %s running %s", got, err, choice, program)
		}
	}

	doc := readDoc(t, path)
	if doc[FlagshipAgentKey] != FlagshipAgentPython {
		t.Fatalf("the key on disk is %v", doc[FlagshipAgentKey])
	}
	future, _ := doc["future_key"].(map[string]any)
	if doc["default_model"] != "Gemma-4-E4B-it-GGUF" || doc["full_access"] != false || future["a"] != float64(1) {
		t.Fatalf("a write dropped another key: %v", doc)
	}
}

// A well-formed name this build can't run loads, so a config a newer GAIA wrote never
// stops the TUI starting; launching it is the error.
func TestFlagshipAgentThisBuildCannotRunLoadsAndSaysWhy(t *testing.T) {
	for _, name := range []string{"gaiacode", "gaia-agent"} {
		useConfig(t, `{"flagship_agent": "`+name+`"}`)
		got, err := ReadFlagshipAgent()
		if err != nil {
			t.Fatalf("%s did not load: %v", name, err)
		}
		if got.Choice != name || got.Program() != "" {
			t.Errorf("%s loaded as %+v", name, got)
		}
		if why := FlagshipAgentUnavailable(got.Choice); !strings.Contains(why, name) {
			t.Errorf("%s: no reason it can't run: %q", name, why)
		}
	}
	if why := FlagshipAgentUnavailable(FlagshipAgentNative); why != "" {
		t.Errorf("gaiacpp was reported as unavailable: %q", why)
	}
}

func TestFlagshipAgentBadValueIsAnErrorNamingFileKeyAndValues(t *testing.T) {
	for _, body := range []string{
		`{"flagship_agent": ""}`,
		`{"flagship_agent": "gaia cpp"}`,
		`{"flagship_agent": 2}`,
		`{"flagship_agent": true}`,
	} {
		path := useConfig(t, body)
		got, err := ReadFlagshipAgent()
		if err == nil {
			t.Fatalf("%s was accepted as %+v", body, got)
		}
		if got.Choice != "" {
			t.Errorf("%s: a refused value still came back as a choice: %q", body, got.Choice)
		}
		for _, want := range []string{path, FlagshipAgentKey, `"gaiapy"`, `"gaiacpp"`} {
			if !strings.Contains(err.Error(), want) {
				t.Errorf("%s: the error does not name %s:\n%v", body, want, err)
			}
		}
	}
}

// Python's GaiaConfig accepts the same spellings, so the two readers agree.
func TestFlagshipAgentIsTrimmedAndLowercasedLikePython(t *testing.T) {
	useConfig(t, `{"flagship_agent": " GaiaCPP "}`)
	got, err := ReadFlagshipAgent()
	if err != nil || got.Choice != FlagshipAgentNative {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestFlagshipAgentUnreadableConfigIsAnError(t *testing.T) {
	path := useConfig(t, `{not json`)
	if _, err := ReadFlagshipAgent(); err == nil || !strings.Contains(err.Error(), path) {
		t.Fatalf("a corrupt config was read as no choice: %v", err)
	}
	if _, err := WriteFlagshipAgent(FlagshipAgentNative); err == nil {
		t.Fatal("a write replaced a config file that is not valid JSON")
	}
}

func TestWriteFlagshipAgentRefusesUnknownValues(t *testing.T) {
	path := useConfig(t, `{"keep": 1}`)
	if _, err := WriteFlagshipAgent("gaia-agent"); err == nil || !strings.Contains(err.Error(), "gaiapy") {
		t.Fatalf("an unknown value was written or the error names no valid one: %v", err)
	}
	if doc := readDoc(t, path); len(doc) != 1 {
		t.Fatalf("a refused write changed the file: %v", doc)
	}
}
