package cli

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/ui"
)

// dev is developer mode: rich in-TUI output (per-turn timings, step and turn
// boundaries, tool arguments and truncated tool output, raw harness statuses)
// and DEBUG-level file logging in the agent it spawns.
//
// One flag, not two. `--debug` already meant exactly this in the TUI, and the
// agent half of the same feature shipped as `--dev`; keeping both names as
// separate switches would give one idea two spellings that could disagree. So
// `--dev` is the name and `--debug` is a hidden alias onto this same variable
// (see init) — old scripts and docs keep working, help lists one flag.
var dev bool

const defaultBinaryName = "gaia-tui"

// binaryName derives the command name from argv[0]. The installer ships this as
// `gaia-tui` because the Python CLI owns `gaia`, so a hardcoded name would print
// usage lines for a command the user does not have.
func binaryName(argv0 string) string {
	name := filepath.Base(strings.TrimSpace(argv0))
	if ext := filepath.Ext(name); strings.EqualFold(ext, ".exe") {
		name = strings.TrimSuffix(name, ext)
	}
	switch name {
	case "", ".", "..", "/", `\`:
		return defaultBinaryName
	}
	// Cobra takes the command name from the first word of Use, so a name with
	// whitespace in it would be silently truncated.
	if strings.ContainsAny(name, " \t") {
		return defaultBinaryName
	}
	return name
}

var rootCmd = &cobra.Command{
	Use:   defaultBinaryName,
	Short: "GAIA Terminal Agent Hub",
	Long:  "Terminal-native hub for browsing, launching, and chatting with GAIA agents.",
	// A one-line refusal followed by 20 lines of command listing pushes the
	// actual error off a short terminal. Usage is what --help is for.
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		ctrl, err := controlOptionsFor(cmd)
		if err != nil {
			return err
		}
		return ui.RunHub(dev, mockAgent, ctrl)
	},
}

func init() {
	rootCmd.PersistentFlags().BoolVar(&dev, "dev", false,
		"developer mode: show per-turn timings, steps, tool arguments and output, and log the agent at DEBUG to ~/.gaia/logs/gaia-agent.log")
	// Same variable as --dev, hidden: the previous name for this mode. Kept so
	// existing scripts and docs do not break, out of --help so the two spellings
	// never read as two features.
	rootCmd.PersistentFlags().BoolVar(&dev, "debug", false, "deprecated alias for --dev")
	if err := rootCmd.PersistentFlags().MarkHidden("debug"); err != nil {
		panic(err) // only fails on a flag name that was never registered
	}
	rootCmd.PersistentFlags().BoolVar(&controlEnabled, "control", false,
		"expose the loopback control API so an assistant can drive this session (auto-assigned port)")
	rootCmd.PersistentFlags().IntVar(&controlPort, "control-port", 0,
		"control API port (implies --control; 0 auto-assigns)")
	rootCmd.Flags().StringVar(&mockAgent, "mock", "", "path to mock agent binary for testing (overrides all agent binaries)")
}

// Execute runs the CLI.
//
// A leading `tui` word is accepted and dropped. This binary is addressed as
// `gaia tui …` everywhere it is documented, but its own root command is the
// binary's own name, so without this `gaia tui install email` — the exact line
// the docs and the install refusal tell people to run — would fail with
// "unknown command". Both spellings work; only the first argument is
// considered, so an agent named "tui" is unaffected.
func Execute() error {
	rootCmd.Use = binaryName(os.Args[0])
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "tui" {
		rootCmd.SetArgs(args[1:])
	}
	return rootCmd.Execute()
}

func debugLog(format string, args ...interface{}) {
	if dev {
		fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
	}
}
