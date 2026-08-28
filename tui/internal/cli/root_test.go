// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// The installer ships this binary as `gaia-tui`, so a hardcoded `gaia` in the
// usage lines told users to type a command they do not have. Every name below
// is one the binary can genuinely be invoked under.
func TestBinaryName(t *testing.T) {
	tests := []struct {
		name  string
		argv0 string
		want  string
	}{
		{"installed name", "gaia-tui", "gaia-tui"},
		{"absolute path", "/usr/local/bin/gaia-tui", "gaia-tui"},
		{"relative path", filepath.Join("build", "gaia-tui"), "gaia-tui"},
		{"dot-slash", "./gaia-tui", "gaia-tui"},
		{"renamed by the user", "/opt/bin/gaia-hub", "gaia-hub"},
		{"go run temp binary", "/tmp/go-build123/b001/exe/gaia", "gaia"},
		{"exe suffix", "gaia-tui.exe", "gaia-tui"},
		{"exe suffix with path", "/opt/GAIA/gaia-tui.exe", "gaia-tui"},
		{"exe suffix uppercase", "GAIA-TUI.EXE", "GAIA-TUI"},
		{"empty argv", "", defaultBinaryName},
		{"whitespace argv", "   ", defaultBinaryName},
		{"bare dot", ".", defaultBinaryName},
		{"parent dir", "..", defaultBinaryName},
		{"root slash", "/", defaultBinaryName},
		{"name with a space", "/opt/My Tools/gaia tui", defaultBinaryName},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := binaryName(tt.argv0); got != tt.want {
				t.Errorf("binaryName(%q) = %q, want %q", tt.argv0, got, tt.want)
			}
		})
	}
}

// A `.exe` suffix is stripped, but a name that merely ends in something else is
// left alone — `gaia-tui.dev` is a legitimate binary name, not a Windows one.
func TestBinaryNameOnlyStripsExe(t *testing.T) {
	if got := binaryName("gaia-tui.dev"); got != "gaia-tui.dev" {
		t.Errorf("binaryName stripped a non-.exe extension: got %q", got)
	}
}

// The end-to-end proof that argv[0] reaches the help text lives in
// tui/test/cli_name_test.go, which runs the real binary under several names.
func TestUsageNamesTheInvokedBinary(t *testing.T) {
	original := rootCmd.Use
	t.Cleanup(func() { rootCmd.Use = original })

	rootCmd.Use = binaryName("/usr/local/bin/gaia-tui")
	usage := rootCmd.UsageString()
	if !strings.Contains(usage, "gaia-tui [flags]") {
		t.Errorf("root usage does not name the invoked binary:\n%s", usage)
	}

	runCmd, _, err := rootCmd.Find([]string{"run"})
	if err != nil {
		t.Fatalf("find run command: %v", err)
	}
	if path := runCmd.CommandPath(); !strings.HasPrefix(path, "gaia-tui ") {
		t.Errorf("subcommand path = %q, want it to start with %q", path, "gaia-tui ")
	}
}

// TestMousetrapIsDisabled pins the Explorer guard off. Cobra's default makes
// preExecHook print "This is a command line tool. You need to open cmd.exe and
// run it from there." and exit 1 for any Windows launch not traced to a
// console — a double-click, and also the Start Menu and desktop shortcuts the
// installer creates. Empty is cobra's opt-out; anything else re-breaks every
// shortcut we ship (see command_win.go's `MousetrapHelpText != ""` guard).
func TestMousetrapIsDisabled(t *testing.T) {
	if cobra.MousetrapHelpText != "" {
		t.Fatalf("cobra.MousetrapHelpText = %q, want empty: a non-empty value makes "+
			"the Windows build refuse to start from a shortcut or a double-click",
			cobra.MousetrapHelpText)
	}
}

// TestVersionFlagIsRegistered pins rootCmd.Version non-empty: cobra only wires
// up --version when it is set, and an empty string here is silently invisible
// -- see the flagship-installer plan for why a published binary needs this.
func TestVersionFlagIsRegistered(t *testing.T) {
	if rootCmd.Version == "" {
		t.Fatal("rootCmd.Version is empty, so cobra never registers --version")
	}
}

// TestVersionFlagPrintsVersion checks `--version` prints the same string as
// the `version` subcommand, not a bare cobra default.
func TestVersionFlagPrintsVersion(t *testing.T) {
	var out bytes.Buffer
	rootCmd.SetOut(&out)
	rootCmd.SetErr(&out)
	rootCmd.SetArgs([]string{"--version"})
	defer func() {
		rootCmd.SetArgs(nil)
		rootCmd.SetOut(nil)
		rootCmd.SetErr(nil)
	}()

	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("--version returned an error: %v", err)
	}
	got := out.String()
	if !strings.Contains(got, version) {
		t.Fatalf("--version output %q does not contain version %q", got, version)
	}
	if !strings.Contains(got, "commit:") || !strings.Contains(got, "built:") {
		t.Fatalf("--version output %q does not match the `version` subcommand's format", got)
	}
}

// TestVersionSubcommandMatchesFlag: the two spellings must never drift into
// reporting different things for the same binary.
func TestVersionSubcommandMatchesFlag(t *testing.T) {
	var flagOut bytes.Buffer
	rootCmd.SetOut(&flagOut)
	rootCmd.SetArgs([]string{"--version"})
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("--version returned an error: %v", err)
	}

	var subOut bytes.Buffer
	rootCmd.SetOut(&subOut)
	rootCmd.SetArgs([]string{"version"})
	defer func() {
		rootCmd.SetArgs(nil)
		rootCmd.SetOut(nil)
	}()
	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("version returned an error: %v", err)
	}

	if flagOut.String() != subOut.String() {
		t.Fatalf("--version printed %q but the version subcommand printed %q", flagOut.String(), subOut.String())
	}
}
