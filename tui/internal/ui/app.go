package ui

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// prepareTerminal does everything that has to TALK to the terminal before
// Bubble Tea takes over stdin. Every full-screen launch path calls it.
func prepareTerminal() {
	// First: it caches the light/dark answer that PrimeRenderer then reads, so
	// the markdown style and the palette can never disagree, and a
	// GAIA_TUI_THEME override reaches both.
	theme.Init()
	if err := components.PrimeRenderer(); err != nil {
		fmt.Fprintf(os.Stderr,
			"%v — replies will be shown as plain text. Report this with `gaia diagnostics`.\n", err)
	}
}

// RunHub launches the Agent Hub TUI — the main entry point for browsing and launching agents.
// If mockAgent is non-empty, all agent binary paths are overridden with it for testing.
// A non-nil ctrl starts the loopback control API against this very program.
func RunHub(debug bool, mockAgent string, ctrl *control.Options) error {
	cat := catalog.NewCatalog()
	if mockAgent != "" {
		cat.SetMockBinary(mockAgent)
	} else {
		cat.DiscoverBinaries()
	}
	return run(root.NewRootModel(cat, debug), debug, ctrl)
}

// RunChat launches the chat TUI directly with a subprocess agent (standalone mode).
//
// subprocess is a command line, so it is split with quoting honoured — a binary
// path containing a space must be quoted, not silently torn in two.
func RunChat(subprocess string, query string, debug bool, ctrl *control.Options) error {
	argv, err := client.SplitCommandLine(subprocess)
	if err != nil {
		return fmt.Errorf("invalid --subprocess command: %w", err)
	}
	// Checked before the alt screen opens: otherwise the chat says "connected"
	// and the exec failure only surfaces when the user sends their first message.
	bin, err := catalog.ResolveExecutable(argv[0], agentNameFromPath(argv[0]))
	if err != nil {
		return fmt.Errorf("cannot start --subprocess %q: %w", argv[0], err)
	}

	c := client.NewSubprocessClient(bin, argv[1:], debug)
	defer c.Close()

	return run(chat.NewChatModel(c, agentNameFromPath(argv[0]), query, debug), debug, ctrl)
}

// run boots the Bubble Tea program, optionally wrapping it with the control
// recorder so the live session can be driven over HTTP.
func run(model tea.Model, debug bool, ctrl *control.Options) error {
	prepareTerminal()

	// Swept whether or not this run publishes one of its own: a session started
	// WITHOUT --control used to leave a dead predecessor's file in place.
	if removed, err := control.ClearStale(daemon.PIDAlive); err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
	} else if removed && debug {
		fmt.Fprintln(os.Stderr, "[DEBUG] removed a stale control discovery file")
	}

	if ctrl == nil {
		p := tea.NewProgram(model, tea.WithAltScreen())
		if _, err := p.Run(); err != nil {
			return fmt.Errorf("TUI error: %w", err)
		}
		return nil
	}

	// One debug switch for both halves: --debug on the TUI implies control
	// logging, and the server must not end up quieter than the recorder.
	opts := *ctrl
	opts.Debug = opts.Debug || debug
	state := control.NewState(control.Debugf(opts.Debug))
	p := tea.NewProgram(control.NewRecorder(model, state), tea.WithAltScreen())

	srv, err := control.Start(p, state, opts)
	if err != nil {
		return err
	}
	defer func() {
		if stopErr := srv.Stop(); stopErr != nil {
			fmt.Fprintf(os.Stderr, "%v\n", stopErr)
		}
	}()

	// A deferred Stop covers a normal exit only; a signalled one has to remove
	// the discovery file too, or it keeps advertising a pid, a port and a token.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	defer func() {
		signal.Stop(sigs)
		close(sigs)
	}()
	go func() {
		srv.WatchTermination(sigs, p.Quit)
		// Default disposition restored after the first one, so a second ctrl+c
		// still kills a quit that is wedged.
		signal.Stop(sigs)
	}()

	fmt.Fprintf(os.Stderr, "control API listening on %s:%d — token in %s\n",
		control.Host, srv.Port(), srv.DiscoveryPath())

	// Bracket Run: tea.Program.Send silently discards messages outside it, so
	// the control API must refuse injection rather than report a false success.
	srv.MarkRunning()
	_, err = p.Run()
	srv.MarkStopped()
	if err != nil {
		return fmt.Errorf("TUI error: %w", err)
	}
	return nil
}

// RunAgent launches one catalog agent by id, over whatever transport that entry
// declares — so the daemon/SSE transport is reachable without waiting for the
// hub's install flow.
//
// With query != "" this is a genuine non-interactive one-shot: no alt screen, the
// answer on stdout, progress on stderr, and a real exit code. That is what makes
// the transport exercisable from a script, from CI, and against a live daemon.
// timeout bounds that turn; it is ignored by the interactive path, where a person
// can see what is happening and press ctrl+c.
//
// ctrl is only honoured on the interactive path (query == ""): a one-shot has no
// session for the control API to attach to, so the caller is expected to have
// already refused that combination (see cli.agentControlOptions) rather than
// pass a non-nil ctrl through here.
// Returns the process exit code.
func RunAgent(agentID, query, model string, debug bool, timeout time.Duration, ctrl *control.Options) (int, error) {
	cat := catalog.NewCatalog()
	cat.DiscoverBinaries()

	agent := cat.Get(agentID)
	if agent == nil {
		return 1, fmt.Errorf("no agent %q in the catalog. %s", agentID, runnableIDs(cat))
	}

	// A one-shot is always bounded — that is the whole point — so an unbounded
	// or negative one is refused here rather than quietly turned into "forever".
	if query != "" && timeout <= 0 {
		return 1, fmt.Errorf(
			"--timeout must be a positive duration, got %s: a one-shot that cannot "+
				"time out is exactly the hang this bound exists to prevent. Pass a "+
				"longer bound instead, e.g. --timeout 2h", timeout)
	}

	// A one-shot is always bounded — that is the whole point — so an unbounded
	// or negative one is refused here rather than quietly turned into "forever".
	if query != "" && timeout <= 0 {
		return 1, fmt.Errorf(
			"--timeout must be a positive duration, got %s: a one-shot that cannot "+
				"time out is exactly the hang this bound exists to prevent. Pass a "+
				"longer bound instead, e.g. --timeout 2h", timeout)
	}

	logf := func(string, ...any) {}
	if debug {
		logf = func(format string, args ...any) {
			fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
		}
	}

	c, err := client.ForAgent(*agent, client.ForAgentOptions{
		Debug: debug,
		Model: model,
		Logf:  logf,
		// A one-shot has nobody at the keyboard; only the interactive chat can
		// answer a question, so it must not claim it can.
		Interactive: query == "",
	})
	if err != nil {
		return 1, err
	}
	defer c.Close()

	if query != "" {
		logf("one-shot: agent=%s transport=%s model=%q timeout=%s",
			agent.ID, agent.Transport, orDefault(model, "the agent's default"), timeout)

		// A one-shot runs unattended, so an unmet precondition has to be
		// reported and refused rather than waited on. Interactive is left alone
		// on purpose: a person can read a half-answer and press ctrl+c, and the
		// launch that does have a gate is the hub's.
		if agent.Transport == catalog.TransportDaemon {
			// Only a relayed agent has the /v1/<agent>/init route the check
			// probes. For a subprocess agent the rows could only say "not
			// installed" — four wrong answers over a launch that works.
			t := preflight.NewDaemonTransport(daemon.New(daemon.Options{Logf: logf}))
			rep := ReportReadiness(context.Background(), t,
				preflight.ConfigFor(agent.ID, agent.Name), os.Stderr)
			// Every row, not just the blocker: a turn that answers nothing is
			// usually explained by a row that passed for the wrong reason.
			for _, row := range rep.Rows {
				logf("readiness %s: state=%v %s | remedy=%q",
					row.Key, row.State, row.Line, row.Remedy.Command)
			}
			if rep.Blocked() {
				return 1, nil
			}
		}

		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		res := RunOneShot(ctx, c, query, os.Stdout, os.Stderr, logf)
		return res.ExitCode, nil
	}

	model_ := chat.NewChatModelForCatalogAgent(c, agent.ID, agent.Name, debug)
	if err := run(model_, debug, ctrl); err != nil {
		return 1, err
	}
	return 0, nil
}

// runnableIDs names what would actually start, and keeps the rest separate.
// Listing every catalog id as "known ids" read as a menu, and most of it could
// not run.
func runnableIDs(cat *catalog.Catalog) string {
	var runnable, notYet []string
	for _, a := range cat.All() {
		if canStart(a) {
			runnable = append(runnable, a.ID)
			continue
		}
		notYet = append(notYet, a.ID)
	}

	if len(runnable) == 0 && len(notYet) == 0 {
		return "The catalog is empty."
	}

	var b strings.Builder
	if len(runnable) == 0 {
		b.WriteString("Nothing is installed yet — see what the Agent Hub offers with `gaia tui list`, " +
			"then install one with `gaia tui install <id>`")
	} else {
		b.WriteString("Installed and runnable: " + strings.Join(runnable, ", "))
		b.WriteString(". Install more with `gaia tui install <id>` (`gaia tui list` shows what is offered)")
	}
	if len(notYet) > 0 {
		b.WriteString(". Not runnable here: " + strings.Join(notYet, ", "))
	}
	return b.String()
}

// canStart reports whether this entry would actually start right now. `run`
// never consulted Status, so listing by Status alone called an agent unrunnable
// while `gaia tui run <id>` ran it.
func canStart(a catalog.Agent) bool {
	if a.Transport != catalog.TransportSubprocess {
		return a.Status.IsLaunchable()
	}
	if a.BinaryPath == "" {
		return false
	}
	_, err := catalog.ResolveExecutable(a.BinaryPath, a.ID)
	return err == nil
}

// orDefault names what will actually be used when a flag was left unset, so a
// debug line never reads "model=\"\"".
func orDefault(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

func agentNameFromPath(path string) string {
	name := filepath.Base(path)
	return strings.TrimSuffix(name, ".exe")
}
