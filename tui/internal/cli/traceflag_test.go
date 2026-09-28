// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// parseTraceFlag runs argv through the root command's flag set and returns what
// --trace resolved to. It parses only; nothing is launched.
func parseTraceFlag(t *testing.T, argv ...string) (string, error) {
	t.Helper()
	tracePath = ""
	t.Cleanup(func() { tracePath = "" })
	flags := rootCmd.PersistentFlags()
	err := flags.Parse(argv)
	return tracePath, err
}

func TestTraceFlagFormsResolve(t *testing.T) {
	t.Run("absent means off", func(t *testing.T) {
		got, err := parseTraceFlag(t)
		if err != nil {
			t.Fatalf("parse: %v", err)
		}
		if got != "" {
			t.Errorf("--trace = %q with the flag absent, want \"\" (tracing off)", got)
		}
	})

	t.Run("bare picks the default path", func(t *testing.T) {
		got, err := parseTraceFlag(t, "--trace")
		if err != nil {
			t.Fatalf("bare --trace was rejected: %v", err)
		}
		if got != traceAutoPath {
			t.Errorf("bare --trace = %q, want the auto sentinel", got)
		}
	})

	// pflag prints NoOptDefVal verbatim in --help, so the sentinel has to be a
	// word. An unprintable one lands in the flag listing.
	t.Run("the sentinel is printable", func(t *testing.T) {
		for _, r := range traceAutoPath {
			if r < 0x20 || r == 0x7f {
				t.Fatalf("traceAutoPath contains %q, which --help will render raw", r)
			}
		}
	})

	t.Run("attached path wins", func(t *testing.T) {
		got, err := parseTraceFlag(t, "--trace=/tmp/run.jsonl")
		if err != nil {
			t.Fatalf("parse: %v", err)
		}
		if got != "/tmp/run.jsonl" {
			t.Errorf("--trace=<path> = %q, want the path", got)
		}
	})
}

// pflag will not attach a SPACED value to a NoOptDefVal flag, so
// `--trace out.jsonl` silently records to the default path and leaves
// out.jsonl to be reported as an unknown command. The root command's Args
// hook has to name the real fix instead.
func TestTraceFlagRejectsASpacedPathWithTheFix(t *testing.T) {
	if _, err := parseTraceFlag(t, "--trace"); err != nil {
		t.Fatalf("parse: %v", err)
	}
	err := rootCmd.Args(rootCmd, []string{"out.jsonl"})
	if err == nil {
		t.Fatal("a spaced --trace path was accepted; it would have recorded to the default path instead")
	}
	if !strings.Contains(err.Error(), "--trace=out.jsonl") {
		t.Errorf("the error does not show the working form: %v", err)
	}

	// A misspelled COMMAND is still a misspelled command with --trace on. The
	// trace advice must not swallow the suggestion it deserves.
	typo := rootCmd.Args(rootCmd, []string{"chatt"})
	if typo == nil {
		t.Fatal("a misspelled subcommand was accepted")
	}
	if !strings.Contains(typo.Error(), "Did you mean") {
		t.Errorf("--trace swallowed the typo suggestion: %v", typo)
	}
	if strings.Contains(typo.Error(), "--trace=") {
		t.Errorf("a misspelled command was reported as a misplaced trace path: %v", typo)
	}
}

// --trace is a PERSISTENT flag, so the spaced-path trap exists on every
// subcommand too. It used to be caught only on the root command: `chat --trace
// out.jsonl` silently recorded to the default path and never created the file
// the user named, and `run <agent> --trace out.jsonl` said only "accepts 1
// arg(s), received 2".
func TestTraceSpacedPathIsCaughtOnEverySubcommand(t *testing.T) {
	for _, tc := range []struct {
		name string
		cmd  *cobra.Command
		args []string
	}{
		{"root", rootCmd, []string{"out.jsonl"}},
		{"chat", chatCmd, []string{"out.jsonl"}},
		{"run", runCmd, []string{"email", "out.jsonl"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := parseTraceFlag(t, "--trace"); err != nil {
				t.Fatalf("parse: %v", err)
			}
			if tc.cmd.Args == nil {
				t.Fatalf("%s declares no Args hook, so a stray --trace path is swallowed", tc.name)
			}
			err := tc.cmd.Args(tc.cmd, tc.args)
			if err == nil {
				t.Fatalf("%s accepted a spaced --trace path; it would record to the default path", tc.name)
			}
			if !strings.Contains(err.Error(), "--trace=out.jsonl") {
				t.Errorf("%s does not name the working form: %v", tc.name, err)
			}
		})
	}
}

// The advice must not fire on arguments the command legitimately takes, nor
// when --trace was given a real path.
func TestTraceArgAdviceStaysOutOfTheWay(t *testing.T) {
	t.Run("run keeps its agent id", func(t *testing.T) {
		if _, err := parseTraceFlag(t, "--trace"); err != nil {
			t.Fatalf("parse: %v", err)
		}
		if err := runCmd.Args(runCmd, []string{"email"}); err != nil {
			t.Errorf("`run email --trace` was rejected: %v", err)
		}
	})

	t.Run("an attached path leaves a stray arg to the normal error", func(t *testing.T) {
		if _, err := parseTraceFlag(t, "--trace=/tmp/run.jsonl"); err != nil {
			t.Fatalf("parse: %v", err)
		}
		err := chatCmd.Args(chatCmd, []string{"nonsense"})
		if err == nil {
			t.Fatal("a stray argument was accepted")
		}
		if strings.Contains(err.Error(), "--trace=") {
			t.Errorf("an unrelated stray argument was blamed on --trace: %v", err)
		}
	})

	t.Run("no --trace at all", func(t *testing.T) {
		if _, err := parseTraceFlag(t); err != nil {
			t.Fatalf("parse: %v", err)
		}
		if err := traceArgAdvice([]string{"whatever"}, 0); err != nil {
			t.Errorf("advice fired without --trace: %v", err)
		}
	})
}

// Without --trace, a stray argument is still a bad command and must say so —
// the Args hook replaced cobra's own check, so it owns this case too.
func TestStrayArgumentIsStillRejected(t *testing.T) {
	if _, err := parseTraceFlag(t); err != nil {
		t.Fatalf("parse: %v", err)
	}
	err := rootCmd.Args(rootCmd, []string{"nonsense"})
	if err == nil {
		t.Fatal("a stray argument was accepted")
	}
	if !strings.Contains(err.Error(), "unknown command") {
		t.Errorf("unexpected message: %v", err)
	}
	// The hook replaced cobra's legacyArgs, so it owes the same typo help.
	near := rootCmd.Args(rootCmd, []string{"chatt"})
	if near == nil || !strings.Contains(near.Error(), "Did you mean") {
		t.Errorf("a near-miss command lost its suggestion: %v", near)
	}
	if rootCmd.Args(rootCmd, nil) != nil {
		t.Error("a bare launch with no arguments was rejected")
	}
}

func TestOpenTraceIsOffUnlessAsked(t *testing.T) {
	tracePath = ""
	t.Cleanup(func() { tracePath = "" })
	w, err := openTrace("gaia")
	if err != nil {
		t.Fatalf("openTrace: %v", err)
	}
	if w != nil {
		w.Close()
		t.Error("a writer was opened without --trace")
	}
}

func TestOpenTraceHonoursAnExplicitPath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "run.jsonl")
	tracePath = path
	t.Cleanup(func() { tracePath = "" })

	w, err := openTrace("gaia")
	if err != nil {
		t.Fatalf("openTrace: %v", err)
	}
	defer w.Close()
	if w.Path() != path {
		t.Errorf("Path() = %q, want %q", w.Path(), path)
	}
}

// An unwritable --trace has to stop the launch. A TUI that opens anyway records
// nothing, and "the agent did nothing" is indistinguishable from "nothing was
// recorded" — which is the confusion this flag exists to remove.
func TestOpenTraceFailsOnAnUnwritablePath(t *testing.T) {
	dir := t.TempDir()
	tracePath = dir // a directory can never be opened as the trace FILE
	t.Cleanup(func() { tracePath = "" })

	if w, err := openTrace("gaia"); err == nil {
		w.Close()
		t.Fatal("an unwritable --trace was accepted")
	}
}

// --dev and --trace are orthogonal: one renders, the other records. Cross-linked
// in help so they are discoverable together, but never bound to each other.
func TestTraceAndDevAreIndependent(t *testing.T) {
	flags := rootCmd.PersistentFlags()
	traceFlag := flags.Lookup("trace")
	if traceFlag == nil {
		t.Fatal("--trace is not registered")
	}
	if traceFlag.Hidden {
		t.Error("--trace is hidden; a recorder nobody can find does not exist")
	}
	if !strings.Contains(flags.Lookup("dev").Usage, "--trace") {
		t.Error("--dev's help does not mention --trace, so the two are not discoverable together")
	}
	// The scope limit belongs in --help: this records behaviour, not tokens.
	if !strings.Contains(traceFlag.Usage, "GAIA_TURN_LOG") {
		t.Error("--trace's help does not say what it cannot capture")
	}

	dev = false
	tracePath = ""
	t.Cleanup(func() { dev = false; tracePath = "" })
	if err := flags.Set("trace", "/tmp/x.jsonl"); err != nil {
		t.Fatalf("set --trace: %v", err)
	}
	if dev {
		t.Error("--trace turned on developer mode")
	}
	if err := flags.Set("dev", "true"); err != nil {
		t.Fatalf("set --dev: %v", err)
	}
	if tracePath != "/tmp/x.jsonl" {
		t.Error("--dev changed the trace path")
	}
}
