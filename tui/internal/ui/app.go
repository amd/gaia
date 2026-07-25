package ui

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
)

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

	c := client.NewSubprocessClient(argv[0], argv[1:], debug)
	defer c.Close()

	return run(chat.NewChatModel(c, agentNameFromPath(argv[0]), query, debug), debug, ctrl)
}

// run boots the Bubble Tea program, optionally wrapping it with the control
// recorder so the live session can be driven over HTTP.
func run(model tea.Model, debug bool, ctrl *control.Options) error {
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
// Returns the process exit code.
func RunAgent(agentID, query, model string, debug bool, timeout time.Duration) (int, error) {
	cat := catalog.NewCatalog()
	cat.DiscoverBinaries()

	agent := cat.Get(agentID)
	if agent == nil {
		return 1, fmt.Errorf("no agent %q in the catalog — known ids: %s",
			agentID, strings.Join(agentIDs(cat), ", "))
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
			if rep.Blocked() {
				return 1, nil
			}
		}

		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		res := RunOneShot(ctx, c, query, os.Stdout, os.Stderr)
		return res.ExitCode, nil
	}

	model_ := chat.NewChatModel(c, agent.Name, "", debug)
	p := tea.NewProgram(model_, tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		return 1, fmt.Errorf("TUI error: %w", err)
	}
	return 0, nil
}

func agentIDs(cat *catalog.Catalog) []string {
	all := cat.All()
	ids := make([]string, 0, len(all))
	for _, a := range all {
		ids = append(ids, a.ID)
	}
	return ids
}

func agentNameFromPath(path string) string {
	name := filepath.Base(path)
	return strings.TrimSuffix(name, ".exe")
}
