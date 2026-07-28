package cli

import (
	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/ui"
)

var mockAgent string

var hubCmd = &cobra.Command{
	Use:          "hub",
	Short:        "Browse and launch GAIA agents",
	Long:         "Open the Agent Hub to discover, search, and launch GAIA agents.",
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		ctrl, err := controlOptionsFor(cmd)
		if err != nil {
			return err
		}
		return ui.RunHub(debug, mockAgent, ctrl)
	},
}

func init() {
	hubCmd.Flags().StringVar(&mockAgent, "mock", "", "path to mock agent binary for testing (overrides all agent binaries)")
	rootCmd.AddCommand(hubCmd)
}
