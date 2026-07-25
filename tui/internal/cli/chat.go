package cli

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/ui"
)

var (
	subprocess string
	query      string
	agentID    string
	chatModel  string
)

var chatCmd = &cobra.Command{
	Use:   "chat",
	Short: "Start interactive chat with an agent",
	Long: "Launch the chat TUI connected to an agent, either by catalog id " +
		"(--agent, which uses the transport that agent declares) or by spawning a " +
		"binary directly (--subprocess).",
	RunE: func(cmd *cobra.Command, args []string) error {
		if agentID != "" && subprocess != "" {
			return fmt.Errorf("--agent and --subprocess are mutually exclusive: pick one")
		}
		if agentID != "" {
			code, err := ui.RunAgent(agentID, query, chatModel, debug)
			if err != nil {
				return err
			}
			if code != 0 {
				// The failure was already rendered to stderr; exit without
				// letting cobra print a second, less useful message.
				os.Exit(code)
			}
			return nil
		}
		if subprocess == "" {
			return fmt.Errorf("one of --agent or --subprocess is required\n\n" +
				"Usage: gaia tui chat --agent email\n" +
				"       gaia tui chat --agent email --query \"triage my inbox\"\n" +
				"       gaia tui chat --subprocess \"./gaia-bash --json-events\"")
		}
		return ui.RunChat(subprocess, query, debug)
	},
}

func init() {
	chatCmd.Flags().StringVar(&agentID, "agent", "", "catalog agent id to chat with (e.g. \"email\")")
	chatCmd.Flags().StringVar(&chatModel, "model", "", "model id override (--agent only; the sidecar default is used when unset)")
	chatCmd.Flags().StringVar(&subprocess, "subprocess", "", "command to spawn agent subprocess (e.g. \"./gaia-bash --json-events\")")
	chatCmd.Flags().StringVar(&query, "query", "", "single query to send; with --agent this is a genuine non-interactive one-shot (answer on stdout, exit 0/1)")
	rootCmd.AddCommand(chatCmd)
}
