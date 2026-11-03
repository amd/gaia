// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/images"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// `/image` shows and saves the model, size and steps the flagship creates
// images with. The settings live in ~/.gaia/config.json, shared with the
// Agent UI, and are checked against Lemonade's catalog the same way.
const imageCommandPrefix = "/image"

const imageUsage = "Usage: /image (show the settings and models) · /image model <id> · " +
	"/image size <WxH|auto> · /image steps <n|auto> · /image open (the last image created)"

const imageCatalogTimeout = 15 * time.Second

// imageSuffixes are the files /image open hands to the OS; anything else
// could be a program, and the default handler would run it.
var imageSuffixes = map[string]bool{".png": true, ".jpg": true, ".jpeg": true, ".webp": true, ".gif": true}

func isImageCommand(query string) bool {
	trimmed := strings.TrimSpace(query)
	return trimmed == imageCommandPrefix || strings.HasPrefix(trimmed, imageCommandPrefix+" ")
}

func imageCommandFields(query string) []string {
	return strings.Fields(strings.TrimPrefix(strings.TrimSpace(query), imageCommandPrefix))
}

// imageResultMsg is a finished /image request: a line to show, or why not.
type imageResultMsg struct {
	text string
	err  error
}

// runImageCommand dispatches one /image line.
func (m ChatModel) runImageCommand(query string) (tea.Model, tea.Cmd) {
	if m.agentID != setupAgentID {
		return m.imageError(m.agentName + " does not create images — only the gaia flagship agent does."), nil
	}
	fields := imageCommandFields(query)
	base := m.lemonadeBaseURL
	switch {
	case len(fields) == 0:
		return m, func() tea.Msg { return showImageSettings(base) }
	case len(fields) == 1 && fields[0] == "open":
		return m.openLastImage()
	case len(fields) == 2 && fields[0] == "model":
		id := fields[1]
		return m, func() tea.Msg { return saveImageModel(base, id) }
	case len(fields) == 2 && fields[0] == "size":
		size, err := parseImageSizeArg(fields[1])
		if err != nil {
			return m.imageError(err.Error()), nil
		}
		return m, func() tea.Msg { return saveImageSize(base, size) }
	case len(fields) == 2 && fields[0] == "steps":
		steps, err := parseImageStepsArg(fields[1])
		if err != nil {
			return m.imageError(err.Error()), nil
		}
		return m, func() tea.Msg { return saveImageSteps(base, steps) }
	}
	return m.imageError(imageUsage), nil
}

func (m ChatModel) imageError(text string) ChatModel {
	m.messages = append(m.messages, Message{Role: RoleError, Content: text})
	m.updateViewport()
	return m
}

func (m ChatModel) handleImageResult(msg imageResultMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.imageError(msg.err.Error()), nil
	}
	return m.statusNote(msg.text), nil
}

// parseImageSizeArg is nil for auto, else a WIDTHxHEIGHT size.
func parseImageSizeArg(arg string) (*string, error) {
	if strings.EqualFold(arg, "auto") {
		return nil, nil
	}
	if _, _, err := images.ParseSize(arg); err != nil {
		return nil, err
	}
	return &arg, nil
}

// parseImageStepsArg is nil for auto, else a step count in range.
func parseImageStepsArg(arg string) (*int, error) {
	if strings.EqualFold(arg, "auto") {
		return nil, nil
	}
	n, err := strconv.Atoi(arg)
	if err != nil {
		return nil, fmt.Errorf("Image steps must be a whole number from 1 to %d, or auto; got %q.", images.MaxSteps, arg)
	}
	if n < 1 || n > images.MaxSteps {
		return nil, fmt.Errorf("Image steps must be from 1 to %d, got %d.", images.MaxSteps, n)
	}
	return &n, nil
}

// imageChoices reads the image models this Lemonade offers.
func imageChoices(base string) ([]images.Choice, error) {
	ctx, cancel := context.WithTimeout(context.Background(), imageCatalogTimeout)
	defer cancel()
	client := lemonade.New(base)
	catalog, err := client.ImageCatalog(ctx)
	if err != nil {
		return nil, fmt.Errorf("Could not read the image models from Lemonade at %s: %v", client.Address(), err)
	}
	return images.Choices(catalog), nil
}

func savedImageModel(s preflight.ImageSettings) string {
	if s.Model == "" {
		return images.DefaultModel
	}
	return s.Model
}

func showImageSettings(base string) tea.Msg {
	saved, err := preflight.ReadImageSettings()
	if err != nil {
		return imageResultMsg{err: err}
	}
	choices, err := imageChoices(base)
	if err != nil {
		return imageResultMsg{err: fmt.Errorf("%s: %w", describeImageSettings(saved, nil), err)}
	}
	return imageResultMsg{text: renderImageSettings(saved, choices)}
}

// describeImageSettings is one line naming what the next image is created
// with. choices nil means the catalog could not be read.
func describeImageSettings(s preflight.ImageSettings, choices []images.Choice) string {
	model := savedImageModel(s)
	choice, known := images.Find(choices, model)
	size := "its default size"
	if s.Size != nil {
		size = *s.Size
	} else if known {
		size = choice.DefaultSize + " (its default)"
	}
	steps := "its default steps"
	if s.Steps != nil {
		steps = fmt.Sprintf("%d steps", *s.Steps)
	} else if known {
		steps = fmt.Sprintf("%d steps (its default)", choice.DefaultSteps)
	}
	return "Images are created with " + model + " · " + size + " · " + steps
}

func renderImageSettings(s preflight.ImageSettings, choices []images.Choice) string {
	var b strings.Builder
	b.WriteString(describeImageSettings(s, choices) + ".")
	if _, ok := images.Find(choices, savedImageModel(s)); !ok {
		b.WriteString("\n[!] " + savedImageModel(s) + " is not on this Lemonade, or it is not downloaded — pick one below.")
	}
	if len(choices) == 0 {
		b.WriteString("\nThis Lemonade offers no image models.")
	} else {
		b.WriteString("\nModels on this Lemonade:")
		for _, c := range choices {
			line := fmt.Sprintf("\n  %s — %s, %d steps by default", c.ID, c.DefaultSize, c.DefaultSteps)
			if c.DownloadsOnFirstUse {
				line += " · downloads on first use"
			}
			b.WriteString(line)
			b.WriteString("\n    sizes: " + strings.Join(c.Sizes, " "))
		}
	}
	b.WriteString("\n/image model <id> · /image size <WxH|auto> · /image steps <n|auto> · /image open")
	return b.String()
}

// saveImage checks a model, size and steps against the catalog and saves all
// three, as the Agent UI's settings route does.
func saveImage(base, model string, size *string, steps *int) tea.Msg {
	choices, err := imageChoices(base)
	if err != nil {
		return imageResultMsg{err: err}
	}
	if _, err := images.Check(choices, model, size, steps); err != nil {
		return imageResultMsg{err: err}
	}
	path, err := preflight.WriteImageSettings(model, size, steps)
	if err != nil {
		return imageResultMsg{err: fmt.Errorf("Could not save the image settings: %w", err)}
	}
	saved := preflight.ImageSettings{Model: model, Size: size, Steps: steps}
	return imageResultMsg{text: "[✓] " + describeImageSettings(saved, choices) + ". Saved in " + path + "."}
}

// saveImageModel switches model. A new model goes back to its own size and
// steps, since sizes differ per model.
func saveImageModel(base, model string) tea.Msg {
	saved, err := preflight.ReadImageSettings()
	if err != nil {
		return imageResultMsg{err: err}
	}
	size, steps := saved.Size, saved.Steps
	if model != savedImageModel(saved) {
		size, steps = nil, nil
	}
	return saveImage(base, model, size, steps)
}

func saveImageSize(base string, size *string) tea.Msg {
	saved, err := preflight.ReadImageSettings()
	if err != nil {
		return imageResultMsg{err: err}
	}
	return saveImage(base, savedImageModel(saved), size, saved.Steps)
}

func saveImageSteps(base string, steps *int) tea.Msg {
	saved, err := preflight.ReadImageSettings()
	if err != nil {
		return imageResultMsg{err: err}
	}
	return saveImage(base, savedImageModel(saved), saved.Size, steps)
}

// --- created images -----------------------------------------------------------

// createdImagePath is the image a successful tool result says it wrote, or "".
// The flagship's tool result carries image_path; an image card carries path.
func createdImagePath(render string, data json.RawMessage) string {
	var payload struct {
		ImagePath string `json:"image_path"`
		Path      string `json:"path"`
	}
	if len(data) == 0 || json.Unmarshal(data, &payload) != nil {
		return ""
	}
	path := strings.TrimSpace(payload.ImagePath)
	if path == "" && render == "image" {
		path = strings.TrimSpace(payload.Path)
	}
	if path == "" || !imageSuffixes[strings.ToLower(filepath.Ext(path))] {
		return ""
	}
	return path
}

// noteCreatedImage remembers an image the agent created for /image open, and
// says where it is unless an image card already does.
func (m *ChatModel) noteCreatedImage(render string, data json.RawMessage) {
	path := createdImagePath(render, data)
	if path == "" {
		return
	}
	m.lastImagePath = path
	if render == "image" {
		return
	}
	m.messages = append(m.messages, Message{
		Role:      RoleStatus,
		Content:   "Created image: " + path,
		ImagePath: path,
	})
}

// fileURL is path as a file:// URL, for a terminal hyperlink or the Windows
// opener.
func fileURL(goos, path string) string {
	if goos == "windows" {
		path = "/" + strings.ReplaceAll(path, `\`, "/")
	}
	return (&url.URL{Scheme: "file", Path: path}).String()
}

// linkImagePath marks the path in a rendered "Created image" line as a
// terminal hyperlink. A line wider than the pane is left alone: the viewport
// clips it, and a clipped link would never close.
func linkImagePath(rendered, path string, width int) string {
	target := fileURL(runtime.GOOS, path)
	lines := strings.Split(rendered, "\n")
	for i, line := range lines {
		if !strings.Contains(line, path) || (width > 0 && ansi.StringWidth(line) > width) {
			continue
		}
		lines[i] = strings.Replace(line, path, osc8Prefix+target+osc8Sep+path+osc8Close, 1)
		break
	}
	return strings.Join(lines, "\n")
}

// imageOpenArgv is the command that opens path in the OS's default viewer.
// Windows gets a file:// URL, which carries no spaces or quotes for rundll32
// to re-parse.
func imageOpenArgv(goos, path string) []string {
	switch goos {
	case "windows":
		return []string{"rundll32", "url.dll,FileProtocolHandler", fileURL(goos, path)}
	case "darwin":
		return []string{"open", path}
	default:
		return []string{"xdg-open", path}
	}
}

// Seams for tests: which programs exist, and starting one.
var (
	imageLookPath = exec.LookPath
	imageStart    = func(argv []string) error {
		cmd := exec.Command(argv[0], argv[1:]...)
		if err := cmd.Start(); err != nil {
			return err
		}
		go func() { _ = cmd.Wait() }()
		return nil
	}
)

// openImage hands path to the OS's default image viewer.
func openImage(path string) tea.Cmd {
	return func() tea.Msg {
		info, err := os.Stat(path)
		if errors.Is(err, os.ErrNotExist) {
			return imageResultMsg{err: fmt.Errorf("The image is no longer at %s.", path)}
		}
		if err != nil {
			return imageResultMsg{err: fmt.Errorf("Could not open %s: %v", path, err)}
		}
		if !info.Mode().IsRegular() {
			return imageResultMsg{err: fmt.Errorf("%s is not an image file.", path)}
		}
		argv := imageOpenArgv(runtime.GOOS, path)
		if _, err := imageLookPath(argv[0]); err != nil {
			return imageResultMsg{err: fmt.Errorf("This computer has no program for opening images "+
				"(%s was not found). The image is at %s.", argv[0], path)}
		}
		if err := imageStart(argv); err != nil {
			return imageResultMsg{err: fmt.Errorf("Could not open %s: %v", path, err)}
		}
		return imageResultMsg{text: "Opened " + path + "."}
	}
}

func (m ChatModel) openLastImage() (tea.Model, tea.Cmd) {
	if m.lastImagePath == "" {
		return m.statusNote("No image has been created in this session yet."), nil
	}
	return m, openImage(m.lastImagePath)
}
