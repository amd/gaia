// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
)

// ImageModel is one catalog row as an image picker needs it. ImageDefaults
// stays raw: Lemonade sends null for upscalers, and one odd row must not
// fail the whole listing.
type ImageModel struct {
	ID            string          `json:"id"`
	Recipe        string          `json:"recipe"`
	Downloaded    bool            `json:"downloaded"`
	Labels        []string        `json:"labels"`
	Size          float64         `json:"size"`
	ImageDefaults json.RawMessage `json:"image_defaults"`
}

// ImageCatalog lists every model Lemonade knows, downloaded or not, for
// picking an image model. Read-only, so a remote Lemonade is fine.
func (c *Client) ImageCatalog(ctx context.Context) ([]ImageModel, error) {
	var reply struct {
		Data []ImageModel `json:"data"`
	}
	if err := c.get(ctx, "/models?show_all=true", &reply); err != nil {
		return nil, err
	}
	return reply.Data, nil
}

// Address is where c's requests go, for naming it in a message.
func (c *Client) Address() string { return c.current() }
