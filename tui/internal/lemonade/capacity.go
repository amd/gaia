// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
)

// Capacity is what this machine can hold, read from Lemonade's /system-info so
// the answer matches the server that will load the model. Mirrors
// gaia.llm.model_fit.capacity_from_system_info.
type Capacity struct {
	MemoryGB     float64
	MemorySource string
	// DiskFreeGB is negative when Lemonade did not report its model store.
	DiskFreeGB float64
}

type gpuInfo struct {
	Available  bool    `json:"available"`
	Integrated bool    `json:"integrated"`
	VRAMGB     float64 `json:"vram_gb"`
	VirtualGB  float64 `json:"virtual_mem_gb"`
}

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
		AMD    gpuList `json:"amd_gpu"`
		NVIDIA gpuList `json:"nvidia_gpu"`
		Metal  gpuInfo `json:"metal"`
	} `json:"devices"`
	Storage *struct {
		FreeBytes *float64 `json:"free_bytes"`
	} `json:"model_storage"`
}

var physicalGB = regexp.MustCompile(`([\d.]+)\s*GB`)

func capacityFrom(info systemInfo) (Capacity, error) {
	c := Capacity{DiskFreeGB: -1}
	if info.Storage != nil && info.Storage.FreeBytes != nil {
		c.DiskFreeGB = *info.Storage.FreeBytes / 1e9
	}
	for _, g := range info.Devices.AMD {
		if g.Available && g.Integrated && g.VRAMGB > 0 {
			c.MemoryGB, c.MemorySource = g.VRAMGB+g.VirtualGB, "AMD iGPU"
			return c, nil
		}
	}
	for _, g := range info.Devices.AMD {
		if g.Available && g.VRAMGB > 0 {
			c.MemoryGB, c.MemorySource = g.VRAMGB, "AMD GPU"
			return c, nil
		}
	}
	for _, g := range info.Devices.NVIDIA {
		if g.Available && g.VRAMGB > 0 {
			c.MemoryGB, c.MemorySource = g.VRAMGB, "NVIDIA GPU"
			return c, nil
		}
	}
	if info.Devices.Metal.Available && info.Devices.Metal.VRAMGB > 0 {
		c.MemoryGB, c.MemorySource = info.Devices.Metal.VRAMGB, "Apple GPU"
		return c, nil
	}
	if m := physicalGB.FindStringSubmatch(info.PhysicalMemory); m != nil {
		if gb, err := strconv.ParseFloat(m[1], 64); err == nil && gb > 0 {
			c.MemoryGB, c.MemorySource = gb, "System RAM"
			return c, nil
		}
	}
	return c, fmt.Errorf("Lemonade reported neither GPU memory nor system memory, so GAIA cannot tell which models fit this PC. Update Lemonade and retry")
}

// Capacity asks Lemonade what this machine can hold.
func (c *Client) Capacity(ctx context.Context) (Capacity, error) {
	var info systemInfo
	if err := c.request(ctx, "GET", "/system-info", nil, &info); err != nil {
		return Capacity{}, err
	}
	return capacityFrom(info)
}

// RequiredMemoryGB is the memory a model of sizeGB weights needs to run.
func RequiredMemoryGB(sizeGB float64) float64 {
	return sizeGB*fitRule.MemoryOverheadFactor + fitRule.MemoryOverheadGB
}

// Fit reports whether a local model of sizeGB fits, and if not why — in the
// same words as gaia.llm.model_fit.check_fit.
func (c Capacity) Fit(sizeGB float64) (bool, string) {
	if need := RequiredMemoryGB(sizeGB); need > c.MemoryGB {
		return false, fmt.Sprintf("needs ~%.0f GB of memory; this PC has %.0f GB (%s)", need, c.MemoryGB, c.MemorySource)
	}
	if c.DiskFreeGB >= 0 && sizeGB > c.DiskFreeGB {
		return false, fmt.Sprintf("needs %.0f GB of disk; %.0f GB free", sizeGB, c.DiskFreeGB)
	}
	return true, ""
}
