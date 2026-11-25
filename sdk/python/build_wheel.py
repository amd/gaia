# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build a gaia-sdk wheel that carries the gaiacpp engine for one platform.

    python build_wheel.py --engine path/to/gaiacpp --out dist

The engine is a native binary, so the wheel is tagged for the platform it was
built on (`py3-none-win_amd64`, not `py3-none-any`): pip then refuses to
install it where the binary cannot run.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE / "gaia_sdk" / "bin"
ANY = "py3-none-any"


def platform_tag() -> str:
    return sysconfig.get_platform().replace("-", "_").replace(".", "_")


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"{name},sha256={digest.decode()},{len(data)}"


def retag(wheel: Path, tag: str) -> Path:
    """Rewrite a pure wheel as a platform wheel: its name, WHEEL and RECORD."""
    if ANY not in wheel.name:
        raise SystemExit(f"{wheel.name} is not tagged {ANY}; nothing to retag.")
    target = wheel.with_name(wheel.name.replace(ANY, f"py3-none-{tag}"))
    with zipfile.ZipFile(wheel) as source:
        files = {info.filename: (info, source.read(info)) for info in source.infolist()}
    wheel_name = next(n for n in files if n.endswith(".dist-info/WHEEL"))
    record_name = next(n for n in files if n.endswith(".dist-info/RECORD"))
    info, data = files[wheel_name]
    text = data.decode("utf-8").replace(f"Tag: {ANY}", f"Tag: py3-none-{tag}")
    text = text.replace("Root-Is-Purelib: true", "Root-Is-Purelib: false")
    files[wheel_name] = (info, text.encode("utf-8"))
    lines = [
        _record_line(name, content)
        for name, (_, content) in files.items()
        if name != record_name
    ]
    lines.append(f"{record_name},,")
    files[record_name] = (files[record_name][0], ("\n".join(lines) + "\n").encode())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for name, (info, content) in files.items():
            out.writestr(info, content)
    wheel.unlink()
    return target


def build(engine: Path, out: Path, tag: str) -> Path:
    if not engine.is_file():
        raise SystemExit(f"--engine {engine} is not a file. Build gaiacpp first.")
    name = "gaiacpp.exe" if engine.suffix.lower() == ".exe" else "gaiacpp"
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("gaia_sdk-*.whl"):
        stale.unlink()
    BIN.mkdir(exist_ok=True)
    packaged = BIN / name
    shutil.copy2(engine, packaged)
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                str(HERE),
                "--no-deps",
                "-w",
                str(out),
            ],
            check=True,
            timeout=600,
        )
    finally:
        # The source tree stays binary-free; only the wheel carries the engine.
        packaged.unlink()
        if not any(BIN.iterdir()):
            BIN.rmdir()
    (built,) = out.glob("gaia_sdk-*.whl")
    with zipfile.ZipFile(built) as check:
        if f"gaia_sdk/bin/{name}" not in check.namelist():
            raise SystemExit(f"{built.name} was built without gaia_sdk/bin/{name}.")
    return retag(built, tag)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--engine", required=True, type=Path, help="the built gaiacpp binary"
    )
    parser.add_argument(
        "--out", default=HERE / "dist", type=Path, help="where the wheel goes"
    )
    parser.add_argument(
        "--platform-tag",
        default=platform_tag(),
        help="wheel platform tag (default: this machine's)",
    )
    args = parser.parse_args()
    print(build(args.engine.resolve(), args.out.resolve(), args.platform_tag))


if __name__ == "__main__":
    main()
