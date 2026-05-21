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
		return ui.RunHub(debug, mockAgent)
	},
}

func init() {
	rootCmd.PersistentFlags().BoolVar(&debug, "debug", false, "enable debug logging to stderr")
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
