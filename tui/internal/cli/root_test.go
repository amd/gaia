package cli

import (
	"path/filepath"
	"strings"
	"testing"
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

	listCmd, _, err := rootCmd.Find([]string{"list"})
	if err != nil {
		t.Fatalf("find list command: %v", err)
	}
	if path := listCmd.CommandPath(); !strings.HasPrefix(path, "gaia-tui ") {
		t.Errorf("subcommand path = %q, want it to start with %q", path, "gaia-tui ")
	}
}
