package preflight

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Why a start command has to carry the context window.
//
// A bare `lemond` comes up HEALTHY — /api/v1/health answers 200 — and small
// requests even succeed, so nothing looks wrong until a real one arrives. On a
// 10.10.0 machine, across repeated runs, the model loaded at 25037-37888 tokens
// started bare and 32527-43227 with the profile's 65536 requested. A 40k-token
// request came back `context_length_exceeded` against the low end of both.
//
// That is the worst shape this package exists to prevent: the Local AI row goes
// green off the healthy server, hands the user into chat, and the first real
// request fails. A remedy that produces it is a wrong remedy, not merely an
// incomplete one.
//
// Two things those ranges say, and both are load-bearing:
//
//   - Asking for the window RAISES it. Every paired measurement put the
//     requested run above the bare one, so the prefix earns its place.
//   - It does not GUARANTEE it. The figure moved run to run and none of those
//     runs reached 65536, because llama.cpp clamps to the memory actually free at
//     load time. That is a load-moment effect, NOT a ceiling: other loads on the
//     same machine the same day did reach 65536, five of them. So nothing here
//     promises the target, and nothing here blames the hardware — the AI model
//     row reports the window the server really loaded, and says so when it falls
//     short (see markCtxShortfall).
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

// ctxSizeEnv is the variable modern Lemonade reads its context window from.
// Modern tooling has no `--ctx-size` flag; only the legacy CLI takes one.
const ctxSizeEnv = "LEMONADE_CTX_SIZE"

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
	// An explicit override wins: a user who set the variable has already chosen,
	// and telling them a different number would contradict their own environment.
	if raw := strings.TrimSpace(p.getenv(ctxSizeEnv)); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n > 0 {
			return n
		}
	}
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
	if file := strings.TrimSpace(p.getenv(configFileEnv)); file != "" {
		return file
	}
	if dir := strings.TrimSpace(p.getenv(configDirEnv)); dir != "" {
		return filepath.Join(dir, "config.json")
	}
	home, err := p.homeDir()
	if err != nil || home == "" {
		return ""
	}
	return filepath.Join(home, ".gaia", "config.json")
}

// --- attaching it to a command ----------------------------------------------

// ctxPrefix renders the assignment for a command this user runs THEMSELVES, in
// the syntax of the shell they will run it in.
//
// It is only ever attached to a launch whose environment the command controls. A
// service or app launch (systemd, launchd, the macOS app, the Windows tray) takes
// its environment from its own unit or bundle, so prefixing one of those would
// look like it did something and change nothing at all.
func ctxPrefix(goos string, ctx int) string {
	if goos == "windows" {
		// cmd.exe, and the QUOTES matter: `set VAR=1 && cmd` assigns "1 " —
		// everything up to the ampersand, trailing space included — which the
		// server then fails to parse. `set "VAR=1"` bounds the value.
		//
		// PowerShell wants `$env:VAR=...;` instead, and no single string is right
		// in both; same tradeoff as the quoted exe path, and the tray app remains
		// the shell-free alternative.
		return fmt.Sprintf(`set "%s=%d" && `, ctxSizeEnv, ctx)
	}
	// POSIX: `env VAR=value command` runs in any shell, unlike `VAR=value command`
	// which is a shell builtin form.
	return fmt.Sprintf("env %s=%d ", ctxSizeEnv, ctx)
}

// legacyCtxFlag is how the PRE-10.7 CLI took the same setting: a flag, not an
// environment variable (lemonade_launcher.build_start_command's legacy branch).
func legacyCtxFlag(ctx int) string {
	return fmt.Sprintf(" --ctx-size %d", ctx)
}

// osReadFile is the real file reader for hostProbe.
func osReadFile(path string) ([]byte, error) { return os.ReadFile(path) }

// profileCtxTarget is the window this machine's profile pins, for callers that
// have no probe of their own.
func profileCtxTarget() int { return profileCtxSize(realHostProbe()) }
