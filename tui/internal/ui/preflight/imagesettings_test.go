// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"
)

func TestImageSettingsDefaultWithNoConfig(t *testing.T) {
	useConfig(t, "")
	got, err := ReadImageSettings()
	if err != nil || got.Model != "" || got.Size != nil || got.Steps != nil {
		t.Fatalf("got %+v, %v; want the defaults", got, err)
	}
}

func TestImageSettingsRoundTripKeepingOtherKeys(t *testing.T) {
	path := useConfig(t, `{"profile":"chat","image_size":"1440x1440"}`)
	size, steps := "1280x720", 12
	if _, err := WriteImageSettings("Z-Image-Turbo", &size, &steps); err != nil {
		t.Fatal(err)
	}
	doc := readDoc(t, path)
	if doc["profile"] != "chat" || doc["image_model"] != "Z-Image-Turbo" ||
		doc["image_size"] != "1280x720" || doc["image_steps"] != float64(12) {
		t.Fatalf("saved %v", doc)
	}
	got, err := ReadImageSettings()
	if err != nil || got.Model != "Z-Image-Turbo" || *got.Size != "1280x720" || *got.Steps != 12 {
		t.Fatalf("read back %+v, %v", got, err)
	}
}

func TestImageSettingsNilSavesNull(t *testing.T) {
	path := useConfig(t, `{"image_size":"1440x1440","image_steps":20}`)
	if _, err := WriteImageSettings("SDXL-Turbo", nil, nil); err != nil {
		t.Fatal(err)
	}
	doc := readDoc(t, path)
	for _, k := range []string{"image_size", "image_steps"} {
		v, ok := doc[k]
		if !ok || v != nil {
			t.Errorf("%s = %v (present %v); want null", k, v, ok)
		}
	}
}

func TestImageSettingsOfTheWrongTypeAreAnError(t *testing.T) {
	useConfig(t, `{"image_steps":"lots"}`)
	if _, err := ReadImageSettings(); err == nil || !strings.Contains(err.Error(), "config.json") {
		t.Fatalf("err = %v; want one naming the file", err)
	}
}
