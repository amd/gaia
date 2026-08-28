package cli

import (
	"context"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui"
)

var (
	runQuery   string
	runModel   string
	runTimeout time.Duration
)

// hubClient builds a client over the real daemon, logging through --dev.
func hubClient() *catalog.HubClient {
	return catalog.NewHubClient(func(format string, args ...interface{}) {
		debugLog(format, args...)
	})
}

// --- gaia tui run ------------------------------------------------------------

var runCmd = &cobra.Command{
	Use:   "run <agent-id>",
	Short: "Run an agent — interactive chat, or a one-shot with --query",
	Long: "Open the chat TUI for an installed agent.\n\n" +
		"--query makes it a genuine non-interactive one-shot: no alt screen, the " +
		"answer on stdout, progress on stderr, and an exit code a script can act " +
		"on. That is what makes it usable from a script or from CI.\n\n" +
		"  exit 0  the agent answered and nothing reported a failure\n" +
		"  exit 1  an error, or a tool failed and nothing recovered it — even when " +
		"the agent still wrote an answer, so `gaia tui run … && next-step` does not " +
		"fire over work that never happened\n" +
		"  exit 3  a confirmation gate held back a destructive action this run has " +
		"no way to approve: nothing broke, and nothing was done\n\n" +
		"A tool that reports no outcome at all is named on stderr as unverified " +
		"rather than counted as a success.\n\n" +
		"A one-shot checks its preconditions first and refuses in seconds — naming " +
		"the unmet one and the command that fixes it on stderr — rather than waiting " +
		"on a model server that is not there. The turn itself is bounded too, so an " +
		"agent that accepts the query and then goes quiet is reported, never waited " +
		"on forever — raise or lower that bound with --timeout.",
	Args:         cobra.ExactArgs(1),
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		if err := checkModelSupported(args[0], runModel); err != nil {
			return err
		}
		ctrl, err := controlOptionsForAgentRun(cmd, runQuery != "")
		if err != nil {
			return err
		}
		code, err := ui.RunAgent(args[0], runQuery, runModel, dev, runTimeout, ctrl,
			bypassPermissions, useClaude, claudeModelArg(), mockAgent)
		if err != nil {
			return err
		}
		if code != 0 {
			// The failure was already rendered to stderr; exit without letting
			// cobra print a second, less useful message.
			os.Exit(code)
		}
		return nil
	},
}

// checkModelSupported refuses --model on a transport that cannot honour it.
//
// A subprocess agent takes its model from the binary args the catalog declares,
// so the flag was accepted and silently dropped — the run then used a different
// model than the one asked for, with nothing said about it.
func checkModelSupported(agentID, model string) error {
	if model == "" {
		return nil
	}
	cat := catalog.NewCatalog()
	cat.DiscoverBinaries()
	agent := cat.Get(agentID)
	if agent == nil || agent.Transport == catalog.TransportDaemon {
		return nil
	}
	return fmt.Errorf(
		"--model is not supported for '%s': it runs as a local subprocess whose model is "+
			"fixed by the catalog entry, not chosen per run. Drop --model, or use /model "+
			"inside the chat to switch the model this session runs on", agentID)
}

// --- gaia tui status ---------------------------------------------------------

var statusCmd = &cobra.Command{
	Use:          "status",
	Short:        "Show the background service and what is installed",
	Args:         cobra.NoArgs,
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		return printStatus(cmd.OutOrStdout())
	},
}

func printStatus(w io.Writer) error {
	hc := hubClient()
	ctx := context.Background()

	// Attach only: `status` must report what IS running, not start something.
	agents, err := hc.Agents(ctx, false)
	if err != nil {
		fmt.Fprintln(w, "Background service:  not reachable")
		// Non-zero, so `gaia tui status || <alert>` fires. A status command
		// that exits 0 while the thing it reports on is down is unusable in a
		// script.
		return fmt.Errorf("the GAIA background service is not reachable: %w", err)
	}
	inst := hc.Instance()
	fmt.Fprintf(w, "Background service:  running (pid %d, port %d, api %s)\n\n",
		inst.PID, inst.Port, inst.APIVersion)

	fmt.Fprintf(w, "%-14s %-10s %-8s %s\n", "AGENT", "STATE", "PID", "VERSION")
	for _, a := range agents {
		pid := "-"
		if a.PID > 0 {
			pid = fmt.Sprintf("%d", a.PID)
		}
		fmt.Fprintf(w, "%-14s %-10s %-8s %s\n", a.AgentID, orDash(a.State), pid, orDash(a.AgentVersion))
	}

	installed, err := hc.Catalog(ctx, false, true, false)
	if err != nil {
		return err
	}
	fmt.Fprintf(w, "\nInstalled in %s:\n", catalog.InstallRoot())
	if len(installed.Agents) == 0 {
		fmt.Fprintln(w, "  (none) — install one with `gaia hub install <id>`")
		return nil
	}
	for _, a := range installed.Agents {
		fmt.Fprintf(w, "  %-14s %s\n", a.ID, orDash(a.InstalledVersion))
	}
	return nil
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func init() {
	runCmd.Flags().StringVar(&runQuery, "query", "",
		"run one query non-interactively: answer on stdout, exit 0 answered / "+
			"1 failed / 3 needs approval")
	runCmd.Flags().StringVar(&runModel, "model", "",
		"model id override (the agent's default is used when unset)")
	runCmd.Flags().DurationVar(&runTimeout, "timeout", ui.DefaultOneShotTimeout,
		"how long one --query turn may take before it is abandoned and reported (e.g. 90s, 2h)")

	rootCmd.AddCommand(runCmd, statusCmd)
}
