package gaiainit

import (
	"slices"
	"testing"
)

// The TUI never loads the browser Agent UI, and that build step only runs in a
// source checkout — where it failed on a missing type package and took the
// whole run's exit code with it, AFTER Lemonade and both models had installed.
// Setup launched from the terminal UI must not fail on a component it does not
// use.
func TestRunArgsSkipTheBrowserUIBuild(t *testing.T) {
	for _, claude := range []bool{false, true} {
		args := RunArgs(claude)
		if !slices.Contains(args, "--skip-webui-build") {
			t.Errorf("RunArgs(%v) = %v; missing --skip-webui-build", claude, args)
		}
	}
}

// --check is read-only and never builds anything, so the flag would be noise
// there — and an older gaia that does not know it would exit 2, which Check
// reports as "could not determine" rather than "not ready".
func TestCheckArgsDoNotCarryBuildFlags(t *testing.T) {
	if args := CheckArgs(false); slices.Contains(args, "--skip-webui-build") {
		t.Errorf("CheckArgs = %v; the read-only probe builds nothing", args)
	}
}

// The command shown to the user must match what the TUI actually ran, or a
// copy-pasted retry behaves differently from the key they just pressed.
func TestRunCommandNamesTheProfileThatIsRun(t *testing.T) {
	args := RunArgs(false)
	i := slices.Index(args, "--profile")
	if i < 0 || i+1 >= len(args) {
		t.Fatalf("RunArgs carries no --profile: %v", args)
	}
	if got := RunCommand(false); got != "gaia init --profile "+args[i+1] {
		t.Errorf("RunCommand = %q, but RunArgs uses profile %q", got, args[i+1])
	}
}
