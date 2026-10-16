// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"strings"
	"testing"
)

func parseResumeFlag(t *testing.T, argv ...string) (string, error) {
	t.Helper()
	resumeArg = ""
	t.Cleanup(func() {
		resumeArg = ""
		_ = rootCmd.Flags().Set("resume", "")
	})
	err := rootCmd.Flags().Parse(argv)
	return resumeArg, err
}

func TestResumeFlagForms(t *testing.T) {
	for _, tc := range []struct {
		argv []string
		want string
	}{
		{nil, ""},
		{[]string{"--resume"}, resumePick},
		{[]string{"--resume=ab12cd"}, "ab12cd"},
	} {
		got, err := parseResumeFlag(t, tc.argv...)
		if err != nil || got != tc.want {
			t.Errorf("%v → %q, %v; want %q", tc.argv, got, err, tc.want)
		}
	}
}

// pflag never attaches a spaced value to a flag with a bare form, so
// `--resume ab12cd` would open the list and drop the id the user typed.
func TestResumeWithASpacedIDNamesTheFix(t *testing.T) {
	if _, err := parseResumeFlag(t, "--resume"); err != nil {
		t.Fatal(err)
	}
	err := rootCmd.Args(rootCmd, []string{"ab12cd"})
	if err == nil || !strings.Contains(err.Error(), "--resume=ab12cd") {
		t.Fatalf("err = %v, want the attached form named", err)
	}
}
