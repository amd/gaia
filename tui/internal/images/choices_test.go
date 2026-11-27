// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package images

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// CatalogJSON is a slice of Lemonade 2026.44.1's catalog, the same rows
// tests/unit/chat/ui/test_image_settings.py checks the Python side with.
const CatalogJSON = `[
 {"id":"SDXL-Turbo","recipe":"sd-cpp","downloaded":false,"labels":["image"],"size":6.46,
  "image_defaults":{"width":512,"height":512,"steps":4,"cfg_scale":1.0,"soft_pixel_cap":1048576}},
 {"id":"Z-Image-Turbo","recipe":"sd-cpp","downloaded":true,"labels":["image"],"size":20.7,
  "image_defaults":{"width":1024,"height":1024,"steps":9,"cfg_scale":1.0,"soft_pixel_cap":2097152}},
 {"id":"Krea-2-Turbo","recipe":"thenoise","downloaded":false,"labels":["image"],"size":35.0,
  "image_defaults":{"width":1024,"height":1024,"steps":8,"soft_pixel_cap":4194304}},
 {"id":"RealESRGAN-x4plus","recipe":"sd-cpp","downloaded":true,"labels":["upscaling","image"],"image_defaults":null},
 {"id":"Gemma-4-E4B-it-GGUF","recipe":"llamacpp","downloaded":true,"labels":["vision"]}
]`

func catalog(t *testing.T) []lemonade.ImageModel {
	t.Helper()
	var out []lemonade.ImageModel
	if err := json.Unmarshal([]byte(CatalogJSON), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func byID(choices []Choice) map[string]Choice {
	out := map[string]Choice{}
	for _, c := range choices {
		out[c.ID] = c
	}
	return out
}

func TestChoicesAreDownloadedModelsAndLegacySDUpscalersExcluded(t *testing.T) {
	choices := Choices(catalog(t))
	var ids []string
	for _, c := range choices {
		ids = append(ids, c.ID)
	}
	// Krea is not downloaded and is no legacy model; an upscaler makes no image.
	if want := []string{"Z-Image-Turbo", "SDXL-Turbo"}; !reflect.DeepEqual(ids, want) {
		t.Fatalf("ids = %v, want %v", ids, want)
	}
	got := byID(choices)
	if !got["SDXL-Turbo"].DownloadsOnFirstUse || got["Z-Image-Turbo"].DownloadsOnFirstUse {
		t.Errorf("downloads on first use: SDXL=%v Z=%v", got["SDXL-Turbo"].DownloadsOnFirstUse, got["Z-Image-Turbo"].DownloadsOnFirstUse)
	}
}

func TestSizesStayUnderEachModelsPixelCap(t *testing.T) {
	got := byID(Choices(catalog(t)))
	z := got["Z-Image-Turbo"]
	if z.DefaultSize != "1024x1024" || z.DefaultSteps != 9 {
		t.Errorf("Z defaults = %s, %d", z.DefaultSize, z.DefaultSteps)
	}
	joined := " " + strings.Join(z.Sizes, " ") + " "
	if !strings.Contains(joined, " 1440x1440 ") || !strings.Contains(joined, " 1920x1080 ") {
		t.Errorf("Z sizes = %v", z.Sizes)
	}
	for _, s := range z.Sizes {
		w, h, err := ParseSize(s)
		if err != nil || w*h > 2097152 {
			t.Errorf("Z offers %s over its cap", s)
		}
	}
	// The SD client takes only these three for a legacy model.
	if want := []string{"512x512", "768x768", "1024x1024"}; !reflect.DeepEqual(got["SDXL-Turbo"].Sizes, want) {
		t.Errorf("SDXL sizes = %v", got["SDXL-Turbo"].Sizes)
	}
}

func TestADefaultSizeOutsideTheCandidatesIsStillOffered(t *testing.T) {
	models := []lemonade.ImageModel{{
		ID: "Odd", Recipe: "sd-cpp", Downloaded: true, Labels: []string{"image"},
		ImageDefaults: json.RawMessage(`{"width":640,"height":480,"steps":6}`),
	}}
	c := Choices(models)[0]
	if c.Sizes[0] != "640x480" || c.DefaultSteps != 6 {
		t.Errorf("choice = %+v", c)
	}
}

func intp(n int) *int       { return &n }
func strp(s string) *string { return &s }

func TestAChoiceLemonadeCannotMakeSaysWhy(t *testing.T) {
	choices := Choices(catalog(t))
	for _, tc := range []struct {
		model string
		size  *string
		steps *int
		named string
	}{
		{"Krea-2-Turbo", nil, nil, "not downloaded"},
		{"Z-Image-Turbo", strp("4096x4096"), nil, "4096x4096"},
		{"Z-Image-Turbo", strp("big"), nil, "WIDTHxHEIGHT"},
		{"Z-Image-Turbo", nil, intp(0), "1 to 100"},
	} {
		_, err := Check(choices, tc.model, tc.size, tc.steps)
		if err == nil || !strings.Contains(err.Error(), tc.named) {
			t.Errorf("Check(%s) = %v; want it to name %q", tc.model, err, tc.named)
		}
	}
	if _, err := Check(choices, "Z-Image-Turbo", strp("1280x720"), intp(12)); err != nil {
		t.Errorf("a valid choice was refused: %v", err)
	}
}

func TestParseSizeMatchesPython(t *testing.T) {
	for _, ok := range []string{"512x512", "1920x1080", " 1024x768 "} {
		if _, _, err := ParseSize(ok); err != nil {
			t.Errorf("%q refused: %v", ok, err)
		}
	}
	for _, bad := range []string{"0x512", "5x512", "512X512", "512x", "auto", "123456x2"} {
		if _, _, err := ParseSize(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}
