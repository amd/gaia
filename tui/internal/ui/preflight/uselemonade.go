// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

// Using a Lemonade the user already runs, instead of GAIA's own.
//
// Two servers on one machine hold two copies of every model and run it out of
// memory. So on a machine where nobody has chosen yet, GAIA's own server is not
// installed, and a new-enough Lemonade already answers on its standard port,
// the Lemonade row offers to use that one. Once the user picks it
// (lemonade_mode=external), GAIA starts nothing: a server that is down, too
// old, keyed, or not Lemonade is reported with the next step, never replaced.

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// detectProbeTimeout bounds one port's /health answer during detection.
const detectProbeTimeout = 3 * time.Second

// lemonadeOnServerNote says what adopting the user's server does to it.
const lemonadeOnServerNote = "GAIA registers its embedding model there and loads its models there; " +
	"it does not change your server's settings."

// detectRunningLemonade finds a Lemonade the user runs on this machine's
// standard ports. A var so tests never reach a real port.
var detectRunningLemonade = func(ctx context.Context) (lemonade.ProbeResult, bool) {
	var bases []string
	own := 0
	if state := lemonade.ReadEmbedded(); state != nil {
		own = state.Port
	}
	for _, port := range lemonadePorts {
		if fmt.Sprint(own) == port {
			continue
		}
		bases = append(bases, "http://localhost:"+port+"/api/v1")
	}
	return detectLemonadeAt(ctx, bases, func() bool { return resolveLemonade().Found })
}

// detectLemonadeAt returns the first usable or key-protected Lemonade among
// bases. A 401 counts only where a Lemonade is installed, since any server can
// answer 401. HTML, other JSON and too-old servers are never offered.
func detectLemonadeAt(ctx context.Context, bases []string, installed func() bool) (lemonade.ProbeResult, bool) {
	for _, base := range bases {
		pctx, cancel := context.WithTimeout(ctx, detectProbeTimeout)
		r := lemonade.DetectProbe(pctx, base)
		cancel()
		switch {
		case r.Status == lemonade.ProbeOK:
			return r, true
		case r.Status == lemonade.ProbeNeedsKey && installed():
			return r, true
		}
	}
	return lemonade.ProbeResult{}, false
}

// offersRunningLemonade reports whether this launch may offer the user's own
// server: nobody chose, no LEMONADE_BASE_URL, and GAIA's own is not installed.
func offersRunningLemonade(choice lemonade.Choice) bool {
	return choice.Mode == "" &&
		strings.TrimSpace(os.Getenv(lemonadeBaseURLEnv)) == "" &&
		!lemonade.EmbeddedInstalled()
}

// offerRow is the Lemonade row asking which server to use. Unknown + Halt:
// it holds the screen for a choice; enter continues without one.
func offerRow(r lemonade.ProbeResult) Row {
	row := Row{
		Key:         KeyLemonade,
		State:       StateUnknown,
		Disposition: status.DispositionHalt,
		DeclineFix:  FixUseGAIALemonade,
	}
	if r.Status == lemonade.ProbeNeedsKey {
		row.Line = "found one at " + r.URL + " that needs an API key"
		row.Detail = "A Lemonade Server is already running at " + r.URL + ", and it needs an API key. " +
			lemonadeOnServerNote
		row.Remedy = Remedy{
			Action: "To use it, save its key with the command below, then press r. " +
				"Or press g to install GAIA's own instead.",
			Command: "gaia lemonade connect " + r.URL,
			Where:   lemonadeDocs,
		}
		row.Raw = "GET " + r.URL + "/health -> 401"
		return row
	}
	row.Line = fmt.Sprintf("Lemonade v%s already running at %s", r.Version, r.URL)
	row.Detail = "Use the Lemonade already running at " + r.URL + ", or install GAIA's own? " +
		"One server keeps one copy of each model in memory. " + lemonadeOnServerNote
	row.Remedy = Remedy{
		Action: "Press f to use it, or g to install GAIA's own. To switch back later, run " +
			"`gaia config set lemonade_mode gaia`.",
		Where: lemonadeDocs,
	}
	row.Fix = FixUseRunningLemonade
	row.Raw = fmt.Sprintf("GET %s/health -> ok, version %s", r.URL, r.Version)
	return row
}

// checkExternalLemonade is the Lemonade row in external mode. It starts
// nothing, whatever it finds.
func (l localRunner) checkExternalLemonade(ctx context.Context, row Row, base string) Row {
	key, err := lemonade.APIKeyFor(ctx, base)
	if err != nil {
		row.State = StateFailed
		row.Disposition = status.DispositionHalt
		row.Line = "API key unavailable for " + base
		row.Detail = err.Error() + "."
		row.Raw = err.Error()
		return row
	}
	pctx, cancel := context.WithTimeout(ctx, lemonadeProbeTimeout)
	r := lemonade.Probe(pctx, base, key)
	cancel()
	row.Raw = fmt.Sprintf("GET %s/health -> %s %s", r.URL, r.Status, r.Detail)
	if r.Status == lemonade.ProbeOK {
		row.State = StateOK
		row.Line = fmt.Sprintf("your Lemonade v%s at %s", r.Version, r.URL)
		return row
	}
	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.Detail = r.Message()
	row.Remedy = Remedy{Action: "Then press r to re-check.", Where: lemonadeDocs}
	switch r.Status {
	case lemonade.ProbeUnreachable:
		row.Line = "your server at " + r.URL + " is not answering"
		if l.opts.ClaudeMode {
			row.State = StateUnknown
			row.Disposition = status.DispositionNotify
			row.Line = "not answering — this session runs on Claude"
		}
	case lemonade.ProbeTooOld:
		row.Line = fmt.Sprintf("v%s is older than %s", r.Version, r.Minimum)
		row.Remedy.Where = "https://lemonade-server.ai"
	case lemonade.ProbeNeedsKey, lemonade.ProbeBadKey:
		row.Line = "needs an API key"
		if r.Status == lemonade.ProbeBadKey {
			row.Line = "rejected the saved API key"
		}
		row.Remedy.Command = "gaia lemonade connect " + r.URL
	default:
		row.Line = "not a Lemonade Server at " + r.URL
	}
	return row
}

// useRunningLemonade saves the detected server as GAIA's Lemonade, after
// checking it still is one.
func (l localRunner) useRunningLemonade(ctx context.Context) FixResult {
	r, ok := detectRunningLemonade(ctx)
	if !ok || r.Status != lemonade.ProbeOK {
		return FixResult{Err: errFixFailed, Diagnosis: Diagnosis{
			Cause:  "The Lemonade Server found earlier is no longer answering as one GAIA can use.",
			Remedy: "Press r to re-check.",
			Where:  lemonadeDocs,
		}}
	}
	path, err := writeConfigKeys(map[string]any{
		"lemonade_mode":           lemonade.ModeExternal,
		"lemonade_base_url":       r.URL,
		"lemonade_api_key_stored": false,
	})
	if err != nil {
		return FixResult{Err: err, Diagnosis: Diagnosis{
			Cause:  "GAIA could not save the choice: " + err.Error() + ".",
			Remedy: "Fix the file, then press r.",
			Where:  path,
		}}
	}
	if err := lemonade.ApplyChoiceToDaemon(ctx, lemonade.ModeExternal, r.URL); err != nil {
		return FixResult{Err: err, Diagnosis: Diagnosis{
			Cause:   "Saved: GAIA now uses your Lemonade at " + r.URL + ". But " + err.Error() + ".",
			Command: "gaia daemon restart",
			Where:   daemonLog(),
		}}
	}
	return FixResult{Note: "GAIA now uses your Lemonade at " + r.URL + "."}
}

// declinedRunningLemonade is set when the user answered the offer with `g`.
// The answer is held here, not saved: until GAIA's own server is installed,
// a saved "gaia" would name a server that does not exist, and the default
// port would answer with the user's server instead.
var declinedRunningLemonade atomic.Bool

// useGAIALemonade answers the offer with GAIA's own server. Nothing is saved:
// setup saves the choice once GAIA's server is installed and running.
func (l localRunner) useGAIALemonade() FixResult {
	declinedRunningLemonade.Store(true)
	return FixResult{Note: "GAIA will install its own Lemonade. Press f to set it up."}
}

// awaitsGAIALemonade reports whether GAIA's own server is wanted but not yet
// installed: chosen with `g` this session, or saved as "gaia" earlier. Then
// a Lemonade answering on a standard port is someone else's, never GAIA's.
func awaitsGAIALemonade(choice lemonade.Choice) bool {
	wanted := declinedRunningLemonade.Load() || choice.Mode == lemonade.ModeGAIA
	return wanted &&
		strings.TrimSpace(os.Getenv(lemonadeBaseURLEnv)) == "" &&
		!lemonade.EmbeddedInstalled() &&
		readEmbeddedLemonade() == nil
}

// gaiaLemonadeMissingRow is the Lemonade row while GAIA's own server is wanted
// but not installed. It probes no port, so another server can't pass for it.
func gaiaLemonadeMissingRow(row Row) Row {
	row.State = StateFailed
	row.Disposition = status.DispositionHalt
	row.FirstRun = true
	row.Line = "GAIA's own not installed yet"
	row.Step = installServerStep
	row.Detail = "You chose GAIA's own Lemonade Server. Setup installs it privately for GAIA; " +
		"another Lemonade running on this PC is not used."
	row.Fix = FixInstallServer
	row.Remedy = Remedy{
		Action:  "Press f to install it, or run this in a terminal, then press r.",
		Command: gaiainit.GAIAServerOnlyCommand(),
		Where:   lemonadeDocs,
	}
	return row
}
