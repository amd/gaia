"""Convert a temperature between units.

Usage: python convert_units.py VALUE --from {c,f,k} --to {c,f,k}
"""

import argparse

_TO_C = {"c": lambda v: v, "f": lambda v: (v - 32) * 5 / 9, "k": lambda v: v - 273.15}
_FROM_C = {"c": lambda v: v, "f": lambda v: v * 9 / 5 + 32, "k": lambda v: v + 273.15}


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a temperature.")
    parser.add_argument("value", type=float)
    parser.add_argument("--from", dest="src", required=True, choices=sorted(_TO_C))
    parser.add_argument("--to", dest="dst", required=True, choices=sorted(_FROM_C))
    args = parser.parse_args()
    print(f"{_FROM_C[args.dst](_TO_C[args.src](args.value)):.1f}")


if __name__ == "__main__":
    main()
