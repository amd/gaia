# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The baseline semantic search has to beat: ``git grep`` for the issue's identifiers.

This is what a developer (or an agent without an index) does first: pull the
code-looking words out of the issue and grep for them. Each identifier is
weighted by how rare it is across the indexed files (inverse document
frequency); a file scores the sum of ``idf * (1 + log(hits))`` over the
identifiers it contains, and a file whose path the issue names outright gets
the largest weight on top. Symbols are ranked the same way from the hits
inside them. Only files the code index holds are searched, so both retrievers
choose from the same corpus.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from gaia.eval.code_retrieval.gold import Symbol, enclosing, innermost, python_symbols
from gaia.eval.code_retrieval.repos import GIT_TIMEOUT_S, RepoError

#: Words that look like identifiers in prose but carry nothing.
_STOP = frozenset("""
    the and for are but not you all any can had her was one our out has him his how
    its may new now old see two way who did get let put say she too use that with
    this from they will would there their what about which when make like time just
    know take into year your good some could them than then look only come over
    think also back after work first well even want because these give most none
    true false self cls def class return import from none pass raise yield lambda
    while with try except finally assert global async await elif else print len
    str int float dict list set tuple bool object type range open file line lines
    error errors issue bug value values code test tests example expected actual
    result results should does doesn't using used case python version
    """.split())

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_BACKTICK = re.compile(r"`([^`\n]+)`")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_PATH = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:py|pyw|js|ts|go|rs|java|c|h|cpp|hpp|rb)\b")
#: An identifier in more than this share of files says nothing about which file.
MAX_DF_SHARE = 0.25
MAX_IDENTIFIERS = 40


def _looks_like_code(token: str) -> bool:
    return (
        "_" in token.strip("_")
        or any(c.isdigit() for c in token)
        or bool(re.search(r"[a-z][A-Z]", token))
        or bool(re.match(r"^[A-Z][a-z0-9]+[A-Z]", token))
    )


def identifiers(text: str) -> List[str]:
    """Code-looking tokens of *text*, first-seen order, at most :data:`MAX_IDENTIFIERS`."""
    code_spans = _FENCE.findall(text) + _BACKTICK.findall(text)
    found: List[str] = []

    def add(token: str) -> None:
        if len(token) >= 3 and token.lower() not in _STOP and token not in found:
            found.append(token)

    for span in code_spans:
        for tok in _IDENT.findall(span):
            add(tok)
    for dotted in _DOTTED.findall(text):
        if re.search(r"\.(?:py|txt|md|rst|html|com|org)$", dotted):
            continue
        for tok in dotted.split("."):
            add(tok)
    for tok in _IDENT.findall(text):
        if _looks_like_code(tok):
            add(tok)
    return found[:MAX_IDENTIFIERS]


def mentioned_paths(text: str) -> List[str]:
    """Source paths the issue names, slash-normalized (tracebacks, prose)."""
    out = []
    for raw in _PATH.findall(text):
        path = raw.replace("\\", "/").lstrip("./")
        if path not in out:
            out.append(path)
    return out


def _grep(workdir: Path, ident: str) -> Dict[str, List[int]]:
    """``path -> [line numbers]`` of whole-word hits of *ident* in tracked files."""
    exe = shutil.which("git")
    if not exe:
        raise RepoError("The lexical baseline needs git on PATH.")
    proc = subprocess.run(
        [exe, "grep", "-n", "-w", "-F", "-I", "--no-color", "-z", "-e", ident],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode == 1:  # no match
        return {}
    if proc.returncode != 0:
        raise RepoError(
            f"`git grep {ident}` failed in {workdir}: {proc.stderr.strip()[-500:]}"
        )
    hits: Dict[str, List[int]] = {}
    for record in proc.stdout.splitlines():
        parts = record.split("\0")
        if len(parts) < 3:
            continue
        hits.setdefault(parts[0], []).append(int(parts[1]))
    return hits


class LexicalSearch:
    """``git grep`` ranking over one working tree, restricted to *corpus*."""

    def __init__(self, workdir: Path, corpus: Iterable[str]):
        self.workdir = workdir
        self.corpus: Set[str] = set(corpus)
        self._symbols: Dict[str, List[Symbol]] = {}

    def symbols_of(self, path: str) -> List[Symbol]:
        if path not in self._symbols:
            text = None
            if path.endswith((".py", ".pyw")):
                full = self.workdir / path
                if full.is_file():
                    text = full.read_text(encoding="utf-8", errors="replace")
            self._symbols[path] = python_symbols(path, text) if text else []
        return self._symbols[path]

    def rank(self, text: str) -> Tuple[List[str], List[Tuple[str, str]], List[str]]:
        """Ranked files, ranked ``(path, symbol)`` pairs, and the identifiers used."""
        n_files = max(len(self.corpus), 1)
        file_score: Dict[str, float] = {}
        sym_score: Dict[Tuple[str, str], float] = {}
        used: List[str] = []
        idfs: List[float] = []
        for ident in identifiers(text):
            hits = {
                p: v for p, v in _grep(self.workdir, ident).items() if p in self.corpus
            }
            if not hits or len(hits) / n_files > MAX_DF_SHARE:
                continue
            used.append(ident)
            idf = math.log(n_files / len(hits))
            idfs.append(idf)
            for path, lines in hits.items():
                file_score[path] = file_score.get(path, 0.0) + idf * (
                    1 + math.log(len(lines))
                )
                per_sym: Dict[Tuple[str, str], int] = {}
                syms = self.symbols_of(path)
                for ln in lines:
                    sym = innermost(syms, ln)
                    if sym is not None:
                        key = (path, sym.qualname)
                        per_sym[key] = per_sym.get(key, 0) + 1
                for key, n in per_sym.items():
                    sym_score[key] = sym_score.get(key, 0.0) + idf * (1 + math.log(n))
        bonus = 2 * max(idfs, default=math.log(n_files))
        for named in mentioned_paths(text):
            for path in self.corpus:
                # Either side may be the longer: a traceback names an absolute
                # path, prose may name just "sessions.py".
                if (
                    path == named
                    or path.endswith("/" + named)
                    or named.endswith("/" + path)
                ):
                    file_score[path] = file_score.get(path, 0.0) + bonus
        files = sorted(file_score, key=lambda p: (-file_score[p], p))
        symbols = sorted(sym_score, key=lambda k: (-sym_score[k], k))
        return files, symbols, used

    def chunk_symbol(
        self, path: str, name: Optional[str], start_line: int
    ) -> Optional[Tuple[str, str]]:
        """The ``(path, qualname)`` a code-index chunk belongs to, or None.

        Chunks carry the bare name (``__init__``); the innermost same-named
        symbol around the chunk's first line disambiguates it. Split parts
        ("f (part 2/3)") start inside their symbol, so they resolve to it too.
        """
        if not name:
            return None
        bare = re.sub(r" \(part \d+/\d+\)$", "", name)
        for sym in enclosing(self.symbols_of(path), start_line):
            if sym.name == bare:
                return (path, sym.qualname)
        return (path, bare)
