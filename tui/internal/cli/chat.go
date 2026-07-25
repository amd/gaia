package cli

import (
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/ui"
)

var (
	subprocess  string
	query       string
	agentID     string
	chatModel   string
	chatTimeout time.Duration
)

var chatCmd = &cobra.Command{
	Use:   "chat",
	Short: "Start interactive chat with an agent",
	Long: "Launch the chat TUI connected to an agent, either by catalog id " +
		"(--agent, which uses the transport that agent declares) or by spawning a " +
		"binary directly (--subprocess).",
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		if agentID != "" && subprocess != "" {
			return fmt.Errorf("--agent and --subprocess are mutually exclusive: pick one")
		}
		if subprocess != "" {
			// Both were accepted and then silently dropped here — RunChat is
			// given neither. (--query IS honoured: it opens the chat and sends
			// that first message.)
			for _, f := range []struct{ name, why string }{
				{"model", "a subprocess agent's model is fixed by the command you passed"},
				{"timeout", "nothing bounds an interactive session; press ctrl+c to leave it"},
			} {
				if cmd.Flags().Changed(f.name) {
					return fmt.Errorf(
						"--%s is not supported with --subprocess: %s. Use `gaia tui chat --agent <id> --%s …` "+
							"(see `gaia tui list` for ids), or drop --%s",
						f.name, f.why, f.name, f.name)
				}
			}
		}
		if agentID != "" {
			code, err := ui.RunAgent(agentID, query, chatModel, debug, chatTimeout)
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
		ctrl, err := controlOptionsFor(cmd)
		if err != nil {
			return err
		}
		return ui.RunChat(subprocess, query, debug, ctrl)
	},
}

func init() {
	chatCmd.Flags().StringVar(&agentID, "agent", "", "catalog agent id to chat with (e.g. \"email\")")
	chatCmd.Flags().StringVar(&chatModel, "model", "", "model id override (--agent only; the sidecar default is used when unset)")
	chatCmd.Flags().StringVar(&subprocess, "subprocess", "", "command to spawn agent subprocess (e.g. \"./gaia-bash --json-events\")")
	chatCmd.Flags().StringVar(&query, "query", "", "single query to send. With --agent it is a genuine non-interactive one-shot: it refuses in seconds when a precondition is unmet, answers on stdout, and exits 0/1. With --subprocess it opens the interactive chat and sends this as the first message, so it still needs a terminal")
	chatCmd.Flags().DurationVar(&chatTimeout, "timeout", ui.DefaultOneShotTimeout,
		"how long one --query turn may take before it is abandoned and reported (--agent only)")
	rootCmd.AddCommand(chatCmd)
}
