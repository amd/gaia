# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SWE-bench Verified: real issues from public repositories, graded by the official harness.

Nothing of the dataset is committed here. At run time:

- :func:`load_instances` fetches each instance from the Hugging Face dataset
  ``SWE-bench/SWE-bench_Verified`` (the ``datasets`` package when it is
  installed, the datasets-server REST API otherwise) and caches it as JSON
  under the work root, which ``--fence`` keeps from the agent.
- :func:`checkout` gives the agent the repository at ``base_commit`` the way
  :mod:`therock` does: a bare cache per repository holds only history up to
  the base, fetched by commit, and each task gets a single-branch clone of
  depth 200 over ``file://`` (git ignores ``--depth`` on a plain local path),
  detached, with no remote. ``git fsck`` confirms the clone holds nothing off
  that history.
- :func:`tool_interpreter` is the Python either harness's agent runs:
  a bare venv, so a newer installed release of the package under test (fix
  included) is not importable. Under ``--fence`` the installed copies are also
  denied (:func:`host_site_packages`, :func:`deniable_copy`).
- :func:`capture_prediction` turns what the agent changed into one row of the
  predictions file the official harness reads.
- :func:`evaluate` runs ``swebench.harness.run_evaluation`` one instance at a
  time in Docker. The images are amd64-only and about 3 GB each, so each is
  pulled just before its run and removed right after.
- :func:`parse_report` reads the per-instance ``report.json`` the harness
  writes: resolved or not, and the FAIL_TO_PASS / PASS_TO_PASS counts.

The gold patch is the judge's reference, read only in the judge step.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import random
import re
import shutil
import site
import subprocess
import sys
import sysconfig
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode

from gaia.eval.bench.therock import HISTORY_DEPTH
from gaia.logger import get_logger

log = get_logger(__name__)

SUITE = "swebench"
CHECK = "swebench"
DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"
DATASET_ROWS_URL = "https://datasets-server.huggingface.co/rows"
#: The datasets-server pages at most 100 rows at a time.
ROWS_PER_PAGE = 100
#: Kept from each dataset row; the rest (hints, eval script, test patch) is not.
COLUMNS = (
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "image",
    "version",
    "patch",
)
#: The pilot: five instances from four repositories, small enough to run on
#: one machine in an evening.
PILOT = (
    "psf__requests-1921",
    "psf__requests-5414",
    "pallets__flask-5014",
    "pylint-dev__pylint-4970",
    "sphinx-doc__sphinx-8721",
)
MAX_STEPS = 120
#: Appended to every problem statement. The agent cannot run the project's
#: tests: they run later, in the instance's own container.
PROMPT_SUFFIX = (
    "Work only in this checkout. Make the code change that resolves the issue "
    "described above. Do not use the internet. You cannot run this project's "
    "test suite here (it is evaluated separately in a container), so reason "
    "carefully from the code and keep the change minimal."
)
DOCKER_PLATFORM = "linux/amd64"
#: The official harness's per-instance timeout, in seconds.
HARNESS_TIMEOUT_S = 1800
#: Identifies the evaluation in the harness's ``logs/run_evaluation/<run_id>``.
RUN_ID = "gaia-bench"
#: The harness version the Linux grader container pins (Windows hosts only).
GRADER_SWEBENCH = "5.0.2"
GRADER_IMAGE = f"gaia-swebench-grader:{GRADER_SWEBENCH}"
GRADER_DOCKERFILE = f"""FROM python:3.11-slim
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker
RUN pip install --no-cache-dir swebench=={GRADER_SWEBENCH}
"""
GRADER_BUILD_TIMEOUT_S = 1800
GIT_TIMEOUT_S = 900
FETCH_TIMEOUT_S = 120
DOCKER_PULL_TIMEOUT_S = 3600

Fetcher = Callable[[Sequence[str]], Dict[str, Dict[str, Any]]]


class SweBenchError(RuntimeError):
    """An instance could not be fetched, checked out, submitted or graded."""


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------


def cache_dir(work_root: Path) -> Path:
    return work_root / "swebench"


def _normalize(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One dataset row, cut to :data:`COLUMNS`; test lists are lists, not JSON text."""
    missing = [c for c in COLUMNS if c not in row]
    if missing:
        raise SweBenchError(
            f"dataset row {row.get('instance_id')!r} lacks {missing}. The harness "
            f"needs {DATASET} (with an 'image' column), not the older princeton-nlp "
            "dataset."
        )
    inst = {c: row[c] for c in COLUMNS}
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        if isinstance(inst[key], str):
            inst[key] = json.loads(inst[key])
    return inst


def _fetch_with_datasets(ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    from datasets import (  # pylint: disable=import-outside-toplevel,import-error
        load_dataset,
    )

    wanted = set(ids)
    rows = load_dataset(DATASET, split=SPLIT)
    return {r["instance_id"]: r for r in rows if r["instance_id"] in wanted}


def _fetch_with_rest(ids: Optional[Sequence[str]]) -> Dict[str, Dict[str, Any]]:
    """Page through the split on the datasets-server; stop once every id is seen.

    ``None`` reads every row.
    """
    import requests  # pylint: disable=import-outside-toplevel

    wanted = None if ids is None else set(ids)
    found: Dict[str, Dict[str, Any]] = {}
    offset = 0
    while wanted is None or wanted - set(found):
        query = urlencode(
            {
                "dataset": DATASET,
                "config": "default",
                "split": SPLIT,
                "offset": offset,
                "length": ROWS_PER_PAGE,
            }
        )
        try:
            resp = requests.get(f"{DATASET_ROWS_URL}?{query}", timeout=FETCH_TIMEOUT_S)
            resp.raise_for_status()
            page = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise SweBenchError(
                f"could not read {DATASET} from {DATASET_ROWS_URL} (offset {offset}): "
                f"{exc}. Install the `datasets` package to read it directly, or "
                "retry when the datasets-server answers."
            ) from exc
        rows = [entry["row"] for entry in page.get("rows") or []]
        for row in rows:
            if wanted is None or row.get("instance_id") in wanted:
                found[row["instance_id"]] = row
        offset += len(rows)
        if not rows or offset >= int(page.get("num_rows_total") or 0):
            break
    return found


def fetch_instances(ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """The raw dataset rows for *ids*, from ``datasets`` when installed, else the REST API."""
    if importlib.util.find_spec("datasets") is not None:
        return _fetch_with_datasets(ids)
    if importlib.util.find_spec("requests") is not None:
        return _fetch_with_rest(ids)
    raise SweBenchError(
        f"reading {DATASET} needs the `datasets` package or `requests`. "
        "Run `pip install datasets` (or `pip install requests`)."
    )


#: The seed behind the published GAIA samples, so `--sample N` is reproducible.
SAMPLE_SEED = 20260930


def all_instance_ids() -> List[str]:
    """Every instance id in the split, sorted."""
    if importlib.util.find_spec("datasets") is not None:
        from datasets import (  # pylint: disable=import-outside-toplevel,import-error
            load_dataset,
        )

        return sorted(load_dataset(DATASET, split=SPLIT)["instance_id"])
    return sorted(_fetch_with_rest(None))


def sample_ids(
    n: int, seed: int = SAMPLE_SEED, ids: Optional[Sequence[str]] = None
) -> List[str]:
    """A seeded random *n* of the split: the same *n* and seed pick the same tasks."""
    pool = sorted(ids if ids is not None else all_instance_ids())
    if not 1 <= n <= len(pool):
        raise SweBenchError(
            f"--sample must be between 1 and {len(pool)} ({DATASET} {SPLIT}), not {n}"
        )
    return sorted(random.Random(seed).sample(pool, n))


def load_instances(
    ids: Sequence[str], cache_dir: Path, fetch: Optional[Fetcher] = None
) -> List[Dict[str, Any]]:
    """The instances *ids* name, in that order, from the cache or the dataset.

    Each instance is cached as ``<cache_dir>/<instance_id>.json`` on first
    use; a later run reads it without the network. An id the dataset does
    not have is an error naming it.
    """
    if not ids:
        raise SweBenchError("no SWE-bench instances were named")
    duplicates = sorted({i for i in ids if list(ids).count(i) > 1})
    if duplicates:
        raise SweBenchError(f"instance ids repeat: {duplicates}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached: Dict[str, Dict[str, Any]] = {}
    for instance_id in ids:
        path = cache_dir / f"{instance_id}.json"
        if path.is_file():
            inst = json.loads(path.read_text(encoding="utf-8"))
            if inst.pop("patch", None) is not None:
                # Written by an older harness: take the answer back off disk.
                path.write_text(json.dumps(inst, indent=2), encoding="utf-8")
            cached[instance_id] = inst
    missing = [i for i in ids if i not in cached]
    if missing:
        fetched = (fetch or fetch_instances)(missing)
        unknown = [i for i in missing if i not in fetched]
        if unknown:
            raise SweBenchError(
                f"{DATASET} ({SPLIT}) has no instance {unknown}. Instance ids look "
                f"like {PILOT[0]!r}."
            )
        for instance_id in missing:
            inst = _normalize(fetched[instance_id])
            # The gold patch is the answer and a full-access agent can read any
            # file, so this cache never holds it; gold_patches() fetches it for
            # the judge afterwards. (The `datasets` package's own HF cache still does.)
            del inst["patch"]
            (cache_dir / f"{instance_id}.json").write_text(
                json.dumps(inst, indent=2), encoding="utf-8"
            )
            cached[instance_id] = inst
    return [cached[i] for i in ids]


def gold_patches(ids: Sequence[str], fetch: Optional[Fetcher] = None) -> Dict[str, str]:
    """The reference fix for each instance, fetched now and never written to disk."""
    fetched = (fetch or fetch_instances)(list(ids))
    missing = [i for i in ids if i not in fetched]
    if missing:
        raise SweBenchError(f"{DATASET} ({SPLIT}) has no instance {missing}.")
    return {i: _normalize(fetched[i])["patch"] for i in ids}


# ---------------------------------------------------------------------------
# The checkout
# ---------------------------------------------------------------------------


def _git(args: Sequence[str], cwd: Optional[Path] = None) -> str:
    git = shutil.which("git")
    if not git:
        raise SweBenchError("SWE-bench tasks need git on PATH.")
    try:
        proc = subprocess.run(
            [git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_S,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SweBenchError(
            f"`git {' '.join(args)}` failed: {exc.stderr.strip()[-500:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SweBenchError(
            f"`git {' '.join(args)}` did not finish in {GIT_TIMEOUT_S}s"
        ) from exc
    return proc.stdout


def repo_url(instance: Mapping[str, Any]) -> str:
    return f"https://github.com/{instance['repo']}.git"


def _cache(cache_root: Path, url: str, prefix: str = "swebench") -> Path:
    key = hashlib.sha256(url.encode()).hexdigest()[:12]
    cache = cache_root / "cache" / f"{prefix}-{key}.git"
    if not cache.is_dir():
        cache.parent.mkdir(parents=True, exist_ok=True)
        _git(["init", "--quiet", "--bare", str(cache)])
    return cache


def _branch(sha: str) -> str:
    return f"base-{sha[:12]}"


def fetch_commit(
    url: str, sha: str, cache_root: Path, prefix: str = "swebench"
) -> Tuple[Path, str]:
    """The bare cache for *url* holding *sha* (and its :data:`HISTORY_DEPTH` ancestors).

    Returns the cache and the branch naming *sha* in it. *prefix* names the
    cache, so TheRock's ``therock`` caches are shared with :mod:`therock`.
    """
    cache, branch = _cache(cache_root, url, prefix), _branch(sha)
    if not _git(["for-each-ref", f"refs/heads/{branch}"], cwd=cache).strip():
        _git(
            [
                "fetch",
                "--quiet",
                "--no-tags",
                f"--depth={HISTORY_DEPTH}",
                url,
                f"{sha}:refs/heads/{branch}",
            ],
            cwd=cache,
        )
    return cache, branch


def checkout(
    instance: Mapping[str, Any],
    workdir: Path,
    cache_root: Path,
    url: Optional[str] = None,
) -> None:
    """*workdir* becomes the repository at ``base_commit``, detached, with no remote.

    The bare cache is filled by commit, to depth :data:`HISTORY_DEPTH`, so it
    never holds the fix that landed after the base. *url* replaces the GitHub
    URL, for a mirror or a test.
    """
    base, url = instance["base_commit"], url or repo_url(instance)
    cache, branch = fetch_commit(url, base, cache_root)
    _git(
        [
            "clone",
            "--quiet",
            "--no-tags",
            "--single-branch",
            f"--branch={branch}",
            f"--depth={HISTORY_DEPTH}",
            cache.resolve().as_uri(),
            str(workdir),
        ]
    )
    _git(["remote", "remove", "origin"], cwd=workdir)
    _git(["checkout", "--quiet", "--detach"], cwd=workdir)
    _git(["branch", "--quiet", "-D", branch], cwd=workdir)
    head = _git(["rev-parse", "HEAD"], cwd=workdir).strip()
    if head != base:
        raise SweBenchError(
            f"{instance['instance_id']}: checkout is at {head}, not the base "
            f"commit {base}"
        )
    stray = _git(["fsck", "--unreachable", "--no-reflogs"], cwd=workdir).strip()
    if stray:
        raise SweBenchError(
            f"{instance['instance_id']}: the checkout holds objects off its "
            f"history, which could carry the fix: {stray[:300]}"
        )
    remotes = _git(["remote"], cwd=workdir).strip()
    if remotes:
        raise SweBenchError(f"the checkout still has remotes: {remotes}")


# ---------------------------------------------------------------------------
# Isolation from the host's installed packages
# ---------------------------------------------------------------------------


def _bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _venv_packages(venv: Path) -> List[str]:
    """What is installed in *venv*: every entry of its site-packages trees."""
    trees = list(venv.glob("Lib/site-packages")) + list(
        venv.glob("lib/python*/site-packages")
    )
    return sorted(e.name for t in trees for e in t.iterdir() if e.name != "__pycache__")


def tool_interpreter(work_root: Path) -> Path:
    """The Python the agent gets: a bare venv with nothing installed, not even pip.

    The host interpreter can hold a newer release of the package under test,
    fix included. The venv is shared by every task of the work root, so one
    that holds anything — an agent installed into it — is rebuilt.
    """
    venv = (
        cache_dir(work_root)
        / "toolchain"
        / f"py{sys.version_info.major}{sys.version_info.minor}"
    )
    python = _bin_dir(venv) / ("python.exe" if os.name == "nt" else "python")
    if python.is_file() and not _venv_packages(venv):
        return python
    proc = subprocess.run(
        [sys.executable, "-m", "venv", "--clear", "--without-pip", str(venv)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0 or not python.is_file() or _venv_packages(venv):
        raise SweBenchError(
            f"could not build the agent's bare interpreter at {venv}: "
            f"{(proc.stderr or proc.stdout).strip()[-500:]}. Check that "
            f"`{sys.executable} -m venv --without-pip` works on this host."
        )
    return python


def host_site_packages() -> Tuple[Path, ...]:
    """Every site-packages tree of the interpreter running the eval, and of its base."""
    base = {"base": sys.base_prefix, "platbase": sys.base_exec_prefix}
    found = [
        *site.getsitepackages(),
        site.getusersitepackages(),
        sysconfig.get_paths()["purelib"],
        sysconfig.get_paths()["platlib"],
        sysconfig.get_paths(vars=base)["purelib"],
        sysconfig.get_paths(vars=base)["platlib"],
    ]
    paths = [Path(p).resolve() for p in found if p]
    return tuple(
        dict.fromkeys(
            p
            for p in paths
            if p.name in ("site-packages", "dist-packages") and p.is_dir()
        )
    )


def _dist_key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_copy(repo: str) -> Tuple[Path, ...]:
    """Where the host interpreter has the package of *repo* installed, if it does.

    The distribution is named after the repository (``matplotlib/matplotlib``,
    ``scikit-learn/scikit-learn``); its top-level entries in site-packages —
    packages, modules, ``.dist-info`` — are the copy.
    """
    try:
        dist = importlib.metadata.distribution(repo.split("/")[-1])
    except importlib.metadata.PackageNotFoundError:
        return ()
    tops = {Path(str(f)).parts[0] for f in dist.files or ()} - {"..", "__pycache__"}
    paths = [Path(dist.locate_file(t)).resolve() for t in tops]
    return tuple(sorted(p for p in paths if p.exists()))


@functools.lru_cache(maxsize=None)
def gaia_imports(
    roots: Tuple[str, ...] = ("amd-gaia", "gaia-agent", "gaia-agent-chat")
) -> frozenset:
    """Distributions GAIA's agent process may import: *roots* and what they require.

    Extras are left out (with them the set is most of SWE-bench); platform
    markers are not evaluated, so otherwise it errs large. A package wrongly
    left out fails loudly: the agent process exits on the denied import.
    """
    seen: set = set()
    todo = [_dist_key(r) for r in roots]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = importlib.metadata.requires(name) or []
        except importlib.metadata.PackageNotFoundError:
            continue
        for req in requires:
            if "extra" in req.partition(";")[2]:
                continue
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
            if match:
                todo.append(_dist_key(match.group(1)))
    return frozenset(seen)


def deniable_copy(repo: str) -> Tuple[Path, ...]:
    """The installed copy of *repo* that ``--fence`` can deny GAIA's agent process.

    That process imports from site-packages, so it keeps the trees, losing only
    the package under test, and not even that when GAIA imports it itself.
    """
    if _dist_key(repo.split("/")[-1]) in gaia_imports():
        log.warning(
            "%s is one of GAIA's own dependencies, so the fence cannot deny its "
            "installed copy to the GAIA agent; the agent's bare interpreter "
            "still cannot import it.",
            repo,
        )
        return ()
    return installed_copy(repo)


# ---------------------------------------------------------------------------
# Tasks and predictions
# ---------------------------------------------------------------------------


def task_prompt(instance: Mapping[str, Any]) -> str:
    return f"{str(instance['problem_statement']).strip()}\n\n{PROMPT_SUFFIX}"


def task_for(instance: Mapping[str, Any]) -> Dict[str, Any]:
    """The task definition an instance becomes, in the shape ``tasks.json`` uses."""
    return {
        "id": instance["instance_id"],
        "check": CHECK,
        "prompt": task_prompt(instance),
        "max_steps": MAX_STEPS,
        "closed_book": "instructed",
        "swebench": {
            "instance_id": instance["instance_id"],
            "repo": instance["repo"],
            "base_commit": instance["base_commit"],
            "image": instance["image"],
        },
    }


def agent_patch(workdir: Path) -> str:
    """Everything the agent changed, new files included."""
    _git(["add", "--all", "--intent-to-add"], cwd=workdir)
    return _git(["-c", "core.quotepath=off", "diff", "--no-color", "HEAD"], cwd=workdir)


def capture_prediction(
    workdir: Path, instance_id: str, model_name: str, preds_path: Path
) -> str:
    """Add the agent's patch to *preds_path* (JSON lines); return the patch.

    An earlier row for the same instance is replaced. An empty patch is an
    error: there is nothing to submit, and the harness would not run it.
    """
    patch = agent_patch(workdir)
    if not patch.strip():
        raise SweBenchError(
            f"{instance_id}: no patch, the agent changed nothing in {workdir}"
        )
    rows = read_predictions(preds_path) if preds_path.is_file() else []
    rows = [r for r in rows if r.get("instance_id") != instance_id]
    rows.append(
        {
            "instance_id": instance_id,
            "model_name_or_path": model_name,
            "model_patch": patch,
        }
    )
    preds_path.parent.mkdir(parents=True, exist_ok=True)
    preds_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return patch


def read_predictions(preds_path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in preds_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# The official harness
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """What the official harness decided for one instance."""

    instance_id: str
    #: ``None``: the harness left no report (not run, or it failed).
    resolved: Optional[bool] = None
    patch_applied: bool = False
    f2p_passed: int = 0
    f2p_total: int = 0
    p2p_passed: int = 0
    p2p_total: int = 0
    error: str = ""

    @property
    def why(self) -> str:
        if self.error:
            return self.error
        state = "resolved" if self.resolved else "unresolved"
        counts = (
            f"FAIL_TO_PASS {self.f2p_passed}/{self.f2p_total}, "
            f"PASS_TO_PASS {self.p2p_passed}/{self.p2p_total}"
        )
        applied = "" if self.patch_applied else "; patch did not apply"
        return f"{state}; {counts}{applied}"

    def as_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "why": self.why}


def harness_command(
    preds_path: Path, instance_id: str, report_dir: Path, python: str = sys.executable
) -> List[str]:
    return [
        python,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        DATASET,
        "--split",
        SPLIT,
        "--instance_ids",
        instance_id,
        "--predictions_path",
        str(preds_path),
        "--run_id",
        RUN_ID,
        "--max_workers",
        "1",
        "--timeout",
        str(HARNESS_TIMEOUT_S),
        "--report_dir",
        str(report_dir),
    ]


def _grade_in_container() -> bool:
    """Whether the harness must run in a Linux container rather than here.

    On Windows the harness writes each instance's eval script and patch with
    CRLF line endings; bash in the instance container then reads every command
    with a trailing CR, so every patch "fails to apply" and no test runs.
    """
    return sys.platform == "win32"


def containerized_harness_command(
    preds_path: Path, instance_id: str, report_dir: Path
) -> List[str]:
    """The harness run inside :data:`GRADER_IMAGE`, driving the host's Docker."""
    inner = harness_command(preds_path, instance_id, report_dir, python="python")
    inner[inner.index("--predictions_path") + 1] = f"/preds/{preds_path.name}"
    inner[inner.index("--report_dir") + 1] = "/work"
    return [
        shutil.which("docker") or "docker",
        "run",
        "--rm",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{report_dir.resolve()}:/work",
        "-v",
        f"{preds_path.resolve().parent}:/preds:ro",
        "-w",
        "/work",
        GRADER_IMAGE,
        *inner,
    ]


def _ensure_grader_image() -> None:
    if _docker(["image", "inspect", GRADER_IMAGE], FETCH_TIMEOUT_S).returncode == 0:
        return
    log.info("SWE-bench: building the Linux grader image %s", GRADER_IMAGE)
    built = subprocess.run(
        [shutil.which("docker") or "docker", "build", "-t", GRADER_IMAGE, "-"],
        input=GRADER_DOCKERFILE,
        capture_output=True,
        text=True,
        timeout=GRADER_BUILD_TIMEOUT_S,
        check=False,
    )
    if built.returncode != 0:
        raise SweBenchError(
            f"could not build the SWE-bench grader image {GRADER_IMAGE}: "
            f"{(built.stderr or built.stdout).strip()[-400:]}"
        )


def _require_harness() -> None:
    if not _grade_in_container() and importlib.util.find_spec("swebench") is None:
        raise SweBenchError(
            "grading needs the official harness in this interpreter: "
            f"`{sys.executable} -m pip install swebench` (5.x)."
        )
    docker = shutil.which("docker")
    if not docker:
        raise SweBenchError(
            "grading needs Docker on PATH: the harness runs each instance's tests "
            "in its own container."
        )
    probe = subprocess.run(
        [docker, "info"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        raise SweBenchError(
            "the Docker daemon is not answering `docker info`: "
            f"{(probe.stderr or probe.stdout).strip()[-300:]}. Start Docker and retry."
        )


def _docker(args: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shutil.which("docker") or "docker", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def evaluate(
    preds_path: Path,
    instances: Sequence[Mapping[str, Any]],
    report_dir: Path,
    *,
    docker_platform: str = DOCKER_PLATFORM,
    pull_then_remove: bool = True,
) -> Dict[str, Verdict]:
    """Grade the predictions for *instances* with the official harness, one at a time.

    Each instance's image is pulled for *docker_platform* just before its run
    and removed right after, unless *pull_then_remove* is off. The harness
    writes under *report_dir*. A missing harness or Docker is an error before
    anything runs; an instance whose harness run fails is reported in its
    verdict, and the others still run.
    """
    _require_harness()
    if not preds_path.is_file():
        raise SweBenchError(f"no predictions at {preds_path}: nothing to grade")
    in_container = _grade_in_container()
    if in_container:
        _ensure_grader_image()
    submitted = {r["instance_id"] for r in read_predictions(preds_path)}
    report_dir.mkdir(parents=True, exist_ok=True)
    failures: Dict[str, str] = {}
    for inst in instances:
        instance_id, image = inst["instance_id"], inst["image"]
        if instance_id not in submitted:
            continue
        if pull_then_remove:
            pulled = _docker(
                ["pull", "--quiet", "--platform", docker_platform, image],
                DOCKER_PULL_TIMEOUT_S,
            )
            if pulled.returncode != 0:
                raise SweBenchError(
                    f"{instance_id}: `docker pull --platform {docker_platform} "
                    f"{image}` failed: {pulled.stderr.strip()[-300:]}"
                )
        log.info("SWE-bench: grading %s", instance_id)
        command = (
            containerized_harness_command(preds_path, instance_id, report_dir)
            if in_container
            else harness_command(preds_path, instance_id, report_dir)
        )
        proc = subprocess.run(
            command,
            cwd=str(report_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=HARNESS_TIMEOUT_S + 600,
            check=False,
        )
        (report_dir / f"{instance_id}.harness.log").write_text(
            f"$ {' '.join(proc.args)}\n{proc.stdout}\n{proc.stderr}", encoding="utf-8"
        )
        if proc.returncode != 0:
            failures[instance_id] = (
                f"the harness exited {proc.returncode}; see "
                f"{report_dir / f'{instance_id}.harness.log'}"
            )
        if pull_then_remove:
            removed = _docker(["rmi", image], FETCH_TIMEOUT_S)
            if removed.returncode != 0:
                log.warning(
                    "could not remove %s: %s", image, removed.stderr.strip()[-200:]
                )
    verdicts = parse_report(report_dir)
    for inst in instances:
        instance_id = inst["instance_id"]
        if instance_id in verdicts:
            continue
        if instance_id not in submitted:
            error = "no patch was submitted"
        else:
            error = failures.get(instance_id, "the harness wrote no report")
        verdicts[instance_id] = Verdict(instance_id, error=error)
    return verdicts


def parse_report(report_dir: Path) -> Dict[str, Verdict]:
    """Every per-instance ``report.json`` the harness wrote under *report_dir*.

    The harness writes ``logs/run_evaluation/<run id>/<model>/<instance>/
    report.json`` relative to its working directory, keyed by instance id,
    with ``resolved``, ``patch_successfully_applied`` and a ``tests_status``
    of FAIL_TO_PASS / PASS_TO_PASS ``success`` and ``failure`` lists.
    """
    verdicts: Dict[str, Verdict] = {}
    for path in sorted(
        (report_dir / "logs" / "run_evaluation").glob("*/*/*/report.json")
    ):
        instance_id = path.parent.name
        try:
            report = json.loads(path.read_text(encoding="utf-8"))[instance_id]
        except (ValueError, KeyError) as exc:
            verdicts[instance_id] = Verdict(
                instance_id, error=f"unreadable report {path}: {exc}"
            )
            continue
        status = report.get("tests_status") or {}
        f2p = status.get("FAIL_TO_PASS") or {}
        p2p = status.get("PASS_TO_PASS") or {}
        f2p_ok, f2p_bad = len(f2p.get("success") or []), len(f2p.get("failure") or [])
        p2p_ok, p2p_bad = len(p2p.get("success") or []), len(p2p.get("failure") or [])
        verdicts[instance_id] = Verdict(
            instance_id,
            resolved=bool(report.get("resolved")),
            patch_applied=bool(report.get("patch_successfully_applied")),
            f2p_passed=f2p_ok,
            f2p_total=f2p_ok + f2p_bad,
            p2p_passed=p2p_ok,
            p2p_total=p2p_ok + p2p_bad,
        )
    return verdicts
