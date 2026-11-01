package preflight

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// The context window this machine's profile pins.
//
// Modern Lemonade takes no window at launch: it has no `--ctx-size` flag and
// reads no environment variable for it (#3320), so a start command cannot carry
// one. GAIA sends the window with every `/load` instead. The value here is used
// for two things only: the legacy CLI's `--ctx-size` flag, and the AI model row,
// which reports the window the server really loaded and says so when it falls
// short of this target (see markCtxShortfall).
//
// The value is DERIVED, never hardcoded. GAIA pins one context window per device
// profile (lemonade_client.GPU_CTX_SIZE / NPU_CTX_SIZE) and `gaia init` records
// which profile this machine runs in ~/.gaia/config.json as `default_device`.
// Reading that file is how the TUI stays consistent with the Python without
// depending on the `gaia` CLI being on PATH.

// Context windows per device profile. These MUST equal
// gaia.llm.lemonade_client.GPU_CTX_SIZE / NPU_CTX_SIZE: the NPU's FastFlowLM
// build is registered at 32768 and cannot reach 65536, so handing it the GPU
// window fails the load outright — and collapsing them to one number would cap
// GPU doc-Q&A at 32K and re-open the #1030 context overflow.
const (
	gpuCtxSize = 65536 // GPU/CPU — Gemma-4-E4B-it-GGUF (llama.cpp)
	npuCtxSize = 32768 // NPU — gemma4-it-e2b-FLM (FastFlowLM ceiling)
)

// Env overrides for where the config lives, mirroring gaia.config exactly
// (GAIA_CONFIG_FILE wins over GAIA_CONFIG_DIR, which wins over ~/.gaia).
const (
	configFileEnv = "GAIA_CONFIG_FILE"
	configDirEnv  = "GAIA_CONFIG_DIR"
)

// profileCtxSize resolves the context window for this machine's device profile.
//
// It mirrors lemonade_client.profile_ctx_size, including its fallback: anything
// that is not "npu" — an absent config, an unreadable one, an unknown device —
// resolves to the GPU window, which is also GaiaConfig's own default. Guessing
// NPU instead would hand a GPU machine a window half what its model can use.
func profileCtxSize(p hostProbe) int {
	if strings.EqualFold(strings.TrimSpace(readDefaultDevice(p)), "npu") {
		return npuCtxSize
	}
	return gpuCtxSize
}

// readDefaultDevice returns `default_device` from ~/.gaia/config.json, or "" when
// there is no readable answer. A missing file is the normal state on a machine
// that has never run `gaia init`, so it is not an error — it just means the
// caller falls back the same way GaiaConfig does.
func readDefaultDevice(p hostProbe) string {
	path := configPath(p)
	if path == "" {
		return ""
	}
	raw, err := p.readFile(path)
	if err != nil {
		return ""
	}
	var cfg struct {
		DefaultDevice string `json:"default_device"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return ""
	}
	return cfg.DefaultDevice
}

func configPath(p hostProbe) string {
	return lemonade.ConfigPathFrom(p.getenv, p.homeDir)
}

// legacyCtxFlag is how the PRE-10.7 CLI took the window: a flag
// (lemonade_launcher.build_start_command's legacy branch).
func legacyCtxFlag(ctx int) string {
	return fmt.Sprintf(" --ctx-size %d", ctx)
}

// osReadFile is the real file reader for hostProbe.
func osReadFile(path string) ([]byte, error) { return os.ReadFile(path) }

// profileCtxTarget is the window this machine's profile pins, for callers that
// have no probe of their own.
func profileCtxTarget() int { return profileCtxSize(realHostProbe()) }
