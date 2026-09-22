"""Toybox CLI: parses args, validates them, formats output — all in one place."""
import argparse
import sys
from toybox.config import load_config


def main(argv=None):
    p = argparse.ArgumentParser(prog="toybox")
    p.add_argument("--config", required=True)
    p.add_argument("--limit", type=int, default=10)
    args = p.parse_args(argv)
    if args.limit < 1:
        print("limit must be positive", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    for k in sorted(cfg):
        print(f"{k} = {cfg[k]}")
    return 0
