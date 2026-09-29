// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"os"
	"regexp"
	"runtime"
	"strings"
)

// A work-log row has one line to say what a step touched, and an absolute path
// spends it on the part the reader already knows — the drive, the home folder,
// the project they are sitting in. The decision-critical part is the tail.
//
// So every absolute path in agent-supplied work-log text is shown relative to
// the directory GAIA was started in, or under ~ when it is elsewhere in the
// home folder, and a path still longer than pathMax keeps its tail. Only the
// work log is rewritten: the confirmation modal shows exactly what will run,
// and the answer is the model's own words.

// pathMax is the widest a shortened path may stay before its head is elided.
const pathMax = 48

var (
	// Drive-letter paths, either slash. Stops at whitespace, quotes and the
	// punctuation that ends a path in prose; a second ':' is a line number.
	winPathRe = regexp.MustCompile(`(?i)\b[a-z]:[\\/][^\s"'<>|*?,;:()\[\]{}]*`)
	// A rooted POSIX path with at least two segments — "/model" is a slash
	// command, not a path. The leading class keeps a URL's "//host" out.
	posixPathRe = regexp.MustCompile(`(^|[\s"'=(\[])(/[^\s"'<>|,;:()\[\]{}/]+/[^\s"'<>|,;:()\[\]{}]*)`)
)

// pathBase is the directory paths are shown relative to, and homeDir the one
// shown as ~. Package state rather than a lookup per call: shortening runs on
// every tool event, and a test swaps these in rather than chdir-ing.
var pathBase, homeDir = startDirs()

func startDirs() (string, string) {
	wd, _ := os.Getwd()
	home, _ := os.UserHomeDir()
	return wd, home
}

// shortenPaths rewrites every absolute path in s for the work log.
func shortenPaths(s string) string {
	if !strings.ContainsAny(s, `/\`) {
		return s
	}
	s = winPathRe.ReplaceAllStringFunc(s, shortenPath)
	return posixPathRe.ReplaceAllStringFunc(s, func(m string) string {
		sub := posixPathRe.FindStringSubmatch(m)
		return sub[1] + shortenPath(sub[2])
	})
}

// shortenPath shortens one absolute path: relative to pathBase, else ~-based,
// then tail-kept if it is still too wide.
func shortenPath(p string) string {
	// A trailing sentence stop belongs to the prose, not the path.
	trail := ""
	for strings.HasSuffix(p, ".") {
		p, trail = p[:len(p)-1], trail+"."
	}
	short := p
	if rel, ok := under(p, pathBase); ok {
		short = rel
		if short == "" {
			short = "."
		}
	} else if rel, ok := under(p, homeDir); ok {
		short = "~" + sepOf(p) + rel
	}
	return keepTail(short, pathMax) + trail
}

// under reports p relative to base when p is base or inside it. Windows paths
// compare case-insensitively and either slash matches: tool errors routinely
// echo a path lower-cased, and that is still the same folder.
func under(p, base string) (string, bool) {
	if base == "" {
		return "", false
	}
	np, nb := normPath(p), strings.TrimRight(normPath(base), "/")
	if nb == "" {
		return "", false
	}
	if np == nb {
		return "", true
	}
	if !strings.HasPrefix(np, nb+"/") {
		return "", false
	}
	// Sliced from the ORIGINAL, so the remainder keeps its own casing — unless
	// lower-casing changed the byte length, where only the normalized copy lines up.
	if len(np) != len(p) {
		return np[len(nb)+1:], true
	}
	return p[len(nb)+1:], true
}

func normPath(p string) string {
	if isWindowsPath(p) {
		return strings.ToLower(strings.ReplaceAll(p, `\`, "/"))
	}
	return p
}

func isWindowsPath(p string) bool {
	return runtime.GOOS == "windows" || (len(p) > 2 && p[1] == ':')
}

func sepOf(p string) string {
	if strings.Contains(p, `\`) {
		return `\`
	}
	return "/"
}

// keepTail elides a path's head until it fits in limit columns, keeping whole
// segments and always the last one: "…\proj\toybox\dates.py".
func keepTail(p string, limit int) string {
	if displayWidth(p) <= limit {
		return p
	}
	sep := sepOf(p)
	parts := strings.Split(p, sep)
	tail := parts[len(parts)-1]
	for i := len(parts) - 2; i > 0; i-- {
		next := parts[i] + sep + tail
		if displayWidth("…"+sep+next) > limit {
			break
		}
		tail = next
	}
	return "…" + sep + tail
}
