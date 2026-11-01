// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"os"
	"strings"
	"time"

	"golang.org/x/term"
)

// Motion is how much the screens animate, decided once from the environment.
//
// Motion explains a change of state; nothing loops for decoration. Reduced
// motion keeps every state and every word, and drops only the frames: a static
// "[..]" instead of a spinner, bars that step once a second, no flash.
type Motion struct {
	// Reduced is NO_MOTION / GAIA_REDUCED_MOTION, a dumb terminal, or output
	// that is not a terminal at all.
	Reduced bool
	// ASCII draws bars with # and - for a terminal whose locale is not UTF-8.
	ASCII bool
}

// Tick is how often the screens redraw while something moves: 10 Hz, or once
// a second under reduced motion.
func (m Motion) Tick() time.Duration {
	if m.Reduced {
		return time.Second
	}
	return 100 * time.Millisecond
}

// DetectMotion reads the environment. getenv and isTTY are parameters so the
// rules are testable without a terminal.
func DetectMotion(getenv func(string) string, isTTY bool) Motion {
	on := func(key string) bool {
		v := strings.TrimSpace(strings.ToLower(getenv(key)))
		return v != "" && v != "0" && v != "false" && v != "no"
	}
	termName := strings.ToLower(strings.TrimSpace(getenv("TERM")))
	m := Motion{
		Reduced: on("NO_MOTION") || on("GAIA_REDUCED_MOTION") || termName == "dumb" || !isTTY,
	}
	// The first locale variable that is set decides, as in C's setlocale.
	for _, key := range []string{"LC_ALL", "LC_CTYPE", "LANG"} {
		v := strings.ToLower(strings.TrimSpace(getenv(key)))
		if v == "" {
			continue
		}
		m.ASCII = !strings.Contains(v, "utf-8") && !strings.Contains(v, "utf8")
		break
	}
	if termName == "dumb" {
		m.ASCII = true
	}
	return m
}

// EnvMotion is DetectMotion for this process.
func EnvMotion() Motion {
	return DetectMotion(os.Getenv, term.IsTerminal(int(os.Stdout.Fd())))
}
