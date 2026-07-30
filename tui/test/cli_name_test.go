package test

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// TestHelpNamesTheInvokedBinary runs the real binary under the name the
// installer ships it as. A hardcoded root command printed `gaia` here, telling
// users to type a command they do not have — the Python CLI owns `gaia`, and it
// has none of these subcommands.
//
// This spawns the built binary on purpose: asserting on rootCmd.Use in-process
// cannot catch the argv[0] wiring being dropped.
func TestHelpNamesTheInvokedBinary(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	suffix := ""
	if runtime.GOOS == "windows" {
		suffix = ".exe"
	}

	for _, name := range []string{"gaia-tui", "gaia", "gaia-hub"} {
		t.Run(name, func(t *testing.T) {
			installed := filepath.Join(t.TempDir(), name+suffix)
			copyExecutable(t, gaiaBin, installed)

			out, err := exec.Command(installed, "--help").CombinedOutput()
			if err != nil {
				t.Fatalf("%s --help: %v\n%s", name, err, out)
			}
			if want := name + " [flags]"; !strings.Contains(string(out), want) {
				t.Errorf("help does not name the invoked binary (want %q):\n%s", want, out)
			}

			// Subcommand help is built from the same root name.
			out, err = exec.Command(installed, "install", "--help").CombinedOutput()
			if err != nil {
				t.Fatalf("%s install --help: %v\n%s", name, err, out)
			}
			if want := name + " install"; !strings.Contains(string(out), want) {
				t.Errorf("subcommand usage does not name the invoked binary (want %q):\n%s", want, out)
			}
		})
	}
}

// TestLeadingTUIWordStillAccepted pins the documented `gaia tui …` spelling:
// the first argument is dropped when it is `tui`, whatever the binary is named.
func TestLeadingTUIWordStillAccepted(t *testing.T) {
	gaiaBin, _ := buildBinaries(t)

	suffix := ""
	if runtime.GOOS == "windows" {
		suffix = ".exe"
	}
	installed := filepath.Join(t.TempDir(), "gaia-tui"+suffix)
	copyExecutable(t, gaiaBin, installed)

	out, err := exec.Command(installed, "tui", "version").CombinedOutput()
	if err != nil {
		t.Fatalf("gaia-tui tui version: %v\n%s", err, out)
	}
	direct, err := exec.Command(installed, "version").CombinedOutput()
	if err != nil {
		t.Fatalf("gaia-tui version: %v\n%s", err, direct)
	}
	if string(out) != string(direct) {
		t.Errorf("`tui version` and `version` diverged:\n got %q\nwant %q", out, direct)
	}
}

func copyExecutable(t *testing.T, src, dst string) {
	t.Helper()
	data, err := os.ReadFile(src)
	if err != nil {
		t.Fatalf("read %s: %v", src, err)
	}
	if err := os.WriteFile(dst, data, 0o755); err != nil {
		t.Fatalf("write %s: %v", dst, err)
	}
}
