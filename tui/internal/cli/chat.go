package cli

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/ui"
)

var (
	subprocess string
	query      string
)

var chatCmd = &cobra.Command{
	Use:   "chat",
	Short: "Start interactive chat with an agent",
	Long:  "Launch the Bubble Tea chat TUI connected to an agent via subprocess or API.",
	RunE: func(cmd *cobra.Command, args []string) error {
		if subprocess == "" {
			return fmt.Errorf("--subprocess flag is required\n\nUsage: gaia chat --subprocess \"./gaia-bash --json-events\"")
		}
		return ui.RunChat(subprocess, query, debug)
	},
}

func init() {
	chatCmd.Flags().StringVar(&subprocess, "subprocess", "", "command to spawn agent subprocess (e.g. \"./gaia-bash --json-events\")")
	chatCmd.Flags().StringVar(&query, "query", "", "single query to send (non-interactive mode)")
	rootCmd.AddCommand(chatCmd)
}
