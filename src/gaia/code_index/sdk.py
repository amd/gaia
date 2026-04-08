# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
CodeIndexSDK — semantic code search over repositories, git history, and PRs.

Reuses GAIA's Lemonade Server embedding infrastructure (AMD NPU/GPU accelerated)
and FAISS for vector similarity search.
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration and response dataclasses
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS = {
    ".env",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "secret",
    ".htpasswd",
}


@dataclass
class CodeIndexConfig:
    """Configuration for CodeIndexSDK."""

    repo_path: str
    max_files: int = 5000
    max_file_size_mb: float = 1
    chunk_overlap: int = 50
    embedding_model: str = "nomic-embed-text-v2-moe-GGUF"
    cache_dir: str = "~/.gaia/code_index"
    index_git_history: bool = True
    index_prs: bool = False
    max_commits: int = 1000
    embedding_base_url: Optional[str] = None


@dataclass
class CodeChunk:
    """A semantic chunk of source code."""

    content: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None
    docstring: Optional[str] = None
    imports: List[str] = field(default_factory=list)


@dataclass
class CommitChunk:
    """A git commit, represented as a searchable chunk."""

    content: str
    commit_hash: str
    author: str
    date: str
    files_changed: List[str]
    diff_summary: str


@dataclass
class PRChunk:
    """A GitHub pull request, represented as a searchable chunk."""

    content: str
    pr_number: int
    title: str
    state: str
    author: str
    labels: List[str]
    files_changed: List[str]
    url: str
    body: str = ""


@dataclass
class SearchResult:
    """A single search result."""

    chunk: Union[CodeChunk, CommitChunk, PRChunk]
    score: float
    result_type: str  # "code" | "commit" | "pr"


@dataclass
class IndexResult:
    """Result of indexing a repository."""

    files_indexed: int
    chunks_created: int
    commits_indexed: int
    prs_indexed: int
    duration_seconds: float


# ---------------------------------------------------------------------------
# Cache metadata schema version — bump when structure changes
# ---------------------------------------------------------------------------

_CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# CodeIndexSDK
# ---------------------------------------------------------------------------


class CodeIndexSDK:
    """
    Semantic code search over repositories, git history, and pull requests.

    Uses Lemonade Server (AMD NPU/GPU) for hardware-accelerated embeddings
    and FAISS IndexFlatL2 for vector similarity search.

    Cache layout (in ``~/.gaia/code_index/<repo_hash>/``):
    - ``metadata.json``  — chunk metadata + file hashes + model version
    - ``index.faiss``    — FAISS binary index

    Both files are written atomically (temp → rename).
    """

    def __init__(self, config: CodeIndexConfig):
        self.config = config
        self.log = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # Validate repo path
        repo = Path(config.repo_path).resolve()
        if not repo.exists():
            raise ValueError(f"repo_path does not exist: {config.repo_path}")
        if not repo.is_dir():
            raise ValueError(f"repo_path is not a directory: {config.repo_path}")
        self._repo_root = repo

        # PathValidator scoped to repo root
        try:
            from gaia.security import PathValidator

            self._path_validator = PathValidator(allowed_paths=[str(self._repo_root)])
        except ImportError:
            self._path_validator = None

        # Cache directory
        self._cache_dir = Path(config.cache_dir).expanduser() / self._repo_hash()
        self._meta_path = self._cache_dir / "metadata.json"
        self._index_path = self._cache_dir / "index.faiss"

        # Lazy-loaded state
        self._faiss_index = None
        self._metadata: Optional[Dict[str, Any]] = None
        self._embedder = None
        self._llm_client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_repository(self) -> IndexResult:
        """
        Index the repository: discover files, parse, embed, build FAISS index,
        persist atomically.
        """
        start = time.time()

        self.log.info(f"Indexing repository: {self._repo_root}")

        # Load existing metadata (for incremental indexing)
        existing_meta = self._load_metadata()
        existing_file_hashes = {}
        existing_chunks = []
        if existing_meta and existing_meta.get("version") == _CACHE_VERSION:
            if existing_meta.get("embedding_model") == self.config.embedding_model:
                existing_file_hashes = existing_meta.get("file_hashes", {})
                existing_chunks = [
                    self._dict_to_chunk(c) for c in existing_meta.get("chunks", [])
                ]

        # Discover source files
        source_files = self._discover_files()
        self.log.info(f"Discovered {len(source_files)} source files")

        # Lazy import parsers
        from gaia.code_index.parsers import chunk_code_file

        # Parse files — incremental: skip unchanged
        all_chunks: List[Union[CodeChunk, CommitChunk, PRChunk]] = []
        new_file_hashes: Dict[str, str] = {}
        files_indexed = 0

        for file_path in source_files:
            rel_path = str(Path(file_path).relative_to(self._repo_root))
            content = self._read_file_safe(file_path)
            if content is None:
                continue

            file_hash = hashlib.sha256(
                content.encode("utf-8", errors="replace")
            ).hexdigest()
            new_file_hashes[rel_path] = file_hash

            if existing_file_hashes.get(rel_path) == file_hash:
                # File unchanged — reuse existing chunks
                reused = [
                    c
                    for c in existing_chunks
                    if isinstance(c, CodeChunk) and c.file_path == rel_path
                ]
                all_chunks.extend(reused)
                continue

            # Parse changed/new file
            parsed = chunk_code_file(rel_path, content)
            all_chunks.extend(parsed)
            files_indexed += 1

        # Git history
        commits_indexed = 0
        if self.config.index_git_history:
            try:
                from gaia.code_index.git import GitIndexer

                git_indexer = GitIndexer(self.config)
                commits = git_indexer.get_commits()
                all_chunks.extend(commits)
                commits_indexed = len(commits)
                if commits_indexed:
                    self.log.info(f"Indexed {commits_indexed} commits")
            except Exception as e:
                self.log.warning(f"Git history indexing failed (skipping): {e}")

        # PR data
        prs_indexed = 0
        if self.config.index_prs:
            try:
                from gaia.code_index.git import GitIndexer

                git_indexer = GitIndexer(self.config)
                prs = git_indexer.get_pull_requests()
                all_chunks.extend(prs)
                prs_indexed = len(prs)
                if prs_indexed:
                    self.log.info(f"Indexed {prs_indexed} PRs")
            except Exception as e:
                self.log.warning(f"PR indexing failed (skipping): {e}")

        if not all_chunks:
            self.log.warning("No chunks to index")
            return IndexResult(
                files_indexed=0,
                chunks_created=0,
                commits_indexed=commits_indexed,
                prs_indexed=prs_indexed,
                duration_seconds=time.time() - start,
            )

        # Embed and build FAISS index
        self.log.info(f"Embedding {len(all_chunks)} chunks...")
        texts = [self._chunk_to_embed_text(c) for c in all_chunks]
        embeddings, valid_chunks = self._encode_texts_with_sync(texts, all_chunks)

        if len(valid_chunks) == 0:
            self.log.error("All embeddings failed — index not saved")
            return IndexResult(
                files_indexed=files_indexed,
                chunks_created=0,
                commits_indexed=commits_indexed,
                prs_indexed=prs_indexed,
                duration_seconds=time.time() - start,
            )

        faiss_index = self._build_faiss_index(embeddings)

        # Persist atomically
        meta = {
            "version": _CACHE_VERSION,
            "embedding_model": self.config.embedding_model,
            "embedding_dim": embeddings.shape[1],
            "file_hashes": new_file_hashes,
            "chunks": [self._chunk_to_dict(c) for c in valid_chunks],
            "created_at": time.time(),
        }
        self._save_atomic(faiss_index, meta)

        # Update in-memory state
        self._faiss_index = faiss_index
        self._metadata = meta

        duration = time.time() - start
        code_chunks = sum(1 for c in valid_chunks if isinstance(c, CodeChunk))
        self.log.info(f"Indexed {code_chunks} code chunks in {duration:.1f}s")

        return IndexResult(
            files_indexed=files_indexed,
            chunks_created=len(valid_chunks),
            commits_indexed=commits_indexed,
            prs_indexed=prs_indexed,
            duration_seconds=duration,
        )

    def search(
        self,
        query: str,
        scope: str = "all",
        top_k: int = 10,
    ) -> List[SearchResult]:
        """
        Semantic search over indexed code, commits, and/or PRs.

        Args:
            query: Natural language or code query.
            scope: "all" | "code" | "commit" | "pr"
            top_k: Maximum results to return.

        Returns:
            List of SearchResult ordered by descending relevance.
        """
        if not self._ensure_index_loaded():
            return []

        # Verify embedding model matches index
        meta = self._metadata or {}
        indexed_model = meta.get("embedding_model", "")
        if indexed_model and indexed_model != self.config.embedding_model:
            self.log.warning(
                f"Embedding model mismatch: index built with '{indexed_model}', "
                f"current model is '{self.config.embedding_model}'. "
                "Re-run `index_repository()` to rebuild."
            )
            return []

        # Encode query
        try:
            self._load_embedder()
            query_emb = self._encode_texts([query])
        except Exception as e:
            self.log.error(f"Query encoding failed: {e}")
            return []

        if query_emb.size == 0:
            return []

        import numpy as np

        query_vec = query_emb[0:1].astype(np.float32)

        # FAISS search — over-fetch when scope filtering is active so we
        # don't silently return fewer results than top_k.
        ntotal = self._faiss_index.ntotal
        if ntotal == 0:
            return []

        if scope == "all":
            fetch_k = min(top_k, ntotal)
        elif scope in ("commit", "pr"):
            # Commit/PR chunks are typically a tiny fraction of the index,
            # so we need to fetch aggressively to find enough matches.
            fetch_k = min(top_k * 50, ntotal)
        else:
            fetch_k = min(top_k * 3, ntotal)

        distances, indices = self._faiss_index.search(query_vec, fetch_k)

        chunks = [self._dict_to_chunk(c) for c in meta.get("chunks", [])]
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(chunks):
                continue
            chunk = chunks[idx]
            result_type = (
                "code"
                if isinstance(chunk, CodeChunk)
                else "commit" if isinstance(chunk, CommitChunk) else "pr"
            )
            if scope != "all" and result_type != scope:
                continue
            # Convert L2 distance to a similarity score in [0, 1]
            score = float(1.0 / (1.0 + dist))
            results.append(
                SearchResult(chunk=chunk, score=score, result_type=result_type)
            )
            if len(results) >= top_k:
                break

        return results

    def get_status(self) -> Dict[str, Any]:
        """Return index statistics."""
        meta = self._load_metadata()
        if not meta:
            return {"indexed": False, "repo_path": str(self._repo_root)}

        chunks = meta.get("chunks", [])
        code_count = sum(1 for c in chunks if c.get("chunk_type") == "code")
        commit_count = sum(1 for c in chunks if c.get("chunk_type") == "commit")
        pr_count = sum(1 for c in chunks if c.get("chunk_type") == "pr")

        return {
            "indexed": True,
            "repo_path": str(self._repo_root),
            "embedding_model": meta.get("embedding_model"),
            "total_chunks": len(chunks),
            "code_chunks": code_count,
            "commit_chunks": commit_count,
            "pr_chunks": pr_count,
            "files_tracked": len(meta.get("file_hashes", {})),
            "created_at": meta.get("created_at"),
            "cache_path": str(self._cache_dir),
        }

    def clear_index(self):
        """Remove the cached index for this repository."""
        import shutil

        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir)
            self.log.info(f"Cleared index at {self._cache_dir}")

        self._faiss_index = None
        self._metadata = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _repo_hash(self) -> str:
        """SHA-256 of the resolved repo path — used as cache subdirectory."""
        return hashlib.sha256(str(self._repo_root).encode()).hexdigest()[:16]

    def _discover_files(self) -> List[str]:
        """
        Walk the repository, respecting .gitignore patterns and size/binary limits.
        Returns list of absolute file paths.
        """
        import fnmatch

        # Read .gitignore patterns
        ignore_patterns = self._read_gitignore_patterns()

        # Common directories to always skip
        always_skip = {
            ".git",
            ".hg",
            ".svn",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            "env",
            "dist",
            "build",
            ".tox",
            ".eggs",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        }

        # Supported code extensions
        code_extensions = {
            ".py",
            ".pyw",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".java",
            ".c",
            ".h",
            ".cpp",
            ".hpp",
            ".cs",
            ".rb",
            ".php",
            ".swift",
            ".kt",
            ".scala",
            ".sh",
            ".bash",
            ".zsh",
            ".fish",
            ".yaml",
            ".yml",
            ".toml",
            ".json",
            ".md",
            ".mdx",
            ".txt",
            ".rst",
        }

        result = []
        max_size_bytes = int(self.config.max_file_size_mb * 1024 * 1024)

        for root, dirs, files in os.walk(str(self._repo_root)):
            rel_root = Path(root).relative_to(self._repo_root)

            # Filter out skipped directories in-place
            dirs[:] = [
                d
                for d in dirs
                if d not in always_skip
                and not d.endswith(".egg-info")
                and not d.startswith(".")
                and not any(fnmatch.fnmatch(d, p) for p in ignore_patterns)
            ]

            if len(result) >= self.config.max_files:
                break

            for fname in files:

                abs_path = os.path.join(root, fname)
                rel_path = str(rel_root / fname)

                # Check extension
                ext = Path(fname).suffix.lower()
                if ext not in code_extensions:
                    continue

                # Check sensitive file patterns
                fname_lower = fname.lower()
                if any(p in fname_lower for p in _SENSITIVE_PATTERNS):
                    self.log.debug(f"Skipping sensitive file: {rel_path}")
                    continue

                # Check gitignore patterns
                if any(fnmatch.fnmatch(rel_path, p) for p in ignore_patterns):
                    continue

                # Check size
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    continue
                if size > max_size_bytes:
                    self.log.debug(f"Skipping large file ({size} bytes): {rel_path}")
                    continue

                result.append(abs_path)

        return result

    def _read_gitignore_patterns(self) -> List[str]:
        """Read .gitignore patterns from repo root."""
        gitignore = self._repo_root / ".gitignore"
        patterns = []
        if gitignore.exists():
            try:
                for line in gitignore.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
            except OSError:
                pass
        return patterns

    def _read_file_safe(self, file_path: str) -> Optional[str]:
        """Read a file, returning None on error or binary content."""
        try:
            with open(file_path, "rb") as f:
                raw = f.read(8192)
            if b"\x00" in raw:
                return None  # Binary file
            # Try UTF-8 first, then latin-1 fallback
            try:
                return Path(file_path).read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return Path(file_path).read_text(encoding="latin-1")
        except OSError:
            return None

    def _chunk_to_embed_text(
        self, chunk: Union[CodeChunk, CommitChunk, PRChunk]
    ) -> str:
        """Extract the text to embed (first 1200 chars for search)."""
        MAX_EMBED_CHARS = 1200
        if isinstance(chunk, CodeChunk):
            prefix = ""
            if chunk.symbol_name:
                prefix = f"{chunk.symbol_type or 'symbol'}: {chunk.symbol_name}\n"
            return (prefix + chunk.content)[:MAX_EMBED_CHARS]
        elif isinstance(chunk, CommitChunk):
            return f"commit {chunk.commit_hash[:8]} by {chunk.author}: {chunk.content}"[
                :MAX_EMBED_CHARS
            ]
        elif isinstance(chunk, PRChunk):
            return f"PR #{chunk.pr_number} ({chunk.state}): {chunk.title}\n{chunk.content}"[
                :MAX_EMBED_CHARS
            ]
        return str(chunk)[:MAX_EMBED_CHARS]

    def _load_embedder(self):
        """Load the Lemonade embedding model if not already loaded.

        Uses an additive load (no unload) since Lemonade Server supports multiple
        models simultaneously. Checks the health endpoint's ``all_models_loaded``
        list (actually running models) rather than ``/v1/models`` (which only lists
        downloaded models) to decide whether a ``load_model`` call is needed.

        The ``--split-mode none`` flag is required for multi-GPU ROCm systems where
        the embedding model's MoE kernels may crash on secondary GPU devices.
        """
        if self._embedder is not None:
            return

        from gaia.llm.lemonade_client import LemonadeClient

        if self._llm_client is None:
            kwargs = {}
            if self.config.embedding_base_url:
                kwargs["base_url"] = self.config.embedding_base_url
            self._llm_client = LemonadeClient(**kwargs)

        try:
            # Use health endpoint to check actually running models, not just
            # downloaded ones (list_models/get_status returns all downloaded).
            health = self._llm_client.health_check()
            running = [m.get("id", "") for m in health.get("all_models_loaded", [])]
            if self.config.embedding_model not in running:
                self._llm_client.load_model(
                    self.config.embedding_model,
                    llamacpp_args="--ubatch-size 2048 --split-mode none",
                )
        except Exception as e:
            self.log.warning(f"Could not pre-load embedding model: {e}")

        self._embedder = self._llm_client

    def _encode_texts(self, texts: List[str]):
        """Encode texts using Lemonade embeddings. Returns numpy array."""
        import numpy as np

        self._load_embedder()

        BATCH_SIZE = 25
        MAX_EMBED_CHARS = 1200
        safe_texts = [t[:MAX_EMBED_CHARS] for t in texts]

        all_embeddings = []
        for batch_start in range(0, len(safe_texts), BATCH_SIZE):
            batch = safe_texts[batch_start : batch_start + BATCH_SIZE]
            max_retries = 2
            response = None
            for attempt in range(max_retries + 1):
                try:
                    response = self._embedder.embeddings(
                        batch, model=self.config.embedding_model, timeout=180
                    )
                    break
                except Exception as e:
                    if attempt < max_retries:
                        self.log.warning(
                            f"Embedding batch attempt {attempt + 1} failed: {e}"
                        )
                        time.sleep(2)
                    else:
                        raise

            batch_embeddings = [
                item.get("embedding", []) for item in (response or {}).get("data", [])
            ]

            # Fallback: one-by-one if batch returned nothing
            if not batch_embeddings and batch:
                self.log.warning("Batch returned 0 embeddings, trying one-by-one")
                for single in batch:
                    try:
                        resp = self._embedder.embeddings(
                            [single], model=self.config.embedding_model, timeout=60
                        )
                        data = resp.get("data", [])
                        if data:
                            batch_embeddings.append(data[0].get("embedding", []))
                    except Exception as e:
                        self.log.warning(f"Single embedding failed: {e}")

            all_embeddings.extend(batch_embeddings)

        return np.array(all_embeddings, dtype=np.float32)

    def _encode_texts_with_sync(self, texts: List[str], chunks: List):
        """
        Encode texts and return (embeddings, valid_chunks) in lockstep.
        Filters out chunks whose embedding failed (empty vector).
        """
        import numpy as np

        self._load_embedder()

        BATCH_SIZE = 25
        MAX_EMBED_CHARS = 1200

        valid_chunks = []
        valid_embeddings = []

        for batch_start in range(0, len(texts), BATCH_SIZE):
            batch_texts = texts[batch_start : batch_start + BATCH_SIZE]
            batch_chunks = chunks[batch_start : batch_start + BATCH_SIZE]
            safe_texts = [t[:MAX_EMBED_CHARS] for t in batch_texts]

            max_retries = 2
            response = None
            for attempt in range(max_retries + 1):
                try:
                    response = self._embedder.embeddings(
                        safe_texts, model=self.config.embedding_model, timeout=180
                    )
                    break
                except Exception as e:
                    if attempt < max_retries:
                        self.log.warning(f"Batch attempt {attempt + 1} failed: {e}")
                        time.sleep(2)
                    else:
                        self.log.error(f"Batch embedding failed after retries: {e}")
                        # Fall through with empty response
                        response = {}

            batch_embeddings = [
                item.get("embedding", []) for item in (response or {}).get("data", [])
            ]

            # One-by-one fallback if batch returned nothing or partial result
            if len(batch_embeddings) != len(batch_chunks) and batch_embeddings:
                self.log.warning(
                    f"Partial batch: got {len(batch_embeddings)} embeddings "
                    f"for {len(batch_chunks)} chunks — retrying one-by-one"
                )
                batch_embeddings = []
            if not batch_embeddings and safe_texts:
                self.log.warning("Batch returned 0 embeddings, trying one-by-one")
                batch_embeddings = []
                for single_text in safe_texts:
                    try:
                        resp = self._embedder.embeddings(
                            [single_text], model=self.config.embedding_model, timeout=60
                        )
                        data = resp.get("data", [])
                        batch_embeddings.append(
                            data[0].get("embedding", []) if data else []
                        )
                    except Exception as e:
                        self.log.warning(f"Single embedding failed: {e}")
                        batch_embeddings.append([])

            # Sync: only keep chunks with valid (non-empty) embeddings
            for emb, chunk in zip(batch_embeddings, batch_chunks):
                if emb:
                    valid_chunks.append(chunk)
                    valid_embeddings.append(emb)
                else:
                    self.log.debug(
                        f"Skipping chunk with no embedding: {getattr(chunk, 'file_path', '?')}"
                    )

        if not valid_embeddings:
            return np.array([], dtype=np.float32), []

        return np.array(valid_embeddings, dtype=np.float32), valid_chunks

    def _build_faiss_index(self, embeddings):
        """Build a FAISS IndexFlatL2 from embeddings array."""
        import faiss
        import numpy as np

        dim = embeddings.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(embeddings.astype(np.float32))
        return index

    def _save_atomic(self, faiss_index, meta: dict):
        """
        Atomically persist the FAISS index and metadata JSON.
        Writes to temp files, then renames both.
        """
        import faiss

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        tmp_meta = self._meta_path.with_suffix(".tmp.json")
        tmp_index = self._index_path.with_suffix(".tmp.faiss")

        try:
            faiss.write_index(faiss_index, str(tmp_index))
            tmp_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            # Rename index first — if crash happens between renames,
            # _load_metadata will detect stale metadata via ntotal check.
            tmp_index.rename(self._index_path)
            tmp_meta.rename(self._meta_path)
            self.log.debug(f"Index saved to {self._cache_dir}")
        except Exception as e:
            self.log.error(f"Failed to save index: {e}")
            for tmp in (tmp_meta, tmp_index):
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _load_metadata(self) -> Optional[Dict[str, Any]]:
        """Load metadata JSON from cache. Returns None if missing or corrupt."""
        if not self._meta_path.exists():
            return None
        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            # Validate consistency: check that index file also exists
            if not self._index_path.exists():
                self.log.warning(
                    "Metadata exists but FAISS index missing — cache corrupt, ignoring"
                )
                return None
            if meta.get("version") != _CACHE_VERSION:
                self.log.info("Cache version mismatch — will rebuild")
                return None
            return meta
        except (json.JSONDecodeError, OSError) as e:
            self.log.warning(f"Failed to load cache metadata: {e}")
            return None

    def _ensure_index_loaded(self) -> bool:
        """Load FAISS index into memory if not already loaded."""
        if self._faiss_index is not None and self._metadata is not None:
            return True

        meta = self._load_metadata()
        if not meta:
            self.log.warning("No index found. Run index_repository() first.")
            return False

        try:
            import faiss

            index = faiss.read_index(str(self._index_path))
            expected = len(meta.get("chunks", []))
            if index.ntotal != expected:
                self.log.warning(
                    f"Index/metadata mismatch: FAISS has {index.ntotal} vectors "
                    f"but metadata has {expected} chunks — cache corrupt, ignoring"
                )
                return False
            self._faiss_index = index
            self._metadata = meta
            return True
        except Exception as e:
            self.log.error(f"Failed to load FAISS index: {e}")
            return False

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _chunk_to_dict(self, chunk: Union[CodeChunk, CommitChunk, PRChunk]) -> Dict:
        if isinstance(chunk, CodeChunk):
            return {
                "chunk_type": "code",
                "content": chunk.content,
                "file_path": chunk.file_path,
                "language": chunk.language,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "symbol_name": chunk.symbol_name,
                "symbol_type": chunk.symbol_type,
                "docstring": chunk.docstring,
                "imports": chunk.imports,
            }
        elif isinstance(chunk, CommitChunk):
            return {
                "chunk_type": "commit",
                "content": chunk.content,
                "commit_hash": chunk.commit_hash,
                "author": chunk.author,
                "date": chunk.date,
                "files_changed": chunk.files_changed,
                "diff_summary": chunk.diff_summary,
            }
        elif isinstance(chunk, PRChunk):
            return {
                "chunk_type": "pr",
                "content": chunk.content,
                "pr_number": chunk.pr_number,
                "title": chunk.title,
                "state": chunk.state,
                "author": chunk.author,
                "labels": chunk.labels,
                "files_changed": chunk.files_changed,
                "url": chunk.url,
            }
        raise ValueError(f"Unknown chunk type: {type(chunk)}")

    def _dict_to_chunk(self, d: Dict) -> Union[CodeChunk, CommitChunk, PRChunk]:
        t = d.get("chunk_type", "code")
        if t == "code":
            return CodeChunk(
                content=d["content"],
                file_path=d["file_path"],
                language=d["language"],
                start_line=d["start_line"],
                end_line=d["end_line"],
                symbol_name=d.get("symbol_name"),
                symbol_type=d.get("symbol_type"),
                docstring=d.get("docstring"),
                imports=d.get("imports", []),
            )
        elif t == "commit":
            return CommitChunk(
                content=d["content"],
                commit_hash=d["commit_hash"],
                author=d["author"],
                date=d["date"],
                files_changed=d.get("files_changed", []),
                diff_summary=d.get("diff_summary", ""),
            )
        elif t == "pr":
            return PRChunk(
                content=d["content"],
                pr_number=d["pr_number"],
                title=d["title"],
                state=d["state"],
                author=d["author"],
                labels=d.get("labels", []),
                files_changed=d.get("files_changed", []),
                url=d["url"],
            )
        raise ValueError(f"Unknown chunk_type: {t}")
