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
		path := filepath.Clean(strings.TrimSpace(card.Path))
		if path == "." || !filepath.IsAbs(path) {
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

func mediaAction(path, action string) tea.Cmd {
	return func() tea.Msg {
		var cmd *exec.Cmd
		switch {
		case action == "reveal" && runtime.GOOS == "darwin":
			cmd = exec.Command("open", "-R", path)
		case action == "reveal" && runtime.GOOS == "windows":
			cmd = exec.Command("explorer", "/select,"+path)
		case action == "reveal":
			cmd = exec.Command("xdg-open", filepath.Dir(path))
		case runtime.GOOS == "darwin":
			cmd = exec.Command("open", path)
		case runtime.GOOS == "windows":
			cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", path)
		default:
			cmd = exec.Command("xdg-open", path)
		}
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
