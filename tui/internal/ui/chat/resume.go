// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"time"

	"github.com/amd/gaia/tui/internal/update"
)

// An update closes the TUI. The conversation on screen is written here first
// and shown again on the next launch, so the user picks up where they were.
// Only what the transcript shows is kept: the agent process restarts with a
// fresh session, and the resume divider says so.

type resumedMessage struct {
	Role    MessageRole `json:"role"`
	Content string      `json:"content"`
}

type resumeFile struct {
	FromVersion string           `json:"from_version"`
	ToTag       string           `json:"to_tag"`
	Messages    []resumedMessage `json:"messages"`
}

// resumeDir holds one file per TUI process, so two windows never overwrite
// each other's saved conversation. The next launch shows the newest one.
func resumeDir() (string, error) {
	cfg, err := update.ConfigPath()
	if err != nil {
		return "", err
	}
	return filepath.Join(filepath.Dir(cfg), "tui-resume"), nil
}

// saveForResume records the conversation to bring back after the update.
func (m ChatModel) saveForResume(toTag string) error {
	dir, err := resumeDir()
	if err != nil {
		return err
	}
	rf := resumeFile{FromVersion: update.Current, ToTag: toTag}
	for _, msg := range m.messages {
		switch msg.Role {
		case RoleUser, RoleAssistant, RoleStatus, RoleError:
			rf.Messages = append(rf.Messages, resumedMessage{Role: msg.Role, Content: msg.Content})
		}
	}
	data, err := json.Marshal(rf)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("cannot create %s: %w", dir, err)
	}
	path := filepath.Join(dir, fmt.Sprintf("%d.json", os.Getpid()))
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return fmt.Errorf("cannot write %s: %w", path, err)
	}
	return nil
}

// KeepConversationForUpdate runs once the TUI has stopped, however it
// stopped: with an install queued, the conversation is saved to reopen after
// it. If that fails the install is called off — offered again next launch —
// and the returned error says so.
func (m ChatModel) KeepConversationForUpdate() error {
	p, ok := update.Pending()
	if !ok || !m.hasUserMessage() {
		return nil
	}
	if err := m.saveForResume(p.Tag); err != nil {
		update.Cancel()
		return fmt.Errorf("GAIA %s was not installed: this conversation could not be saved first (%v). "+
			"It is offered again next launch", p.Tag, err)
	}
	return nil
}

// TakeResumedConversation returns the most recent conversation an update
// saved, with a divider saying what happened, and removes it so it is shown
// once. Nothing saved is (nil, nil).
func TakeResumedConversation() ([]Message, error) {
	dir, err := resumeDir()
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("cannot read %s: %w", dir, err)
	}
	var path string
	var newest time.Time
	for _, e := range entries {
		info, err := e.Info()
		if err != nil || e.IsDir() || filepath.Ext(e.Name()) != ".json" {
			continue
		}
		if path == "" || info.ModTime().After(newest) {
			path, newest = filepath.Join(dir, e.Name()), info.ModTime()
		}
	}
	if path == "" {
		return nil, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read the conversation saved before the update (%s): %w", path, err)
	}
	if err := os.Remove(path); err != nil {
		return nil, fmt.Errorf("cannot clear %s, so the restored conversation would show again next launch — delete it by hand: %w", path, err)
	}
	var rf resumeFile
	if err := json.Unmarshal(data, &rf); err != nil {
		return nil, fmt.Errorf("the conversation saved before the update (%s) cannot be read: %w", path, err)
	}
	out := make([]Message, 0, len(rf.Messages)+1)
	for _, rm := range rf.Messages {
		out = append(out, Message{Role: rm.Role, Content: rm.Content})
	}
	what := fmt.Sprintf("Reopened after updating to GAIA %s (was %s).", update.Current, rf.FromVersion)
	if update.Current == rf.FromVersion {
		what = fmt.Sprintf("Reopened: the update to %s did not finish, so this is still GAIA %s.", rf.ToTag, update.Current)
	}
	out = append(out, Message{Role: RoleStatus, Content: "— " + what + " Your conversation is shown above; " +
		"the agent started a new session, so mention anything from it you still need. —"})
	return out, nil
}
