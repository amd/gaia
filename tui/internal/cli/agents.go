package cli

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/charmbracelet/x/ansi"
	"github.com/spf13/cobra"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui"
)

var (
	listInstalledOnly bool
	installVersion    string
	installTrust      bool
	runQuery          string
	runModel          string
	runTimeout        time.Duration
)

// hubClient builds a client over the real daemon, logging through --dev.
func hubClient() *catalog.HubClient {
	return catalog.NewHubClient(func(format string, args ...interface{}) {
		debugLog(format, args...)
	})
}

// --- gaia tui list -----------------------------------------------------------

var listCmd = &cobra.Command{
	Use:   "list",
	Short: "List agents from the Agent Hub with their install state",
	Long: "Print the Agent Hub catalog merged with what is installed locally.\n\n" +
		"--installed answers from the local ~/.gaia/agents/*/.installed sentinels " +
		"and never touches the network, so it works offline.",
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		if listInstalledOnly {
			// Answered from the sentinels on disk, with no daemon and no network
			// — which is what --installed has always promised. Routing it through
			// the daemon meant the offline flag both required a daemon AND
			// started one.
			return printLocalInstalls(cmd.OutOrStdout(), cmd.ErrOrStderr())
		}
		hc := hubClient()
		cat, err := hc.Catalog(context.Background(), true, false, false)
		if err != nil {
			return err
		}
		printCatalog(cmd.OutOrStdout(), cat)
		return nil
	},
}

// printLocalInstalls renders ~/.gaia/agents/*/.installed.
func printLocalInstalls(out, errW io.Writer) error {
	records, warnings := catalog.LocalInstalls()
	for _, w := range warnings {
		// Never swallowed: each of these hides an installed agent, and a short
		// list is indistinguishable from a correct one.
		fmt.Fprintf(errW, "[!] %s\n", w)
	}
	if len(records) == 0 && len(warnings) == 0 {
		fmt.Fprintln(out, "No agents installed. Install one with `gaia tui install <id>`.")
		return nil
	}
	fmt.Fprintln(out, row("ID", "VERSION", "STATE", "SIZE", "SECURITY"))
	for _, r := range records {
		fmt.Fprintln(out, row(r.ID, orDash(r.Version), "installed", "-", "-"))
	}
	fmt.Fprintf(out, "\nRead from %s. Run `gaia tui list` for versions and updates from the Agent Hub.\n",
		catalog.InstallRoot())
	if len(warnings) > 0 {
		// Exit non-zero: each warning hides an installed agent, so a short list
		// is indistinguishable from a correct one and a script must be able to
		// tell them apart.
		return fmt.Errorf("this list is incomplete — %d install record(s) could not be read (see above)", len(warnings))
	}
	return nil
}

func printCatalog(w io.Writer, cat *catalog.HubCatalog) {
	if len(cat.Agents) == 0 {
		fmt.Fprintln(w, "The Agent Hub returned no installable agents.")
		return
	}

	agents := make([]catalog.HubEntry, len(cat.Agents))
	copy(agents, cat.Agents)
	sort.SliceStable(agents, func(i, j int) bool { return agents[i].ID < agents[j].ID })

	fmt.Fprintln(w, row("ID", "VERSION", "STATE", "SIZE", "SECURITY"))
	for _, a := range agents {
		version := a.LatestVersion
		state := "available"
		switch {
		case a.UpdateAvailable:
			version = a.InstalledVersion
			state = "update → " + a.LatestVersion
		case a.Installed:
			version = a.InstalledVersion
			state = "installed"
		case !a.Supervised:
			state = "not runnable"
		}
		tier := a.SecurityTier
		if a.RequiresTrust() {
			// Only for a row the user could still install: "installed … (needs
			// --trust)" reads as an install that never finished.
			if a.Installed {
				tier += " (trusted)"
			} else {
				tier += " (needs --trust)"
			}
		}
		fmt.Fprintln(w, row(a.ID, orDash(version), state,
			catalog.FormatSize(a.DownloadSizeBytes), tier))
	}

	if cat.Offline {
		fmt.Fprintf(w, "\n[!] The Agent Hub was unreachable; this is the cached list from %s.\n",
			orDash(cat.GeneratedAt))
	}
	if len(cat.UnsupervisedFiltered) > 0 {
		fmt.Fprintf(w,
			"\nHidden (%d): %s — published, but this GAIA build has no way to run them yet.\n",
			len(cat.UnsupervisedFiltered), strings.Join(cat.UnsupervisedFiltered, ", "))
	}
}

// --- gaia tui install --------------------------------------------------------

var installCmd = &cobra.Command{
	Use:   "install <agent-id>",
	Short: "Install an agent from the Agent Hub",
	Long: "Download and install an agent, then wait for it to finish.\n\n" +
		"Installing an agent GAIA has not verified runs third-party code on this " +
		"machine, so it is refused until you pass --trust. There is no bypass and " +
		"nothing retries on your behalf.",
	Args:         cobra.ExactArgs(1),
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		return runInstall(cmd.OutOrStdout(), cmd.ErrOrStderr(), args[0], installVersion, installTrust)
	},
}

func runInstall(out, errW io.Writer, agentID, version string, trusted bool) error {
	hc := hubClient()
	ctx := context.Background()

	err := hc.Install(ctx, agentID, version, trusted)
	var trustErr *catalog.TrustRequiredError
	if errors.As(err, &trustErr) {
		// Print what the user would be agreeing to, then stop. Re-running with
		// --trust is a decision they make, not one this command makes for them.
		printTrustRefusal(errW, hc, agentID, trustErr)
		return fmt.Errorf("refused to install '%s' without an explicit trust opt-in", agentID)
	}
	if err != nil {
		return err
	}

	fmt.Fprintf(errW, "Installing %s…\n", agentID)
	progress, err := waitForInstall(ctx, hc, agentID, errW)
	if err != nil {
		return err
	}
	if progress.Status == catalog.InstallFailed {
		return fmt.Errorf("installing '%s' failed: %s", agentID, orDash(progress.Error))
	}
	fmt.Fprintf(out, "Installed %s %s\n", agentID, orDash(progress.Version))
	return nil
}

// printTrustRefusal shows the same facts the TUI's trust gate shows: which
// agent, whose package, which tier, how big, and what it wants access to.
func printTrustRefusal(w io.Writer, hc *catalog.HubClient, agentID string, trustErr *catalog.TrustRequiredError) {
	fmt.Fprintf(w, "\nRefusing to install %q without --trust.\n\n", agentID)

	entry, lookupErr := lookupEntry(hc, agentID)
	switch {
	case lookupErr != nil:
		// Never silent: these are the facts the user decides on, so say that
		// they are missing rather than printing a shorter prompt.
		fmt.Fprintf(w, "  (could not read the catalog to show what you would be trusting: %v)\n\n", lookupErr)
	case entry != nil:
		fmt.Fprintf(w, "  Agent       %s\n", entry.ID)
		fmt.Fprintf(w, "  Version     %s\n", orDash(entry.LatestVersion))
		fmt.Fprintf(w, "  Publisher   %s\n", orDash(entry.Author))
		fmt.Fprintf(w, "  Security    %s (not verified by AMD)\n", orDash(entry.SecurityTier))
		fmt.Fprintf(w, "  Download    %s\n", catalog.FormatSize(entry.DownloadSizeBytes))
		access := "none declared"
		if len(entry.Permissions) > 0 {
			access = strings.Join(entry.Permissions, ", ")
		}
		fmt.Fprintf(w, "  Access      %s\n\n", access)
	}

	// The daemon's detail ends with its own CLI hint, which names a different
	// command than this one. Printing both four lines apart just makes the user
	// guess, so this client's own hint is the one that stands.
	fmt.Fprintf(w, "%s\n\n", catalog.WithoutCLIHint(trustErr.Detail))
	fmt.Fprintf(w, "If you trust the publisher, re-run:\n  gaia tui install %s --trust\n", agentID)
}

func lookupEntry(hc *catalog.HubClient, agentID string) (*catalog.HubEntry, error) {
	cat, err := hc.Catalog(context.Background(), true, false, false)
	if err != nil {
		return nil, err
	}
	for i := range cat.Agents {
		if cat.Agents[i].ID == agentID {
			return &cat.Agents[i], nil
		}
	}
	return nil, fmt.Errorf("the daemon's catalog no longer lists %q", agentID)
}

// waitForInstall polls until the install reaches a terminal state, printing a
// line each time the phase changes.
func waitForInstall(
	ctx context.Context,
	hc *catalog.HubClient,
	agentID string,
	errW io.Writer,
) (*catalog.InstallProgress, error) {
	const pollEvery = 500 * time.Millisecond
	const deadline = 15 * time.Minute

	lastPhase := ""
	started := time.Now()
	for {
		progress, err := hc.InstallStatus(ctx, agentID)
		if err != nil {
			return nil, err
		}
		if progress.Phase != lastPhase && progress.Phase != "" {
			fmt.Fprintf(errW, "  %s (%.0f%%)\n", progress.Phase, progress.Percent)
			lastPhase = progress.Phase
		}
		if progress.Terminal() {
			return progress, nil
		}
		if time.Since(started) > deadline {
			return nil, fmt.Errorf(
				"the daemon stopped reporting progress for '%s' after %s (last phase %q). "+
					"Read `gaia daemon logs`, then retry", agentID, deadline, orDash(lastPhase))
		}
		time.Sleep(pollEvery)
	}
}

// --- gaia tui uninstall ------------------------------------------------------

var uninstallCmd = &cobra.Command{
	Use:          "uninstall <agent-id>",
	Short:        "Remove an installed agent",
	Long:         "Stop the agent's sidecar, verify it is gone, then remove its install directory.",
	Args:         cobra.ExactArgs(1),
	SilenceUsage: true,
	RunE: func(cmd *cobra.Command, args []string) error {
		if err := hubClient().Uninstall(context.Background(), args[0]); err != nil {
			return err
		}
		fmt.Fprintf(cmd.OutOrStdout(), "Uninstalled %s\n", args[0])
		return nil
	},
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
		code, err := ui.RunAgent(args[0], runQuery, runModel, dev, runTimeout, ctrl, bypassPermissions)
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
			"fixed by the catalog entry, not chosen per run. Drop --model, or run an agent "+
			"the background service supervises (`gaia tui list`)", agentID)
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
		fmt.Fprintln(w, "  (none) — install one with `gaia tui install <id>`")
		return nil
	}
	for _, a := range installed.Agents {
		fmt.Fprintf(w, "  %-14s %s\n", a.ID, orDash(a.InstalledVersion))
	}
	return nil
}

// catalogColumns are the display widths of `gaia tui list`'s columns.
var catalogColumns = []int{14, 10, 16, 11, 0}

// row pads cells by DISPLAY width, not bytes. `%-12s` counts bytes, so a cell
// containing "→" (3 bytes, 1 column) came out short and skewed the table.
func row(cells ...string) string {
	var b strings.Builder
	for i, cell := range cells {
		b.WriteString(cell)
		if i == len(cells)-1 {
			break
		}
		pad := catalogColumns[i] - ansi.StringWidth(cell)
		if pad < 1 {
			pad = 1
		}
		b.WriteString(strings.Repeat(" ", pad))
	}
	return b.String()
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func init() {
	listCmd.Flags().BoolVar(&listInstalledOnly, "installed", false,
		"only what is installed locally, read from disk (no daemon, no network)")

	installCmd.Flags().StringVar(&installVersion, "version", "",
		"version to install (default: the hub's latest)")
	installCmd.Flags().BoolVar(&installTrust, "trust", false,
		"acknowledge that a non-verified agent runs third-party code on this machine")

	runCmd.Flags().StringVar(&runQuery, "query", "",
		"run one query non-interactively: answer on stdout, exit 0 answered / "+
			"1 failed / 3 needs approval")
	runCmd.Flags().StringVar(&runModel, "model", "",
		"model id override (the agent's default is used when unset)")
	runCmd.Flags().DurationVar(&runTimeout, "timeout", ui.DefaultOneShotTimeout,
		"how long one --query turn may take before it is abandoned and reported (e.g. 90s, 2h)")

	rootCmd.AddCommand(listCmd, installCmd, uninstallCmd, runCmd, statusCmd)
}
