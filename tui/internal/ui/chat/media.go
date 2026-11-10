// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

type mediaCard struct {
	Kind      string `json:"kind"`
	Path      string `json:"path"`
	MediaType string `json:"media_type"`
}

type mediaActionResultMsg struct {
	path   string
	action string
	err    error
}

var allowedMediaExtensions = map[string]struct{}{
	".png": {}, ".jpg": {}, ".jpeg": {}, ".webp": {},
	".wav": {}, ".mp3": {}, ".ogg": {}, ".flac": {},
}

func isAllowedMediaPath(path string) bool {
	_, ok := allowedMediaExtensions[strings.ToLower(filepath.Ext(path))]
	return ok
}

// isRemoteOrDevicePath is judged on the string alone: stat-ing or opening a
// UNC path reaches out to the share.
func isRemoteOrDevicePath(path string) bool {
	if strings.ContainsRune(path, 0) {
		return true
	}
	p := strings.ReplaceAll(path, "/", `\`)
	return strings.HasPrefix(p, `\\`) || strings.HasPrefix(p, `\??\`)
}

func (m ChatModel) latestMediaCard() (mediaCard, bool) {
	for i := len(m.messages) - 1; i >= 0; i-- {
		msg := m.messages[i]
		if msg.Role != RoleCard || msg.Render != "media" {
			continue
		}
		var card mediaCard
		if err := json.Unmarshal(msg.Data, &card); err != nil {
			continue
		}
		raw := strings.TrimSpace(card.Path)
		if isRemoteOrDevicePath(raw) {
			continue
		}
		path := filepath.Clean(raw)
		if path == "." || !filepath.IsAbs(path) || !isAllowedMediaPath(path) {
			continue
		}
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		card.Path = path
		return card, true
	}
	return mediaCard{}, false
}

func mediaStatus(path, action string, err error) string {
	if err != nil {
		if path == "" {
			return fmt.Sprintf("could not %s: %v", action, err)
		}
		return fmt.Sprintf("could not %s %s: %v", action, path, err)
	}
	label := "Opened"
	if action == "reveal" {
		label = "Revealed"
	}
	return fmt.Sprintf("%s %s", label, path)
}

func remoteSession() bool {
	return strings.TrimSpace(os.Getenv("SSH_CONNECTION")) != "" ||
		strings.TrimSpace(os.Getenv("SSH_TTY")) != ""
}

// mediaArgv hands the file to the OS viewer as one argv element, never through
// a shell, so a path cannot become a command.
func mediaArgv(goos, action, path string) []string {
	switch {
	case action == "reveal" && goos == "darwin":
		return []string{"open", "-R", path}
	case action == "reveal" && goos == "windows":
		return []string{"explorer", "/select," + path}
	case action == "reveal":
		return []string{"xdg-open", filepath.Dir(path)}
	case goos == "darwin":
		return []string{"open", path}
	case goos == "windows":
		return []string{"rundll32", "url.dll,FileProtocolHandler", path}
	default:
		return []string{"xdg-open", path}
	}
}

func mediaAction(path, action string) tea.Cmd {
	return func() tea.Msg {
		argv := mediaArgv(runtime.GOOS, action, path)
		cmd := exec.Command(argv[0], argv[1:]...)
		if err := cmd.Start(); err != nil {
			return mediaActionResultMsg{path: path, action: action, err: err}
		}
		go func() { _ = cmd.Wait() }()
		return mediaActionResultMsg{path: path, action: action}
	}
}

func (m ChatModel) mediaActionCmd(action string) tea.Cmd {
	card, ok := m.latestMediaCard()
	if !ok {
		return func() tea.Msg {
			return mediaActionResultMsg{
				action: action,
				err:    errors.New("no generated media card is available"),
			}
		}
	}
	if remoteSession() {
		return func() tea.Msg {
			return mediaActionResultMsg{
				path:   card.Path,
				action: action,
				err:    errors.New("opening local files is unavailable over SSH; use the displayed path"),
			}
		}
	}
	return mediaAction(card.Path, action)
}
