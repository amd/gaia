// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"regexp"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
)

// A line is a command when it starts with "/" and then nothing or one word of
// letters, digits, "-" and "_", ending at a space or the end: "/usr/bin/x" and "//" are text.
var commandShapeRE = regexp.MustCompile(`^/([A-Za-z0-9][A-Za-z0-9_-]*)?(?:\s|$)`)

// commandWord is the lower-cased command a line starts with, e.g. "/help".
func commandWord(query string) (string, bool) {
	match := commandShapeRE.FindStringSubmatch(strings.TrimLeftFunc(query, unicode.IsSpace))
	if match == nil {
		return "", false
	}
	return "/" + strings.ToLower(match[1]), true
}

// normalizeCommand trims a command line and lower-cases its command word, so
// "/Help" runs /help. Text that is not a command comes back untouched.
func normalizeCommand(query string) string {
	word, ok := commandWord(query)
	if !ok {
		return query
	}
	trimmed := strings.TrimSpace(query)
	return word + trimmed[len(word):]
}

const (
	quitCommand = "/quit"
	exitCommand = "/exit"
)

func isQuitCommand(query string) bool {
	return query == quitCommand || query == exitCommand
}

// nativeLacks is what gaiacpp does not do yet, by the command that needs it.
// Each entry is checked against cpp/agents/gaiacpp/stdio_server.cpp.
var nativeLacks = map[string]string{
	"/sessions":         "keep sessions",
	"/context":          "report what fills the model's context",
	skillsCommandPrefix: "list or add skills on command",
	researchCommand:     "write research reports",
	"/research-report":  "write research reports",
	imageCommandPrefix:  "use GAIA's image settings",
}

// nativeUnavailable is the refusal for a command gaiacpp cannot serve, or "".
func (m ChatModel) nativeUnavailable(query string) string {
	if !m.nativeAgent() {
		return ""
	}
	word, ok := commandWord(query)
	if !ok {
		return ""
	}
	lack, ok := nativeLacks[word]
	if !ok {
		return ""
	}
	how := "Start gaia-cli with --agent-program gaiapy to use it."
	if m.agentProgramHost {
		how = "Type /agent gaiapy to switch."
	}
	return fmt.Sprintf("%s needs %s (the Python agent): gaiacpp, the %s agent this session runs, doesn't %s yet. %s",
		word, catalog.HarnessLabel(catalog.PythonAgentChoice), catalog.HarnessLabel(catalog.NativeAgentBinary), lack, how)
}

// builtinCommandWord reports whether word is one of the TUI's own commands.
func builtinCommandWord(word string) bool {
	if word == "/bypass" || word == exitCommand || word == "/research-report" {
		return true
	}
	for _, c := range paletteCommands {
		if first, _, _ := strings.Cut(c.Name, " "); first == word {
			return true
		}
	}
	return false
}

// unrunnableCommand is why a command-shaped line that no command took must not
// be sent to the model, or "" for text and for an installed skill's /<name>.
func (m ChatModel) unrunnableCommand(query string) string {
	word, ok := commandWord(query)
	if !ok {
		return ""
	}
	if builtinCommandWord(word) {
		rest := strings.TrimSpace(strings.TrimSpace(query)[len(word):])
		return fmt.Sprintf("%s doesn't take %q; /help lists commands.", word, rest)
	}
	// Another agent may read its own slash commands.
	if m.agentID != setupAgentID {
		return ""
	}
	if m.skillCommands == nil {
		return fmt.Sprintf("The agent hasn't listed its skills yet, so %s can't be run. "+
			"Try again in a moment; /help lists commands.", word)
	}
	for _, s := range m.skillCommands {
		if "/"+s.Name == word {
			return ""
		}
	}
	return fmt.Sprintf("Unknown command %s; /help lists commands. "+
		"To send this as a message, start it with something other than \"/\".", word)
}

func (m ChatModel) refuseCommand(why string) (tea.Model, tea.Cmd) {
	m.messages = append(m.messages, Message{Role: RoleError, Content: why})
	m.updateViewport()
	return m, nil
}
