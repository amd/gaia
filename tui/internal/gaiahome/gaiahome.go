// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package gaiahome expands GAIA_HOME and config paths using Python's
// os.path.expanduser + os.path.expandvars rules so the TUI and Python
// always resolve the same directory.
package gaiahome

import (
	"os"
	"runtime"
	"strings"
)

// ExpandPath expands leading ~ and environment variables in raw using the
// current process environment and OS rules, mirroring Python's
// os.path.expandvars(os.path.expanduser(raw)).
func ExpandPath(raw string) string {
	return ExpandPathForOS(raw, os.LookupEnv, os.UserHomeDir, runtime.GOOS == "windows")
}

// ExpandPathWith is ExpandPath with injectable environment lookup and home
// directory resolution for tests.
func ExpandPathWith(
	raw string,
	lookup func(string) (string, bool),
	homeDir func() (string, error),
) string {
	return ExpandPathForOS(raw, lookup, homeDir, runtime.GOOS == "windows")
}

// ExpandPathForOS applies ExpandUserForOS first and ExpandVarsForOS second,
// matching Python's os.path.expandvars(os.path.expanduser(raw)).
func ExpandPathForOS(
	raw string,
	lookup func(string) (string, bool),
	homeDir func() (string, error),
	isWindows bool,
) string {
	return ExpandVarsForOS(ExpandUserForOS(raw, homeDir, isWindows), lookup, isWindows)
}

// ExpandUser replaces a leading ~ using the current OS separator rules.
func ExpandUser(path string, homeDir func() (string, error)) string {
	return ExpandUserForOS(path, homeDir, runtime.GOOS == "windows")
}

// ExpandUserForOS replaces a leading ~ with homeDir(). On POSIX only ~ and
// ~/... expand; on Windows ~\... also expands, matching Python's expanduser.
func ExpandUserForOS(path string, homeDir func() (string, error), isWindows bool) string {
	if !HasTildePrefixForOS(path, isWindows) {
		return path
	}
	if homeDir == nil {
		return path
	}
	home, err := homeDir()
	if err != nil || home == "" {
		return path
	}
	if isWindows {
		return home + path[1:]
	}
	trimmed := strings.TrimRight(home, "/")
	out := trimmed + path[1:]
	if out == "" {
		return "/"
	}
	return out
}

// HasTildePrefix reports whether path begins with ~ or a home separator.
func HasTildePrefix(path string) bool {
	return HasTildePrefixForOS(path, runtime.GOOS == "windows")
}

// HasTildePrefixForOS reports whether path begins with ~ or a separator for isWindows.
func HasTildePrefixForOS(path string, isWindows bool) bool {
	if path == "~" || strings.HasPrefix(path, "~/") {
		return true
	}
	return isWindows && strings.HasPrefix(path, `~\`)
}

// LookupFromGetenv adapts a single-return getenv test double (where "" is
// treated as unset because Go's func(string) string cannot distinguish empty
// from absent) to a presence-aware lookup. Production callers must pass
// os.LookupEnv directly via ExpandPath or *FromLookup.
func LookupFromGetenv(getenv func(string) string) func(string) (string, bool) {
	return func(name string) (string, bool) {
		if getenv == nil {
			return "", false
		}
		if val := getenv(name); val != "" {
			return val, true
		}
		return "", false
	}
}

func isPOSIXVarByte(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_'
}

func isWinVarByte(c byte) bool {
	return isPOSIXVarByte(c) || c == '-'
}

// ExpandVarsForOS expands environment variables using Python's
// posixpath.expandvars (when !isWindows) or ntpath.expandvars (when isWindows).
// Undefined variables are preserved verbatim as written.
func ExpandVarsForOS(s string, lookup func(string) (string, bool), isWindows bool) string {
	if lookup == nil || s == "" {
		return s
	}
	if !strings.ContainsRune(s, '$') && (!isWindows || !strings.ContainsRune(s, '%')) {
		return s
	}

	var b strings.Builder
	b.Grow(len(s))

	for i := 0; i < len(s); {
		c := s[i]
		if isWindows && c == '\'' {
			end := strings.IndexByte(s[i+1:], '\'')
			if end < 0 {
				b.WriteString(s[i:])
				break
			}
			b.WriteString(s[i : i+1+end+1])
			i += 1 + end + 1
			continue
		}
		if isWindows && c == '%' {
			if i+1 < len(s) && s[i+1] == '%' {
				b.WriteByte('%')
				i += 2
				continue
			}
			end := strings.IndexByte(s[i+1:], '%')
			if end < 0 {
				b.WriteString(s[i:])
				break
			}
			name := s[i+1 : i+1+end]
			if val, ok := lookup(name); ok {
				b.WriteString(val)
			} else {
				b.WriteString(s[i : i+1+end+1])
			}
			i += 1 + end + 1
			continue
		}
		if c == '$' {
			if isWindows && i+1 < len(s) && s[i+1] == '$' {
				b.WriteByte('$')
				i += 2
				continue
			}
			if i+1 < len(s) && s[i+1] == '{' {
				end := strings.IndexByte(s[i+2:], '}')
				if end < 0 {
					b.WriteString(s[i:])
					break
				}
				name := s[i+2 : i+2+end]
				if val, ok := lookup(name); ok {
					b.WriteString(val)
				} else {
					b.WriteString(s[i : i+2+end+1])
				}
				i += 2 + end + 1
				continue
			}
			j := i + 1
			for j < len(s) {
				if isWindows {
					if !isWinVarByte(s[j]) {
						break
					}
				} else if !isPOSIXVarByte(s[j]) {
					break
				}
				j++
			}
			if j > i+1 {
				name := s[i+1 : j]
				if val, ok := lookup(name); ok {
					b.WriteString(val)
				} else {
					b.WriteString(s[i:j])
				}
				i = j
				continue
			}
		}
		b.WriteByte(c)
		i++
	}
	return b.String()
}
