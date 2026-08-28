package ui

import (
	"context"
	"fmt"
	"io"
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

// RunFlagship launches the TUI: splash, readiness gate, then chat with the
// flagship agent. GAIA ships one agent, so this is the whole product — there is
// nothing to browse and nothing to pick.
//
// If mockAgent is non-empty, agent binary paths are overridden with it for
// testing. A non-nil ctrl starts the loopback control API against this very
// program. bypassPermissions starts the agent with confirmation prompts off.
// useClaude/claudeModel run it against Anthropic's Claude API instead of the
// local Lemonade backend.
func RunFlagship(dev bool, mockAgent string, ctrl *control.Options, bypassPermissions bool, useClaude bool, claudeModel string) error {
	cat := catalog.NewCatalog()
	if mockAgent != "" {
		cat.SetMockBinary(mockAgent)
	} else {
		cat.DiscoverBinaries()
	}
	agent := cat.Get(catalog.FlagshipID)
	if agent == nil {
		return fmt.Errorf("the catalog has no %q entry, so there is nothing to launch. "+
			"Report this with GAIA diagnostics", catalog.FlagshipID)
	}
	m := root.NewFlagshipModel(*agent, dev).
		WithBypassPermissions(bypassPermissions).
		WithClaude(useClaude, claudeModel).
		WithLocalPreflight(preflight.LocalOptions{
			// The gate has to answer about the binary this launch will actually
			// spawn — with --mock that is the mock, not the name the seed carries.
			Binary: agent.BinaryPath, ClaudeMode: useClaude,
		})
	return run(m, dev, ctrl)
}

// RunChat launches the chat TUI directly with a subprocess agent (standalone mode).
//
// subprocess is a command line, so it is split with quoting honoured — a binary
// path containing a space must be quoted, not silently torn in two.
func RunChat(subprocess string, query string, dev bool, ctrl *control.Options) error {
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

	c := client.NewSubprocessClient(bin, argv[1:], dev)
	defer c.Close()

	return run(chat.NewChatModel(c, agentNameFromPath(argv[0]), query, dev), dev, ctrl)
}

// teaOptions are the terminal capabilities every GAIA TUI program asks for.
//
// The mouse is left to the TERMINAL by default, so drag-select and the platform's
// own copy/paste work the way they do in every other program — Ctrl/Cmd+C,
// Ctrl+Shift+C, right-click, whatever that terminal uses.
//
// Capturing it (mode 1002) buys exactly one thing: the wheel scrolling the
// transcript, which an alt-screen app cannot get from the terminal's scrollback
// because it has none. That is not worth breaking selection for every user who
// never asked for it — "I still can't drag my mouse over terminal text and copy
// it" is the report this default answers. Ctrl+T turns capture on when the wheel
// is what you want; ↑/↓ and PgUp/PgDn scroll regardless.
func teaOptions() []tea.ProgramOption {
	return []tea.ProgramOption{
		tea.WithAltScreen(),
	}
}

// run boots the Bubble Tea program, optionally wrapping it with the control
// recorder so the live session can be driven over HTTP.
func run(model tea.Model, dev bool, ctrl *control.Options) error {
	prepareTerminal()

	// The agent is a child process, and on Windows nothing reaps it when this
	// one exits — so whatever the session opened is closed here, after the
	// event loop has stopped, rather than left to the OS.
	if closer, ok := model.(io.Closer); ok {
		defer func() {
			if err := closer.Close(); err != nil {
				fmt.Fprintf(os.Stderr, "%v\n", err)
			}
		}()
	}

	// Swept whether or not this run publishes one of its own: a session started
	// WITHOUT --control used to leave a dead predecessor's file in place.
	if removed, err := control.ClearStale(daemon.PIDAlive); err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
	} else if removed && dev {
		fmt.Fprintln(os.Stderr, "[DEBUG] removed a stale control discovery file")
	}

	if ctrl == nil {
		p := tea.NewProgram(model, teaOptions()...)
		if _, err := p.Run(); err != nil {
			return fmt.Errorf("TUI error: %w", err)
		}
		return nil
	}

	// One dev switch for both halves: --dev on the TUI implies control
	// logging, and the server must not end up quieter than the recorder.
	opts := *ctrl
	opts.Debug = opts.Debug || dev
	state := control.NewState(control.Debugf(opts.Debug))
	p := tea.NewProgram(control.NewRecorder(model, state), teaOptions()...)

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
//
// mockAgent stands in for the agent binary, for tests. It is honoured here and
// not only on the bare launch, because `run` launches an agent too — a flag
// that overrode the binary for one entry point and not the other let a test
// spawn the real agent while believing it had substituted a stand-in.
// Returns the process exit code.
func RunAgent(agentID, query, model string, dev bool, timeout time.Duration, ctrl *control.Options, bypassPermissions bool, useClaude bool, claudeModel, mockAgent string) (int, error) {
	cat := catalog.NewCatalog()
	if mockAgent != "" {
		cat.SetMockBinary(mockAgent)
	} else {
		cat.DiscoverBinaries()
	}

	agent := cat.Get(agentID)
	if agent == nil {
		return 1, fmt.Errorf("no agent %q in the catalog. %s", agentID, knownIDs(cat))
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
	if dev {
		logf = func(format string, args ...any) {
			fmt.Fprintf(os.Stderr, "[DEBUG] "+format+"\n", args...)
		}
	}

	if query != "" {
		logf("one-shot: agent=%s transport=%s model=%q timeout=%s",
			agent.ID, agent.Transport, orDefault(model, "the agent's default"), timeout)

		// Readiness runs BEFORE the client is built, and over EITHER transport.
		//
		// Both halves used to be wrong for a subprocess agent. It ran only for a
		// relayed agent, because every row was probed through the daemon — and
		// it ran after the client, so a machine with no gaia-agent got
		// ForAgent's bare "no runnable binary" instead of the gate's three-part
		// answer, which names where it looked and how to get the program.
		cfg := preflight.ConfigFor(agent.ID, agent.Name)
		var rep preflight.Report
		if agent.Transport == catalog.TransportDaemon {
			t := preflight.NewDaemonTransport(daemon.New(daemon.Options{Logf: logf}))
			rep = ReportReadiness(context.Background(), t, cfg, os.Stderr)
		} else {
			rep = ReportLocalReadiness(context.Background(),
				preflight.LocalOptions{Binary: agent.BinaryPath, ClaudeMode: useClaude},
				cfg, os.Stderr)
		}
		// Every row, not just the blocker: a turn that answers nothing is
		// usually explained by a row that passed for the wrong reason.
		for _, row := range rep.Rows {
			logf("readiness %s: state=%v %s | remedy=%q",
				row.Key, row.State, row.Line, row.Remedy.Command)
		}
		if rep.Blocked() {
			return 1, nil
		}

		c, err := client.ForAgent(*agent, client.ForAgentOptions{
			Dev:   dev,
			Model: model,
			Logf:  logf,
			// A one-shot has nobody at the keyboard; only the interactive chat
			// can answer a question, so it must not claim it can.
			Interactive:       false,
			BypassPermissions: bypassPermissions,
			UseClaude:         useClaude,
			ClaudeModel:       claudeModel,
		})
		if err != nil {
			return 1, err
		}
		defer c.Close()

		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		defer cancel()
		res := RunOneShot(ctx, c, query, os.Stdout, os.Stderr, dev, logf)
		return res.ExitCode, nil
	}

	// Interactive goes through the same splash -> readiness -> chat router the
	// flagship launch uses, and that router builds its own client once the gate
	// passes. That is what gains an interactive --agent launch a gate: it had
	// none, and email in particular went straight to chat and reported a
	// missing daemon as a failed first message.
	m := root.NewFlagshipModel(*agent, dev).
		WithBypassPermissions(bypassPermissions).
		WithClaude(useClaude, claudeModel)
	if err := run(m, dev, ctrl); err != nil {
		return 1, err
	}
	return 0, nil
}

// knownIDs names the agents this build can address, for a --agent that named
// none of them. Whether each one is READY is the readiness gate's answer, not
// this list's: it runs a moment later and says so per row, with a remedy.
func knownIDs(cat *catalog.Catalog) string {
	var ids []string
	for _, a := range cat.All() {
		ids = append(ids, a.ID)
	}
	if len(ids) == 0 {
		return "The catalog is empty."
	}
	return "Known ids: " + strings.Join(ids, ", ") + "."
}

// orDefault names what will actually be used when a flag was left unset, so a
// diagnostic line never reads "model=\"\"".
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
