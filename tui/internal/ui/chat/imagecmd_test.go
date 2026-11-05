// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

// imageCatalog is the slice of Lemonade's catalog the Python tests use.
const imageCatalog = `{"data":[
 {"id":"SDXL-Turbo","recipe":"sd-cpp","downloaded":false,"labels":["image"],"size":6.46,
  "image_defaults":{"width":512,"height":512,"steps":4,"soft_pixel_cap":1048576}},
 {"id":"Z-Image-Turbo","recipe":"sd-cpp","downloaded":true,"labels":["image"],"size":20.7,
  "image_defaults":{"width":1024,"height":1024,"steps":9,"soft_pixel_cap":2097152}},
 {"id":"Krea-2-Turbo","recipe":"thenoise","downloaded":false,"labels":["image"],"size":35.0,
  "image_defaults":{"width":1024,"height":1024,"steps":8,"soft_pixel_cap":4194304}},
 {"id":"RealESRGAN-x4plus","recipe":"sd-cpp","downloaded":true,"labels":["upscaling","image"],"image_defaults":null},
 {"id":"Gemma-4-E4B-it-GGUF","recipe":"llamacpp","downloaded":true,"labels":["vision"]}
]}`

// imageModel is a flagship chat on a fake Lemonade, saving to a throwaway
// config. It returns the config path and how many catalog reads were made.
func imageModel(t *testing.T, config string) (ChatModel, string, *int32) {
	t.Helper()
	var reads int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/models" || r.URL.Query().Get("show_all") != "true" {
			http.NotFound(w, r)
			return
		}
		atomic.AddInt32(&reads, 1)
		_, _ = w.Write([]byte(imageCatalog))
	}))
	t.Cleanup(srv.Close)
	path := filepath.Join(t.TempDir(), "config.json")
	if config != "" {
		if err := os.WriteFile(path, []byte(config), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("GAIA_CONFIG_FILE", path)
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = 100, 30
	m.lemonadeBaseURL = srv.URL
	return m, path, &reads
}

// runImage submits line and applies whatever its command reports.
func runImage(t *testing.T, m ChatModel, line string) ChatModel {
	t.Helper()
	next, cmd := m.submit(line)
	m = next.(ChatModel)
	if m.streaming {
		t.Fatalf("%q started a turn", line)
	}
	if cmd != nil {
		next, _ = m.Update(cmd())
		m = next.(ChatModel)
	}
	return m
}

func savedImageKeys(t *testing.T, path string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{}
	}
	if err != nil {
		t.Fatal(err)
	}
	doc := map[string]any{}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	out := map[string]any{}
	for k, v := range doc {
		if strings.HasPrefix(k, "image_") {
			out[k] = v
		}
	}
	return out
}

func TestImageShowsTheSettingsAndUsableModels(t *testing.T) {
	m, _, _ := imageModel(t, "")
	m = runImage(t, m, "/image")
	got := lastStatus(t, m)
	for _, want := range []string{
		"SDXL-Turbo · 512x512 (its default) · 4 steps (its default)",
		"Z-Image-Turbo — 1024x1024, 9 steps by default",
		"SDXL-Turbo — 512x512, 4 steps by default · downloads on first use",
		"1920x1080",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("status lacks %q:\n%s", want, got)
		}
	}
	for _, hidden := range []string{"Krea-2-Turbo", "RealESRGAN", "Gemma"} {
		if strings.Contains(got, hidden) {
			t.Errorf("status offers %s:\n%s", hidden, got)
		}
	}
}

func TestImageNamesASavedModelThisLemonadeLacks(t *testing.T) {
	m, _, _ := imageModel(t, `{"image_model":"Krea-2-Turbo"}`)
	m = runImage(t, m, "/image")
	if got := lastStatus(t, m); !strings.Contains(got, "Krea-2-Turbo is not on this Lemonade") {
		t.Errorf("status = %q", got)
	}
}

func TestImageSettingsSaveToTheSharedConfig(t *testing.T) {
	m, path, _ := imageModel(t, `{"profile":"chat"}`)
	m = runImage(t, m, "/image model Z-Image-Turbo")
	m = runImage(t, m, "/image size 1280x720")
	m = runImage(t, m, "/image steps 12")
	want := map[string]any{"image_model": "Z-Image-Turbo", "image_size": "1280x720", "image_steps": float64(12)}
	if got := savedImageKeys(t, path); !reflect.DeepEqual(got, want) {
		t.Fatalf("saved %v, want %v", got, want)
	}
	if got := lastStatus(t, m); !strings.Contains(got, "Z-Image-Turbo · 1280x720 · 12 steps") {
		t.Errorf("confirmation = %q", got)
	}
}

func TestANewImageModelGoesBackToItsOwnSizeAndSteps(t *testing.T) {
	m, path, _ := imageModel(t, `{"image_model":"Z-Image-Turbo","image_size":"1440x1440","image_steps":20}`)
	m = runImage(t, m, "/image model SDXL-Turbo")
	want := map[string]any{"image_model": "SDXL-Turbo", "image_size": nil, "image_steps": nil}
	if got := savedImageKeys(t, path); !reflect.DeepEqual(got, want) {
		t.Fatalf("saved %v, want %v", got, want)
	}
	if got := lastStatus(t, m); !strings.Contains(got, "512x512 (its default) · 4 steps (its default)") {
		t.Errorf("confirmation = %q", got)
	}
}

func TestImageAutoMeansTheModelsDefault(t *testing.T) {
	m, path, _ := imageModel(t, `{"image_model":"Z-Image-Turbo","image_size":"1440x1440","image_steps":20}`)
	runImage(t, m, "/image size auto")
	want := map[string]any{"image_model": "Z-Image-Turbo", "image_size": nil, "image_steps": float64(20)}
	if got := savedImageKeys(t, path); !reflect.DeepEqual(got, want) {
		t.Fatalf("saved %v, want %v", got, want)
	}
}

func TestImageSizeAloneSavesTheDefaultModelWithIt(t *testing.T) {
	m, path, _ := imageModel(t, "")
	runImage(t, m, "/image size 1024x1024")
	want := map[string]any{"image_model": "SDXL-Turbo", "image_size": "1024x1024", "image_steps": nil}
	if got := savedImageKeys(t, path); !reflect.DeepEqual(got, want) {
		t.Fatalf("saved %v, want %v", got, want)
	}
}

func TestAnImageSettingLemonadeCannotMakeIsRefusedAndNothingSaved(t *testing.T) {
	for _, tc := range []struct{ line, named string }{
		{"/image model Krea-2-Turbo", "not downloaded"},
		{"/image size 4096x4096", "4096x4096"},
		{"/image size big", "WIDTHxHEIGHT"},
		{"/image steps 0", "1 to 100"},
		{"/image steps lots", "whole number"},
		{"/image please", "Usage: /image"},
		{"/image model", "Usage: /image"},
	} {
		m, path, _ := imageModel(t, "")
		m = runImage(t, m, tc.line)
		if got := lastError(t, m); !strings.Contains(got, tc.named) {
			t.Errorf("%q: error = %q; want it to name %q", tc.line, got, tc.named)
		}
		if got := savedImageKeys(t, path); len(got) != 0 {
			t.Errorf("%q saved %v", tc.line, got)
		}
	}
}

func TestImageShapeErrorsNeedNoLemonade(t *testing.T) {
	m, _, reads := imageModel(t, "")
	runImage(t, m, "/image steps 500")
	if *reads != 0 {
		t.Errorf("a bad step count read the catalog %d times", *reads)
	}
}

func TestImageSaysWhenLemonadeCannotBeRead(t *testing.T) {
	m, path, _ := imageModel(t, "")
	m.lemonadeBaseURL = "http://127.0.0.1:1"
	m = runImage(t, m, "/image model Z-Image-Turbo")
	if got := lastError(t, m); !strings.Contains(got, "Could not read the image models from Lemonade at http://127.0.0.1:1") {
		t.Errorf("error = %q", got)
	}
	if got := savedImageKeys(t, path); len(got) != 0 {
		t.Errorf("saved %v with Lemonade down", got)
	}
}

func TestImageIsOnlyForTheFlagship(t *testing.T) {
	m, _, _ := imageModel(t, "")
	m.agentID = "email"
	m = runImage(t, m, "/image")
	if got := lastError(t, m); !strings.Contains(got, "only the gaia flagship agent") {
		t.Errorf("error = %q", got)
	}
	if m.availableCommandSet()["/image"] {
		t.Error("the palette offers /image to an agent that cannot create images")
	}
}

func TestImageIsNotSentToTheAgent(t *testing.T) {
	if !isImageCommand("/image") || !isImageCommand(" /image  size 512x512") {
		t.Error("/image forms not recognised")
	}
	if isImageCommand("/images") || isImageCommand("draw an /image of a cat") {
		t.Error("a non-command was taken for /image")
	}
}

// --- created images ------------------------------------------------------------

func imageResult(path string) event.CanonicalToolResultEvent {
	data, _ := json.Marshal(map[string]any{"summary": "Generated", "success": true, "status": "success", "image_path": path})
	return event.CanonicalToolResultEvent{Type: "tool_result", Tool: "generate_image", Data: data}
}

func TestACreatedImageIsNamedAndRemembered(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sd_123.png")
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "generate_image"},
		imageResult(path),
	)
	if got := lastStatus(t, m); got != "Created image: "+path {
		t.Errorf("status = %q", got)
	}
	if m.lastImagePath != path {
		t.Errorf("lastImagePath = %q", m.lastImagePath)
	}
}

func TestAResultNamingANonImageIsNotAnImage(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "generate_image"},
		imageResult(`C:\tools\setup.exe`),
	)
	if m.lastImagePath != "" || hasStatus(m) {
		t.Errorf("an .exe was taken for an image: %q", m.lastImagePath)
	}
}

func TestAnImageCardIsRememberedWithoutASecondLine(t *testing.T) {
	data := json.RawMessage(`{"src":"/api/media/image/a.png","alt":"cat","path":"/home/u/.gaia/cache/sd/images/a.png"}`)
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "generate_image"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "generate_image", Render: "image", Data: data},
	)
	if m.lastImagePath != "/home/u/.gaia/cache/sd/images/a.png" {
		t.Errorf("lastImagePath = %q", m.lastImagePath)
	}
	for _, msg := range m.messages {
		if strings.HasPrefix(msg.Content, "Created image") {
			t.Error("the card's path was printed a second time")
		}
	}
}

func TestTheImagePathIsATerminalLink(t *testing.T) {
	path := "/home/u/.gaia/cache/sd/images/my cat.png"
	m := newTestChat(t)
	m.noteCreatedImage("", imageResult(path).Data)
	msg := m.messages[len(m.messages)-1]
	out := m.renderMessage(&msg, nil)
	if !strings.Contains(out, osc8Prefix+fileURL(runtime.GOOS, path)+osc8Sep+path+osc8Close) {
		t.Errorf("no hyperlink around the path:\n%q", out)
	}
	narrow := linkImagePath("  Created image: "+path, path, 10)
	if strings.Contains(narrow, osc8Prefix) {
		t.Error("a line wider than the pane got a link that the viewport would cut open")
	}
}

func TestFileURL(t *testing.T) {
	for _, tc := range []struct{ goos, path, want string }{
		{"windows", `C:\Users\A B\.gaia\cache\sd\images\x.png`, "file:///C:/Users/A%20B/.gaia/cache/sd/images/x.png"},
		{"linux", "/home/a b/x.png", "file:///home/a%20b/x.png"},
		{"darwin", "/Users/a/x&y.png", "file:///Users/a/x&y.png"},
	} {
		if got := fileURL(tc.goos, tc.path); got != tc.want {
			t.Errorf("fileURL(%s, %q) = %q, want %q", tc.goos, tc.path, got, tc.want)
		}
	}
}

func TestImageOpenArgvPerOS(t *testing.T) {
	for _, tc := range []struct {
		goos, path string
		want       []string
	}{
		{"windows", `C:\Users\A B\x.png`, []string{"rundll32", "url.dll,FileProtocolHandler", "file:///C:/Users/A%20B/x.png"}},
		{"darwin", "/Users/a b/x.png", []string{"open", "/Users/a b/x.png"}},
		{"linux", "/home/a b/x.png", []string{"xdg-open", "/home/a b/x.png"}},
	} {
		if got := imageOpenArgv(tc.goos, tc.path); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%s: argv = %q, want %q", tc.goos, got, tc.want)
		}
	}
}

// stubOpener records what /image open would start.
func stubOpener(t *testing.T, found bool) *[]string {
	t.Helper()
	var started []string
	prevLook, prevStart := imageLookPath, imageStart
	imageLookPath = func(name string) (string, error) {
		if !found {
			return "", errors.New("not found")
		}
		return name, nil
	}
	imageStart = func(argv []string) error { started = argv; return nil }
	t.Cleanup(func() { imageLookPath, imageStart = prevLook, prevStart })
	return &started
}

func TestImageOpenWithNoImageSaysSo(t *testing.T) {
	started := stubOpener(t, true)
	m, _, _ := imageModel(t, "")
	m = runImage(t, m, "/image open")
	if got := lastStatus(t, m); !strings.Contains(got, "No image has been created") {
		t.Errorf("status = %q", got)
	}
	if *started != nil {
		t.Errorf("started %q", *started)
	}
}

func TestImageOpenOpensTheLastImage(t *testing.T) {
	started := stubOpener(t, true)
	path := filepath.Join(t.TempDir(), "sd_1.png")
	if err := os.WriteFile(path, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}
	m, _, _ := imageModel(t, "")
	m.noteCreatedImage("", imageResult(path).Data)
	m = runImage(t, m, "/image open")
	if want := imageOpenArgv(runtime.GOOS, path); !reflect.DeepEqual(*started, want) {
		t.Errorf("started %q, want %q", *started, want)
	}
	if got := lastStatus(t, m); got != "Opened "+path+"." {
		t.Errorf("status = %q", got)
	}
}

func TestImageOpenWithNoViewerSaysSoPlainly(t *testing.T) {
	stubOpener(t, false)
	path := filepath.Join(t.TempDir(), "sd_1.png")
	if err := os.WriteFile(path, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}
	m, _, _ := imageModel(t, "")
	m.lastImagePath = path
	m = runImage(t, m, "/image open")
	got := lastError(t, m)
	if !strings.Contains(got, "no program for opening images") || !strings.Contains(got, path) {
		t.Errorf("error = %q", got)
	}
}

func TestImageOpenSaysWhenTheFileIsGone(t *testing.T) {
	stubOpener(t, true)
	m, _, _ := imageModel(t, "")
	m.lastImagePath = filepath.Join(t.TempDir(), "gone.png")
	m = runImage(t, m, "/image open")
	if got := lastError(t, m); !strings.Contains(got, "no longer at") {
		t.Errorf("error = %q", got)
	}
}

func TestClickingTheImageLineOpensIt(t *testing.T) {
	started := stubOpener(t, true)
	path := filepath.Join(t.TempDir(), "sd_1.png")
	if err := os.WriteFile(path, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}
	m, _, _ := imageModel(t, "")
	m.resize()
	m.noteCreatedImage("", imageResult(path).Data)
	m.updateViewport()
	row := -1
	for i := 0; i < m.height; i++ {
		if line, ok := m.viewportLineAt(i); ok && strings.Contains(line, "Created image") {
			row = i
		}
	}
	if row < 0 {
		t.Fatal("the image line is not on screen")
	}
	_, cmd := m.handleClick(tea.MouseMsg{X: 4, Y: row, Button: tea.MouseButtonLeft})
	if cmd == nil {
		t.Fatal("the click did nothing")
	}
	cmd()
	if len(*started) == 0 {
		t.Error("the image was not opened")
	}
}

// chatModelTier makes /image see chatID as the chat model, at tier.
func chatModelTier(t *testing.T, m ChatModel, chatID, tier string) ChatModel {
	t.Helper()
	entry := setupapitest.Entry(chatID, []string{setupapi.RoleChat}, 5.97, true, true, true, nil, 0, 0)
	entry.Name = "Gemma 4 E4B"
	entry.FeatureTier = tier
	prev := newChatSetupAPI
	newChatSetupAPI = func() setupapi.API {
		return &setupapitest.Fake{P: setupapi.Plan{Catalog: []setupapi.CatalogEntry{entry}}}
	}
	t.Cleanup(func() { newChatSetupAPI = prev })
	m.modelID = chatID
	return m
}

func TestImageSaysWhenTheChatModelIsNotOfferedImages(t *testing.T) {
	m, _, _ := imageModel(t, "")
	m = chatModelTier(t, m, "Gemma-4-E4B-it-GGUF", setupapi.TierCompact)
	m = runImage(t, m, "/image")
	got := lastStatus(t, m)
	for _, want := range []string{"Gemma 4 E4B, the chat model, is too small to be offered image creation", "/models"} {
		if !strings.Contains(got, want) {
			t.Errorf("status lacks %q:\n%s", want, got)
		}
	}
}

func TestImageSaysNothingOfAChatModelThatCreatesImages(t *testing.T) {
	m, _, _ := imageModel(t, "")
	m = chatModelTier(t, m, "Gemma-4-E4B-it-GGUF", "full")
	m = runImage(t, m, "/image")
	if got := lastStatus(t, m); strings.Contains(got, "too small") {
		t.Errorf("status warns about a model that creates images:\n%s", got)
	}
}
