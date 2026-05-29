package main

import (
	"os"

	"github.com/amd/gaia/tui/internal/cli"
)

func main() {
	if err := cli.Execute(); err != nil {
		os.Exit(1)
	}
}
