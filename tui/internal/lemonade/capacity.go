// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// Capacity is what this machine can hold, read from Lemonade's /system-info so
// the answer matches the server that will load the model. Mirrors
// gaia.llm.model_fit.capacity_from_system_info.
type Capacity struct {
	MemoryGB     float64
	MemorySource string
	// DiskFreeGB is negative when Lemonade did not report its model store.
	DiskFreeGB float64
	// ServerVersion is Lemonade's own version, "" when it did not say.
	ServerVersion string
	// NPUMemoryGB is the system RAM an FLM model loads into: installed RAM
	// less an APU's GPU carve-out. 0 when Lemonade reports no available NPU.
	NPUMemoryGB float64
}

type gpuInfo struct {
	// Available is a pointer because an entry with no flag counts as available,
	// the way gaia.llm.lemonade_manager reads it.
	Available  *bool   `json:"available"`
	Integrated bool    `json:"integrated"`
	Name       string  `json:"name"`
	VRAMGB     float64 `json:"vram_gb"`
	VirtualGB  float64 `json:"virtual_mem_gb"`
}

func (g gpuInfo) available() bool { return g.Available == nil || *g.Available }

// gpuList accepts both an array and a single object, as Lemonade versions differ.
type gpuList []gpuInfo

func (g *gpuList) UnmarshalJSON(b []byte) error {
	var many []gpuInfo
	if err := json.Unmarshal(b, &many); err == nil {
		*g = many
		return nil
	}
	var one gpuInfo
	if err := json.Unmarshal(b, &one); err != nil {
		return err
	}
	*g = gpuList{one}
	return nil
}

type systemInfo struct {
	PhysicalMemory string `json:"Physical Memory"`
	Devices        struct {
		AMD     gpuList `json:"amd_gpu"`
		AMDIGPU gpuList `json:"amd_igpu"`
		AMDDGPU gpuList `json:"amd_dgpu"`
		NVIDIA  gpuList `json:"nvidia_gpu"`
		Metal   gpuList `json:"metal"`
		NPU     *struct {
			Available bool `json:"available"`
		} `json:"amd_npu"`
	} `json:"devices"`
	Storage *struct {
		FreeBytes *float64 `json:"free_bytes"`
	} `json:"model_storage"`
}

var physicalGB = regexp.MustCompile(`([\d.]+)\s*GB`)

// physicalMemoryGB is Lemonade's "Physical Memory" in GB, or 0 when absent.
func physicalMemoryGB(raw string) float64 {
	if m := physicalGB.FindStringSubmatch(raw); m != nil {
		if gb, err := strconv.ParseFloat(m[1], 64); err == nil && gb > 0 {
			return gb
		}
	}
	return 0
}

// windowsSharedGB is the system RAM Windows lets an integrated GPU use beyond
// its carve-out: half of the RAM Windows sees. Lemonade's "Physical Memory" is
// installed RAM, which still counts the carve-out. Mirrors
// gaia.llm.model_fit._windows_shared_gb.
func windowsSharedGB(physicalGB, carveOutGB float64) float64 {
	return math.Max(0, physicalGB-carveOutGB) * fitRule.WindowsIGPUSharedRAMFraction
}

// formatGB mirrors gaia.llm.model_fit.format_gb: under 1 GB keeps a decimal so
// 0.5 never reads as 0.
func formatGB(gb float64) string {
	if gb > 0 && gb < 1 {
		return strconv.FormatFloat(gb, 'f', 1, 64)
	}
	return strconv.FormatFloat(gb, 'f', 0, 64)
}

type gpuEntry struct {
	vendor     string
	integrated bool
	gpuInfo
}

// capacityFrom mirrors gaia.llm.model_fit.capacity_from_system_info, including
// its refusal to judge a PC whose GPU is reported without its memory.
func capacityFrom(info systemInfo) (Capacity, error) {
	c, err := gpuCapacityFrom(info)
	c.NPUMemoryGB = npuMemoryGB(info)
	return c, err
}

// npuMemoryGB mirrors gaia.llm.model_fit._npu_memory_gb.
func npuMemoryGB(info systemInfo) float64 {
	physical := physicalMemoryGB(info.PhysicalMemory)
	if info.Devices.NPU == nil || !info.Devices.NPU.Available || physical <= 0 {
		return 0
	}
	carveOut := 0.0
	for _, src := range []struct {
		integrated bool
		list       gpuList
	}{{false, info.Devices.AMD}, {true, info.Devices.AMDIGPU}, {false, info.Devices.AMDDGPU}} {
		for _, g := range src.list {
			if g.available() && (src.integrated || g.Integrated) {
				if carveOut = g.VRAMGB; carveOut <= 0 {
					carveOut = adapterMemoryGB(g.Name)
				}
				return math.Max(0, physical-carveOut)
			}
		}
	}
	return physical
}

// NPU is c as an FLM model sees it: the NPU's system RAM as the memory pool.
// ok is false on a PC with no NPU. Mirrors gaia.llm.model_fit.npu_capacity.
func (c Capacity) NPU() (npu Capacity, ok bool) {
	if c.NPUMemoryGB <= 0 {
		return c, false
	}
	c.MemoryGB, c.MemorySource = c.NPUMemoryGB, "NPU, system RAM"
	return c, true
}

func gpuCapacityFrom(info systemInfo) (Capacity, error) {
	c := Capacity{DiskFreeGB: -1}
	if info.Storage != nil && info.Storage.FreeBytes != nil {
		c.DiskFreeGB = *info.Storage.FreeBytes / 1e9
	}
	d := info.Devices
	physical := physicalMemoryGB(info.PhysicalMemory)
	var gpus []gpuEntry
	for _, src := range []struct {
		vendor     string
		integrated bool
		list       gpuList
	}{{"AMD", false, d.AMD}, {"AMD", true, d.AMDIGPU}, {"AMD", false, d.AMDDGPU}, {"NVIDIA", false, d.NVIDIA}, {"Apple", false, d.Metal}} {
		for _, g := range src.list {
			if g.available() {
				gpus = append(gpus, gpuEntry{src.vendor, src.integrated || g.Integrated, g})
			}
		}
	}
	for _, g := range gpus {
		if g.vendor != "AMD" || !g.integrated {
			continue
		}
		if g.VRAMGB > 0 {
			c.MemoryGB, c.MemorySource = g.VRAMGB+g.VirtualGB, "AMD iGPU"
			return c, nil
		}
		if carveOut := adapterMemoryGB(g.Name); carveOut > 0 {
			if physical <= 0 {
				return c, fmt.Errorf("Lemonade reports an AMD integrated GPU (%s) but not this PC's 'Physical Memory', and the GPU's BIOS carve-out alone is not the memory it loads into, so GAIA cannot tell which models fit. Update Lemonade and retry, or choose the model yourself with `gaia config set default_model <model>`", g.Name)
			}
			c.MemoryGB, c.MemorySource = carveOut+windowsSharedGB(physical, carveOut), "AMD iGPU"
			return c, nil
		}
	}
	for _, g := range gpus {
		if g.VRAMGB > 0 {
			c.MemoryGB, c.MemorySource = g.VRAMGB, g.vendor+" GPU"
			return c, nil
		}
	}
	if len(gpus) > 0 {
		var names []string
		for _, g := range gpus {
			if g.Name != "" {
				names = append(names, g.Name)
			} else {
				names = append(names, g.vendor)
			}
		}
		return c, fmt.Errorf("Lemonade reports a GPU (%s) but not its memory, so GAIA cannot tell which models fit. Update Lemonade and retry", strings.Join(names, ", "))
	}
	if physical > 0 {
		c.MemoryGB, c.MemorySource = physical, "System RAM"
		return c, nil
	}
	return c, fmt.Errorf("Lemonade reported neither GPU memory nor system memory, so GAIA cannot tell which models fit this PC. Update Lemonade and retry")
}

// Capacity asks Lemonade what this machine can hold, and which Lemonade it is.
func (c *Client) Capacity(ctx context.Context) (Capacity, error) {
	var info systemInfo
	if err := c.request(ctx, "GET", "/system-info", nil, &info); err != nil {
		return Capacity{}, err
	}
	capacity, err := capacityFrom(info)
	if err != nil {
		return capacity, err
	}
	var health struct {
		Version string `json:"version"`
	}
	if err := c.request(ctx, "GET", "/health", nil, &health); err != nil {
		return capacity, err
	}
	capacity.ServerVersion = health.Version
	return capacity, nil
}

// versionAtLeast compares Lemonade versions by the leading digits of each part,
// as gaia.llm.model_fit.version_tuple does ("2026.39.0~12.abc" reads 2026.39.0).
func versionAtLeast(have, want string) bool {
	parse := func(v string) []int {
		var out []int
		for _, part := range strings.SplitN(strings.TrimPrefix(strings.TrimSpace(v), "v"), ".", 3) {
			n, digits := 0, 0
			for _, r := range part {
				if r < '0' || r > '9' {
					break
				}
				n, digits = n*10+int(r-'0'), digits+1
			}
			if digits == 0 {
				break
			}
			out = append(out, n)
		}
		return out
	}
	h, w := parse(have), parse(want)
	if len(h) == 0 {
		return false
	}
	for i := 0; i < len(w); i++ {
		hv := 0
		if i < len(h) {
			hv = h[i]
		}
		if hv != w[i] {
			return hv > w[i]
		}
	}
	return true
}

// SupportsModel mirrors gaia.llm.model_fit.check_server_supports: an unknown
// server version cannot show support.
func (c Capacity) SupportsModel(minVersion string) (bool, string) {
	if minVersion == "" || versionAtLeast(c.ServerVersion, minVersion) {
		return true, ""
	}
	running := "this server's version is unknown"
	if c.ServerVersion != "" {
		running = "this server is v" + c.ServerVersion
	}
	return false, fmt.Sprintf("needs Lemonade v%s or newer (%s); upgrade it with `gaia init --force-reinstall`", minVersion, running)
}

// RequiredMemoryGB is the memory a model of sizeGB weights needs to run.
// kvCacheGB is its KV cache at its floor window when larger than the shared
// margin allows for (0 otherwise).
func RequiredMemoryGB(sizeGB, kvCacheGB float64) float64 {
	return sizeGB*fitRule.MemoryOverheadFactor + fitRule.MemoryOverheadGB + kvCacheGB
}

// Fit reports whether a local model of sizeGB fits, and if not why — in the
// same words as gaia.llm.model_fit.check_fit.
func (c Capacity) Fit(sizeGB, kvCacheGB float64) (bool, string) {
	if need := RequiredMemoryGB(sizeGB, kvCacheGB); need > c.MemoryGB {
		return false, fmt.Sprintf("needs ~%.0f GB of memory; this PC has %s GB (%s)", need, formatGB(c.MemoryGB), c.MemorySource)
	}
	if c.DiskFreeGB >= 0 && sizeGB > c.DiskFreeGB {
		return false, fmt.Sprintf("needs %.0f GB of disk; %.0f GB free", sizeGB, c.DiskFreeGB)
	}
	return true, ""
}
