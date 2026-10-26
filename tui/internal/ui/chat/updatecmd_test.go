// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/update"
)

// updateModel is a chat whose update settings live in a throwaway home.
func updateModel(t *testing.T) ChatModel {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("GAIA_DISABLE_UPDATE", "")
	prev := update.Current
	update.Current = "0.25.0"
	t.Cleanup(func() { update.Current = prev; update.Cancel() })
	return slackModel(t)
}

func lastError(t *testing.T, m ChatModel) string {
	t.Helper()
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleError {
			return m.messages[i].Content
		}
	}
	t.Fatal("no error message was added")
	return ""
}

func available(auto bool, pin string) update.Status {
	return update.Status{
		Current:   "0.25.0",
		Config:    update.Config{AutoUpdate: auto, PinnedVersion: pin},
		Latest:    update.Release{Tag: "v0.26.0"},
		Available: true,
	}
}

func TestUpdateIsDispatchedNotSentToTheAgent(t *testing.T) {
	m := updateModel(t)
	for _, q := range []string{"/update", "/update now", "/update v0.24.0", "/update  auto   on"} {
		next, cmd := m.submit(q)
		if next.(ChatModel).streaming {
			t.Errorf("%q started a turn", q)
		}
		if cmd == nil {
			t.Errorf("%q did nothing", q)
		}
	}
	if isUpdateCommand("/updates") {
		t.Error("/updates is not /update")
	}
}

func TestUpdateRejectsAnUnknownOption(t *testing.T) {
	m := updateModel(t)
	next, _ := m.submit("/update please")
	if got := lastError(t, next.(ChatModel)); !strings.Contains(got, "/update now") {
		t.Errorf("error = %q; want the usage", got)
	}
}

func TestUpdateAutoOnSavesTheSharedSetting(t *testing.T) {
	m := updateModel(t)
	_, cmd := m.submit("/update auto on")
	next, _ := m.Update(cmd())
	if got := lastStatus(t, next.(ChatModel)); !strings.Contains(got, "Auto-update is on") {
		t.Errorf("status = %q", got)
	}
	if cfg, err := update.ReadConfig(); err != nil || !cfg.AutoUpdate {
		t.Fatalf("config = %+v, %v", cfg, err)
	}
	_, cmd = m.submit("/update auto off")
	m.Update(cmd())
	if cfg, _ := update.ReadConfig(); cfg.AutoUpdate {
		t.Fatal("auto off did not save")
	}
}

func TestUpdateRCNeedsAutoUpdate(t *testing.T) {
	m := updateModel(t)
	_, cmd := m.submit("/update rc on")
	next, _ := m.Update(cmd())
	msgs := next.(ChatModel).messages
	if last := msgs[len(msgs)-1]; last.Role != RoleError || !strings.Contains(last.Content, "auto-update first") {
		t.Errorf("last message = %+v; want an error naming auto-update", last)
	}
	_, cmd = m.submit("/update auto on")
	m.Update(cmd())
	_, cmd = m.submit("/update rc on")
	next, _ = m.Update(cmd())
	if got := lastStatus(t, next.(ChatModel)); !strings.Contains(got, "includes release candidates") {
		t.Errorf("status = %q", got)
	}
	if cfg, _ := update.ReadConfig(); !cfg.IncludeRCs() {
		t.Fatalf("config = %+v; want RCs included", cfg)
	}
}

func TestLaunchCheckOffersTheUpdateWhenAutoUpdateIsOff(t *testing.T) {
	m := updateModel(t)
	next, cmd := m.handleUpdateChecked(updateCheckedMsg{status: available(false, ""), launch: true})
	if cmd != nil {
		t.Fatal("auto-update is off: the launch check must not start an install")
	}
	got := lastStatus(t, next.(ChatModel))
	if !strings.Contains(got, "v0.26.0 is available") || !strings.Contains(got, "/update now") {
		t.Errorf("offer = %q", got)
	}
}

func TestLaunchCheckIsQuietWhenPinnedOrCurrent(t *testing.T) {
	m := updateModel(t)
	before := len(m.messages)
	for _, st := range []update.Status{available(true, "v0.24.0"), {Current: "0.25.0"}} {
		next, cmd := m.handleUpdateChecked(updateCheckedMsg{status: st, launch: true})
		if cmd != nil || len(next.(ChatModel).messages) != before {
			t.Errorf("status %+v: the launch check spoke up or acted", st)
		}
	}
}

func TestLaunchCheckWithAutoUpdatePlansButDoesNotQuit(t *testing.T) {
	m := updateModel(t)
	_, cmd := m.handleUpdateChecked(updateCheckedMsg{status: available(true, ""), launch: true})
	if cmd == nil {
		t.Fatal("auto-update is on: the launch check must plan the install")
	}
	msg := cmd().(updatePlannedMsg)
	if !msg.scheduledOnly {
		t.Fatal("an auto-update must wait for quit, not close the TUI")
	}
	// The test binary has no installer behind it, so planning says why it
	// cannot install — and nothing is queued.
	next, _ := m.handleUpdatePlanned(msg)
	if got := lastError(t, next.(ChatModel)); !strings.Contains(got, "Cannot install that GAIA release") {
		t.Errorf("error = %q", got)
	}
	if _, ok := update.Pending(); ok {
		t.Error("a plan that failed was queued")
	}
}

func TestPlannedInstallQueuesAndQuits(t *testing.T) {
	m := updateModel(t)
	plan := update.Plan{Tag: "v0.24.0", Kind: update.KindScript, Pin: true}
	next, cmd := m.handleUpdatePlanned(updatePlannedMsg{plan: plan})
	if p, ok := update.Pending(); !ok || p.Tag != "v0.24.0" {
		t.Fatalf("pending = %+v, %v", p, ok)
	}
	if cmd == nil {
		t.Fatal("installing must close the TUI")
	}
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatal("the command was not tea.Quit")
	}
	if got := lastStatus(t, next.(ChatModel)); !strings.Contains(got, "/update unpin") {
		t.Errorf("an exact-version install must say it is pinned: %q", got)
	}
}

func TestPlannedInstallWaitsForARunningAnswer(t *testing.T) {
	m := updateModel(t)
	m.streaming = true
	_, cmd := m.handleUpdatePlanned(updatePlannedMsg{plan: update.Plan{Tag: "v0.26.0"}})
	if cmd != nil {
		t.Fatal("must not quit mid-answer")
	}
	if _, ok := update.Pending(); ok {
		t.Fatal("nothing should be queued while an answer runs")
	}
}

func TestUpdateCancelDropsTheQueuedInstall(t *testing.T) {
	m := updateModel(t)
	update.Schedule(update.Plan{Tag: "v0.26.0"})
	m.submit("/update cancel")
	if _, ok := update.Pending(); ok {
		t.Fatal("/update cancel left the install queued")
	}
}

func TestUpdateRunningVersionIsRefused(t *testing.T) {
	m := updateModel(t)
	next, cmd := m.submit("/update v0.25.0")
	if cmd != nil {
		t.Fatal("reinstalling the running version should not look anything up")
	}
	if got := lastStatus(t, next.(ChatModel)); !strings.Contains(got, "already running") {
		t.Errorf("status = %q", got)
	}
}

func TestUpdateCheckWithoutTheCheckSaysSo(t *testing.T) {
	m := updateModel(t)
	t.Setenv("GAIA_DISABLE_UPDATE", "1")
	next, cmd := m.submit("/update now")
	if cmd != nil || !strings.Contains(lastStatus(t, next.(ChatModel)), "GAIA_DISABLE_UPDATE") {
		t.Fatal("a disabled install must say why /update does nothing")
	}
}

func TestAutoOffCancelsTheInstallQueuedForQuit(t *testing.T) {
	m := updateModel(t)
	update.Schedule(update.Plan{Tag: "v0.26.0"})
	_, cmd := m.submit("/update auto off")
	m.Update(cmd())
	if _, ok := update.Pending(); ok {
		t.Fatal("turning auto-update off still installs on quit")
	}
}

func TestInstallSavesTheConversationBeforeClosing(t *testing.T) {
	m := updateModel(t)
	m.messages = append(m.messages,
		Message{Role: RoleUser, Content: "summarise my notes"},
		Message{Role: RoleAssistant, Content: "Here is the summary."})
	next, cmd := m.handleUpdatePlanned(updatePlannedMsg{plan: update.Plan{Tag: "v0.26.0"}})
	if cmd == nil {
		t.Fatal("expected the TUI to close for the install")
	}
	status := lastStatus(t, next.(ChatModel))
	_, path, ok := strings.Cut(status, "saved to ")
	if !ok {
		t.Fatalf("status names no saved file: %q", status)
	}
	data, err := os.ReadFile(strings.TrimSuffix(path, "."))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "summarise my notes") || !strings.Contains(string(data), "Here is the summary.") {
		t.Errorf("saved transcript is missing the conversation:\n%s", data)
	}
}

func TestInstallIsRefusedWhenTheConversationCannotBeSaved(t *testing.T) {
	m := updateModel(t)
	home := os.Getenv("HOME")
	// A file where the .gaia folder should be makes the save fail.
	os.WriteFile(filepath.Join(home, ".gaia"), []byte("x"), 0o644)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "keep this"})
	next, cmd := m.handleUpdatePlanned(updatePlannedMsg{plan: update.Plan{Tag: "v0.26.0"}})
	if cmd != nil {
		t.Fatal("must not close GAIA when the conversation was not saved")
	}
	if _, ok := update.Pending(); ok {
		t.Fatal("nothing should be queued")
	}
	if got := lastError(t, next.(ChatModel)); !strings.Contains(got, "could not be saved") {
		t.Errorf("error = %q", got)
	}
}

func TestInstallWaitsForSetup(t *testing.T) {
	m := updateModel(t)
	m.setupRunning = true
	if _, cmd := m.handleUpdatePlanned(updatePlannedMsg{plan: update.Plan{Tag: "v0.26.0"}}); cmd != nil {
		t.Fatal("must not close GAIA while setup runs")
	}
}

func TestConversationReopensOnceAfterAnUpdate(t *testing.T) {
	m := updateModel(t)
	m.messages = append(m.messages,
		Message{Role: RoleUser, Content: "plan my week"},
		Message{Role: RoleAssistant, Content: "Monday: review."})
	if _, cmd := m.handleUpdatePlanned(updatePlannedMsg{plan: update.Plan{Tag: "v0.26.0"}}); cmd == nil {
		t.Fatal("expected the install to go ahead")
	}

	update.Current = "0.26.0" // the next launch is the new build
	got, err := TakeResumedConversation()
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 || got[0].Content != "plan my week" || got[1].Content != "Monday: review." {
		t.Fatalf("restored = %+v", got)
	}
	if !strings.Contains(got[2].Content, "updating to GAIA 0.26.0 (was 0.25.0)") ||
		!strings.Contains(got[2].Content, "new session") {
		t.Errorf("divider = %q", got[2].Content)
	}
	if again, _ := TakeResumedConversation(); again != nil {
		t.Error("the conversation came back a second time")
	}
}

func TestResumeSaysWhenTheUpdateDidNotFinish(t *testing.T) {
	m := updateModel(t)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "hi"})
	m.handleUpdatePlanned(updatePlannedMsg{plan: update.Plan{Tag: "v0.26.0"}})
	got, _ := TakeResumedConversation() // same version: the install failed
	if last := got[len(got)-1].Content; !strings.Contains(last, "did not finish") {
		t.Errorf("divider = %q", last)
	}
}

func TestExitWithAQueuedUpdateKeepsTheConversation(t *testing.T) {
	m := updateModel(t)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "draft the memo"})
	update.Schedule(update.Plan{Tag: "v0.26.0"})
	if err := m.KeepConversationForUpdate(); err != nil {
		t.Fatal(err)
	}
	got, err := TakeResumedConversation()
	if err != nil || len(got) == 0 || got[0].Content != "draft the memo" {
		t.Fatalf("restored = %+v, %v", got, err)
	}
	if _, ok := update.Pending(); !ok {
		t.Error("a saved conversation must not cancel the install")
	}
}

func TestExitWithNothingQueuedSavesNothing(t *testing.T) {
	m := updateModel(t)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "hi"})
	if err := m.KeepConversationForUpdate(); err != nil {
		t.Fatal(err)
	}
	if got, _ := TakeResumedConversation(); got != nil {
		t.Fatal("a quit with no update queued must not reopen the conversation")
	}
}

func TestExitSaveFailureSkipsTheInstallAndSaysSo(t *testing.T) {
	m := updateModel(t)
	os.WriteFile(filepath.Join(os.Getenv("HOME"), ".gaia"), []byte("x"), 0o644)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "keep this"})
	update.Schedule(update.Plan{Tag: "v0.26.0"})
	err := m.KeepConversationForUpdate()
	if err == nil || !strings.Contains(err.Error(), "v0.26.0 was not installed") {
		t.Fatalf("err = %v", err)
	}
	if _, ok := update.Pending(); ok {
		t.Fatal("the install must be called off when the conversation was not saved")
	}
}

func TestTwoWindowsDoNotShareOneResumeFile(t *testing.T) {
	m := updateModel(t)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "this window"})
	if err := m.saveForResume("v0.26.0"); err != nil {
		t.Fatal(err)
	}
	dir, _ := resumeDir()
	// Another window's file, saved earlier.
	other := filepath.Join(dir, "1.json")
	os.WriteFile(other, []byte(`{"from_version":"0.25.0","to_tag":"v0.26.0","messages":[{"role":"user","content":"other window"}]}`), 0o600)
	old := time.Now().Add(-time.Hour)
	os.Chtimes(other, old, old)

	got, _ := TakeResumedConversation()
	if got[0].Content != "this window" {
		t.Fatalf("took %q first; want the newest", got[0].Content)
	}
	got, _ = TakeResumedConversation()
	if got[0].Content != "other window" {
		t.Fatalf("the other window's conversation was lost: %+v", got)
	}
}
