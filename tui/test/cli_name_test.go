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
			out, err = exec.Command(installed, "run", "--help").CombinedOutput()
			if err != nil {
				t.Fatalf("%s run --help: %v\n%s", name, err, out)
			}
			if want := name + " run"; !strings.Contains(string(out), want) {
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

	for _, name := range []string{"gaia-tui", "gaia"} {
		t.Run(name, func(t *testing.T) {
			installed := filepath.Join(t.TempDir(), name+suffix)
			copyExecutable(t, gaiaBin, installed)

			// Both need no daemon, and `gaia tui <sub>` is the documented
			// line — it must reach the same subcommand rather than dying on
			// "unknown command".
			for _, sub := range []string{"version", "help"} {
				viaTUI, _ := exec.Command(installed, "tui", sub).CombinedOutput()
				direct, _ := exec.Command(installed, sub).CombinedOutput()
				if string(viaTUI) != string(direct) {
					t.Errorf("`%s tui %s` and `%s %s` diverged:\n got %q\nwant %q",
						name, sub, name, sub, viaTUI, direct)
				}
				if strings.Contains(string(viaTUI), "unknown command") {
					t.Errorf("`%s tui %s` was not recognised:\n%s", name, sub, viaTUI)
				}
			}
		})
	}
}

// TestSymlinkedBinaryNamesTheLink covers the packaging shape where a symlink on
// PATH points at a differently-named real binary (Homebrew, /usr/local/bin).
// argv[0] is the link the user invoked, which is the name to print — resolving
// it to the target would print a name they do not have.
func TestSymlinkedBinaryNamesTheLink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation needs elevation on Windows")
	}
	gaiaBin, _ := buildBinaries(t)

	target := filepath.Join(t.TempDir(), "gaia-tui-v2")
	copyExecutable(t, gaiaBin, target)

	link := filepath.Join(t.TempDir(), "gaia-tui")
	if err := os.Symlink(target, link); err != nil {
		t.Fatalf("symlink: %v", err)
	}

	out, err := exec.Command(link, "--help").CombinedOutput()
	if err != nil {
		t.Fatalf("%s --help: %v\n%s", link, err, out)
	}
	if !strings.Contains(string(out), "gaia-tui [flags]") {
		t.Errorf("help names the symlink target, not the link the user invoked:\n%s", out)
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
