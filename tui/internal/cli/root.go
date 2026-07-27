package cli

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/ui"
)

var debug bool

var rootCmd = &cobra.Command{
	Use:   "gaia",
	Short: "GAIA Terminal Agent Hub",
	Long:  "Terminal-native hub for browsing, launching, and chatting with GAIA agents.",
	RunE: func(cmd *cobra.Command, args []string) error {
		ctrl, err := controlOptionsFor(cmd)
		if err != nil {
			return err
		}
		return ui.RunHub(debug, mockAgent, ctrl)
	},
}

func init() {
	rootCmd.PersistentFlags().BoolVar(&debug, "debug", false, "enable debug logging to stderr")
	rootCmd.PersistentFlags().BoolVar(&controlEnabled, "control", false,
		"expose the loopback control API so an assistant can drive this session (auto-assigned port)")
	rootCmd.PersistentFlags().IntVar(&controlPort, "control-port", 0,
		"control API port (implies --control; 0 auto-assigns)")
	rootCmd.Flags().StringVar(&mockAgent, "mock", "", "path to mock agent binary for testing (overrides all agent binaries)")
}

// Execute runs the CLI.
//
// A leading `tui` word is accepted and dropped. This binary is addressed as
// `gaia tui …` everywhere it is documented, but its own root command is `gaia`,
// so without this `gaia tui install email` — the exact line the docs and the
// install refusal tell people to run — would fail with "unknown command". Both
// spellings work; only the first argument is considered, so an agent named
// "tui" is unaffected.
func Execute() error {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "tui" {
		rootCmd.SetArgs(args[1:])
	}
	return rootCmd.Execute()
}

func debugLog(format string, args ...interface{}) {
	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
	}
}
