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

func Execute() error {
	return rootCmd.Execute()
}

func debugLog(format string, args ...interface{}) {
	if debug {
		fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
	}
}
