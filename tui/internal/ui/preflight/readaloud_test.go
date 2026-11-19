// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"strings"
	"testing"
)

func TestReadAloudDefaultsWithoutAConfig(t *testing.T) {
	useConfig(t, "")
	got, err := ReadReadAloud()
	if err != nil {
		t.Fatal(err)
	}
	if got.Voice != "af_bella" || got.Speed != 100 || got.Auto {
		t.Fatalf("got %+v", got)
	}
}

func TestReadAloudNullVoiceIsTheDefault(t *testing.T) {
	useConfig(t, `{"read_aloud_voice": null, "read_aloud_speed": 100}`)
	got, err := ReadReadAloud()
	if err != nil || got.Voice != "af_bella" {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestReadAloudWritesEachKeyAndKeepsTheRest(t *testing.T) {
	path := useConfig(t, `{"default_model": "Gemma-4-E4B-it-GGUF", "full_access": false}`)
	if _, err := WriteReadAloudVoice("bm_george"); err != nil {
		t.Fatal(err)
	}
	if _, err := WriteReadAloudSpeed(150); err != nil {
		t.Fatal(err)
	}
	if _, err := WriteReadAloudAuto(true); err != nil {
		t.Fatal(err)
	}
	doc := readDoc(t, path)
	if doc["read_aloud_voice"] != "bm_george" || doc["read_aloud_speed"] != float64(150) || doc["read_aloud_auto"] != true {
		t.Fatalf("read-aloud keys not written: %v", doc)
	}
	if doc["default_model"] != "Gemma-4-E4B-it-GGUF" || doc["full_access"] != false {
		t.Fatalf("a write dropped another key: %v", doc)
	}
	got, err := ReadReadAloud()
	if err != nil {
		t.Fatal(err)
	}
	if got.Voice != "bm_george" || got.Speed != 150 || !got.Auto {
		t.Fatalf("read back %+v", got)
	}
}

func TestReadAloudRefusesValuesPythonRefuses(t *testing.T) {
	path := useConfig(t, `{"keep": 1}`)
	if _, err := WriteReadAloudVoice("xx_nobody"); err == nil || !strings.Contains(err.Error(), "af_bella") {
		t.Fatalf("an unknown voice was accepted or the error lists no voices: %v", err)
	}
	for _, speed := range []int{49, 201} {
		if _, err := WriteReadAloudSpeed(speed); err == nil || !strings.Contains(err.Error(), "50 to 200") {
			t.Fatalf("speed %d: %v", speed, err)
		}
	}
	if doc := readDoc(t, path); len(doc) != 1 {
		t.Fatalf("a refused value was written: %v", doc)
	}
}

func TestReadAloudBadSavedValuesAreErrorsNotDefaults(t *testing.T) {
	for _, body := range []string{
		`{"read_aloud_voice": "xx_nobody"}`,
		`{"read_aloud_speed": 500}`,
		`{"read_aloud_speed": "fast"}`,
		`{not json`,
	} {
		useConfig(t, body)
		if _, err := ReadReadAloud(); err == nil {
			t.Errorf("%s read without an error", body)
		}
	}
}
