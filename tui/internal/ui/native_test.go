package ui

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
)

// --use-claude with the native agent is refused as a command-line error, before
// a screen opens, and says both ways forward.
func TestUseClaudeIsRefusedForTheNativeAgentAtLaunch(t *testing.T) {
	native := catalog.Agent{ID: catalog.FlagshipID, Native: true}
	err := checkClaudeSupported(native, true)
	if err == nil {
		t.Fatal("--use-claude was accepted for gaiacpp, which exits on it")
	}
	for _, want := range []string{"--use-claude", client.NativeClaudeUnavailable,
		catalog.AgentEnv + "=" + catalog.PythonAgentBinary, catalog.AgentChoiceDocsURL} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not say %q:\n%s", want, err)
		}
	}

	if err := checkClaudeSupported(native, false); err != nil {
		t.Errorf("a local launch of the native agent was refused: %v", err)
	}
	if err := checkClaudeSupported(catalog.Agent{ID: catalog.FlagshipID}, true); err != nil {
		t.Errorf("--use-claude was refused for the Python agent: %v", err)
	}
}

func TestANamedAgentThatIsMissingStopsTheLaunch(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Chdir(t.TempDir())

	if _, _, err := loadCatalog("", catalog.NativeAgentBinary, catalog.FlagshipID); err == nil ||
		!strings.Contains(err.Error(), "Looked in") {
		t.Fatalf("a missing gaiacpp did not stop the launch saying where it looked: %v", err)
	}

	// --mock stands in for whichever agent program would have run.
	cat, _, err := loadCatalog("/tmp/mock-agent", catalog.NativeAgentBinary, catalog.FlagshipID)
	if err != nil {
		t.Fatalf("--mock was refused over an agent it replaces: %v", err)
	}
	if got := cat.Get(catalog.FlagshipID); got.Native || got.BinaryPath != "/tmp/mock-agent" {
		t.Errorf("--mock did not replace the agent: %+v", got)
	}
}

// The override names the flagship's program. Launching another agent must not
// trip over it, however wrong its value is.
func TestTheAgentProgramOverrideOnlyAppliesToTheFlagship(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Chdir(t.TempDir())

	for _, choice := range []string{catalog.NativeAgentBinary, "not-an-agent", "/nowhere/gaiacpp"} {
		cat, notice, err := loadCatalog("", choice, "email")
		if err != nil || notice != "" {
			t.Fatalf("launching email failed over the flagship's override %q: %v %q", choice, err, notice)
		}
		if got := cat.Get(catalog.FlagshipID); got.Native || got.BinaryPath != catalog.PythonAgentBinary {
			t.Errorf("the override %q was applied while launching another agent: %+v", choice, got)
		}
		if _, _, err := loadCatalog("", choice, catalog.FlagshipID); err == nil {
			t.Errorf("the same override %q did not stop a flagship launch", choice)
		}
	}
}

// gaiacpp without --stdio opens its own prompt on the pipe; the chat would sit
// on "connected" and never get an event.
func TestSubprocessGaiacppWithoutStdioIsRefused(t *testing.T) {
	name := catalog.NativeAgentBinary
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	bin := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(bin, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	err := RunChat(`"`+filepath.ToSlash(bin)+`" -m some-model`, "", false, nil, nil)
	if err == nil || !strings.Contains(err.Error(), catalog.NativeStdioFlag) {
		t.Fatalf("gaiacpp without --stdio was not refused naming the flag: %v", err)
	}
}
