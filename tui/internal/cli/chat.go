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
	// Everything this command takes is a flag. Without this, a stray argument
	// was accepted and dropped — and `chat --trace out.jsonl` recorded to the
	// default path while the file the user named never appeared.
	Args: func(cmd *cobra.Command, args []string) error {
		if err := traceArgAdvice(args, 0); err != nil {
			return err
		}
		return cobra.NoArgs(cmd, args)
	},
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
				{"use-claude", "you own the command line here — append --use-claude to it yourself"},
				{"claude-model", "you own the command line here — append --claude-model to it yourself"},
			} {
				if cmd.Flags().Changed(f.name) {
					return fmt.Errorf(
						"--%s is not supported with --subprocess: %s. Use `gaia tui chat --agent <id> --%s …` "+
							"(the ids are `gaia` and `email`), or drop --%s",
						f.name, f.why, f.name, f.name)
				}
			}
		}
		if agentID == "" && subprocess == "" {
			return fmt.Errorf("one of --agent or --subprocess is required\n\n" +
				"Usage: gaia tui chat --agent email\n" +
				"       gaia tui chat --agent email --query \"triage my inbox\"\n" +
				"       gaia tui chat --subprocess \"./gaia-bash --json-events\"")
		}
		// Opened only once the command is known to be runnable, so a refused
		// launch never announces a trace it is not going to write.
		trace, err := openTrace(orSubprocess(agentID))
		if err != nil {
			return err
		}
		defer closeTrace(trace)
		if agentID != "" {
			ctrl, err := controlOptionsForAgentRun(cmd, query != "")
			if err != nil {
				return err
			}
			code, err := ui.RunAgent(agentID, query, chatModel, dev, chatTimeout, ctrl,
				bypassPermissions, useClaude, claudeModelArg(), mockAgent, trace)
			if err != nil {
				return err
			}
			if code != 0 {
				// Closed explicitly: os.Exit runs no deferred function, so the
				// trace would never report a recording that stopped early.
				closeTrace(trace)
				// The failure was already rendered to stderr; exit without
				// letting cobra print a second, less useful message.
				os.Exit(code)
			}
			return nil
		}
		ctrl, err := controlOptionsFor(cmd)
		if err != nil {
			return err
		}
		return ui.RunChat(subprocess, query, dev, ctrl, trace)
	},
}

// orSubprocess names the run for the default trace filename. --subprocess has
// no catalog id, so it gets the transport's name rather than another agent's.
func orSubprocess(agentID string) string {
	if agentID != "" {
		return agentID
	}
	return "subprocess"
}

func init() {
	chatCmd.Flags().StringVar(&agentID, "agent", "", "catalog agent id to chat with (e.g. \"email\")")
	chatCmd.Flags().StringVar(&chatModel, "model", "", "model id override (--agent only; the sidecar default is used when unset)")
	chatCmd.Flags().StringVar(&subprocess, "subprocess", "", "command to spawn agent subprocess (e.g. \"./gaia-bash --json-events\")")
	chatCmd.Flags().StringVar(&query, "query", "", "single query to send. With --agent it is a genuine non-interactive one-shot: it refuses in seconds when a precondition is unmet, answers on stdout, and exits 0 answered / 1 failed / 3 needs approval. With --subprocess it opens the interactive chat and sends this as the first message, so it still needs a terminal")
	chatCmd.Flags().DurationVar(&chatTimeout, "timeout", ui.DefaultOneShotTimeout,
		"how long one --query turn may take before it is abandoned and reported (--agent only)")
	rootCmd.AddCommand(chatCmd)
}
