// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/setupapi/setupapitest"
	"github.com/amd/gaia/tui/internal/ui/modelsetup"
)

func setupFake() *setupapitest.Fake {
	url := "http://127.0.0.1:13305/api/v1"
	free := 400.0
	return &setupapitest.Fake{
		P: setupapi.Plan{
			Lemonade:  setupapi.LemonadeInfo{Mode: "gaia", URL: &url, Reachable: true},
			ChatModel: setupapi.ChatModel{ID: "Gemma-4-E4B-it-GGUF"},
			Disk:      setupapi.Disk{FreeGB: &free},
			Catalog: []setupapi.CatalogEntry{
				setupapitest.Entry("Gemma-4-E4B-it-GGUF", []string{"chat", "vision"}, 5.97, true, true, true, setupapitest.Bool(true), 8, 32),
				setupapitest.Entry("user.embeddinggemma-300m-GGUF", []string{"embedding"}, 0.33, true, true, true, setupapitest.Bool(true), 1, 32),
				setupapitest.Entry("gpt-oss-20b-GGUF", []string{"chat"}, 12.1, false, false, false, setupapitest.Bool(true), 14, 32),
			},
		},
		GPUErr: errNotOnPath,
	}
}

func withSetupAPI(t *testing.T, api setupapi.API) {
	t.Helper()
	prev := newChatSetupAPI
	newChatSetupAPI = func() setupapi.API { return api }
	t.Cleanup(func() { newChatSetupAPI = prev })
}

// feed runs cmd and delivers what the setup screens produce, a few levels deep.
func feedSetup(t *testing.T, m ChatModel, cmd tea.Cmd) ChatModel {
	t.Helper()
	for depth := 0; cmd != nil && depth < 8; depth++ {
		var msgs []tea.Msg
		var collect func(c tea.Cmd)
		collect = func(c tea.Cmd) {
			if c == nil {
				return
			}
			msg := c()
			if b, ok := msg.(tea.BatchMsg); ok {
				for _, inner := range b {
					collect(inner)
				}
				return
			}
			if modelsetup.IsMsg(msg) {
				msgs = append(msgs, msg)
			}
			switch msg.(type) {
			case modelsetup.ClosedMsg, modelsetup.FinishedMsg, modelsetup.BackMsg:
				msgs = append(msgs, msg)
			}
		}
		collect(cmd)
		var next []tea.Cmd
		for _, msg := range msgs {
			updated, c := m.Update(msg)
			m = updated.(ChatModel)
			next = append(next, c)
		}
		cmd = tea.Batch(next...)
	}
	return m
}

func TestModelsOpensTheSettingsChecklist(t *testing.T) {
	api := setupFake()
	withSetupAPI(t, api)
	m := gaiaTestModel(t)
	updated, cmd := m.submit("/models")
	m = updated.(ChatModel)
	if !m.modelSetupOpen || m.modelSetup.Mode() != modelsetup.ModeModels {
		t.Fatal("/models did not open the model settings")
	}
	m = feedSetup(t, m, cmd)
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "Models on this PC") || !strings.Contains(view, "a apply") {
		t.Fatalf("not the settings checklist:\n%s", view)
	}
	if snap := m.ControlSnapshot(); snap.Overlay != "setup-models" {
		t.Errorf("overlay = %q, want setup-models", snap.Overlay)
	}
}

// A download started from /models keeps going after the screen closes, and
// the status line says how far it is so the user can keep chatting.
func TestABackgroundDownloadShowsOnTheStatusLine(t *testing.T) {
	api := setupFake()
	withSetupAPI(t, api)
	m := gaiaTestModel(t)
	updated, cmd := m.submit("/models")
	m = feedSetup(t, updated.(ChatModel), cmd)

	// Tick gpt-oss-20b and apply.
	for _, k := range []tea.KeyMsg{
		{Type: tea.KeyDown}, {Type: tea.KeyDown},
		{Type: tea.KeySpace, Runes: []rune{' '}},
		{Type: tea.KeyRunes, Runes: []rune("a")},
	} {
		updated, cmd = m.Update(k)
		m = feedSetup(t, updated.(ChatModel), cmd)
	}
	if !api.Called("download gpt-oss-20b-GGUF") {
		t.Fatalf("apply did not download: %v", api.CallList())
	}
	api.Progress(setupapi.ModelProgress{ID: "gpt-oss-20b-GGUF", State: setupapi.StateDownloading,
		BytesDone: 4_600_000_000, BytesTotal: 12_100_000_000})
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("r")})
	m = feedSetup(t, updated.(ChatModel), cmd)
	// Leave: the download keeps going.
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = feedSetup(t, updated.(ChatModel), cmd)
	if m.modelSetupOpen {
		t.Fatal("esc did not close /models")
	}
	if m.modelSetup == nil {
		t.Fatal("closing /models dropped a running download")
	}
	if hint := m.downloadHint(); !strings.HasPrefix(hint, "downloading ") {
		t.Fatalf("status hint = %q, want downloading N%%", hint)
	}
	if !strings.Contains(ansi.Strip(m.View()), "downloading") {
		t.Fatalf("the status line does not show the download:\n%s", ansi.Strip(m.View()))
	}
	// Cancellable from there too: /models reopens the same download.
	updated, _ = m.submit("/models")
	m = updated.(ChatModel)
	if !m.modelSetupOpen || m.modelSetup.Screen() != "download" {
		t.Fatalf("/models during a download should show it, got %q", m.modelSetup.Screen())
	}
}

func TestGPUOffersOnlyWhereItApplies(t *testing.T) {
	m := gaiaTestModel(t)
	if m.availableCommandSet()["/gpu"] {
		t.Fatal("/gpu offered before anything said this PC has an integrated GPU")
	}
	updated, _ := m.Update(gpuProbedMsg{applies: true})
	m = updated.(ChatModel)
	if !m.availableCommandSet()["/gpu"] {
		t.Fatal("/gpu not offered on an integrated-GPU PC")
	}
	other, _ := newTestModel(t)
	if other.availableCommandSet()["/models"] || other.availableCommandSet()["/gpu"] {
		t.Fatal("a non-flagship agent was offered model setup")
	}
}

func TestGPUOpensTheGPUScreen(t *testing.T) {
	api := setupFake()
	withSetupAPI(t, api)
	m := gaiaTestModel(t)
	updated, _ := m.submit("/gpu")
	m = updated.(ChatModel)
	if !m.modelSetupOpen || m.modelSetup.Mode() != modelsetup.ModeGPU {
		t.Fatal("/gpu did not open the GPU memory screen")
	}
}

// First boot, not set up: the choice opens; nothing runs `gaia init`.
func TestFirstBootOpensTheModelChoice(t *testing.T) {
	withSetupAPI(t, setupFake())
	m := gaiaTestModel(t)
	m.setupChecking = true
	updated, _ := m.Update(setupCheckResultMsg{ready: false})
	m = updated.(ChatModel)
	if !m.modelSetupOpen || m.modelSetup.Mode() != modelsetup.ModeFirstRun {
		t.Fatal("a not-ready first boot must open the model choice")
	}
	if m.setupRunning {
		t.Fatal("`gaia init` ran before the user chose")
	}
	// Leaving it says what is missing and how to come back.
	updated, _ = m.Update(modelsetup.BackMsg{})
	m = updated.(ChatModel)
	if last := m.messages[len(m.messages)-1]; !strings.Contains(last.Content, "/setup") {
		t.Fatalf("leaving setup should name /setup: %q", last.Content)
	}
}

func TestFinishingFirstRunSetupSaysSo(t *testing.T) {
	withSetupAPI(t, setupFake())
	m := gaiaTestModel(t)
	updated, _ := m.submit("/setup")
	m = updated.(ChatModel)
	updated, _ = m.Update(modelsetup.FinishedMsg{})
	m = updated.(ChatModel)
	if m.modelSetup != nil || m.modelSetupOpen {
		t.Fatal("the screens stayed open after setup finished")
	}
	if last := m.messages[len(m.messages)-1]; !strings.Contains(last.Content, "Setup complete") {
		t.Fatalf("finish not announced: %q", last.Content)
	}
}
