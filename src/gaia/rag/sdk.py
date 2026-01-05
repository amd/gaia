#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA RAG SDK - Simple PDF document retrieval and Q&A
"""

import hashlib
import os
import pickle
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    import faiss
except ImportError:
    faiss = None

from gaia.chat.sdk import ChatConfig, ChatSDK
from gaia.logger import get_logger
from gaia.security import PathValidator


@dataclass
class RAGConfig:
    """Configuration for RAG SDK."""

    model: str = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
    max_tokens: int = 1024
    chunk_size: int = 500
    chunk_overlap: int = 100  # Increased to 20% overlap for better context preservation
    max_chunks: int = 5  # Increased to retrieve more context
    embedding_model: str = (
        "nomic-embed-text-v2-moe-GGUF"  # Lemonade GGUF embedding model
    )
    cache_dir: str = ".gaia"
    show_stats: bool = False
    use_local_llm: bool = True
    base_url: str = "http://localhost:8000/api/v1"  # Lemonade server API URL
    # Memory management settings
    max_indexed_files: int = 100  # Maximum number of files to keep indexed
    max_total_chunks: int = 10000  # Maximum total chunks across all files
    enable_lru_eviction: bool = (
        True  # Enable automatic eviction of least recently used documents
    )
    # File size limits (prevent OOM)
    max_file_size_mb: int = 100  # Maximum file size in MB (default: 100MB)
    warn_file_size_mb: int = 50  # Warn if file exceeds this size (default: 50MB)
    # LLM-based chunking
    use_llm_chunking: bool = (
        False  # Enable LLM-based intelligent chunking (requires LLM client)
    )
    # VLM settings (enabled if available, errors out if model can't be loaded)
    vlm_model: str = "Qwen2.5-VL-7B-Instruct-GGUF"
    # Security settings
    allowed_paths: Optional[List[str]] = None


@dataclass
class RAGResponse:
    """Response from RAG operations with enhanced metadata."""

    text: str
    chunks: Optional[List[str]] = None
    chunk_scores: Optional[List[float]] = None
    stats: Optional[Dict[str, Any]] = None
    # Enhanced metadata
    source_files: Optional[List[str]] = None  # List of source files for each chunk
    chunk_metadata: Optional[List[Dict[str, Any]]] = None  # Detailed metadata per chunk
    query_metadata: Optional[Dict[str, Any]] = None  # Query-level metadata


class RAGSDK:
    """
    Simple RAG SDK for PDF document Q&A following GAIA patterns.

    Example usage:
        ```python
        from gaia.rag.sdk import RAGSDK, RAGConfig

        # Initialize
        config = RAGConfig(show_stats=True)
        rag = RAGSDK(config)

        # Index document
        rag.index_document("document.pdf")

        # Query
        response = rag.query("What are the key features?")
        print(response.text)
        ```
    """

    def __init__(self, config: Optional[RAGConfig] = None):
        """Initialize RAG SDK."""
        self.config = config or RAGConfig()
        self.log = get_logger(__name__)

        # Check dependencies
        self._check_dependencies()

        # Initialize components
        self.embedder = None
        self.llm_client = None
        self.use_lemonade_embeddings = False
        self.index = None
        self.chunks = []
        self.indexed_files = set()

        # Per-file indexing: maps file paths to their chunk indices
        # This enables efficient per-file searches
        self.file_to_chunk_indices = {}  # {file_path: [chunk_idx1, chunk_idx2, ...]}
        self.chunk_to_file = {}  # {chunk_idx: file_path} for reverse lookup

        # Per-file FAISS indices and embeddings (CACHED for performance)
        self.file_indices = {}  # {file_path: faiss.Index}
        self.file_embeddings = {}  # {file_path: numpy.array}

        # Per-file metadata (for /dump command and stats)
        self.file_metadata = (
            {}
        )  # {file_path: {'full_text': str, 'num_pages': int, 'vlm_pages': int, ...}}

        # LRU tracking for memory management
        self.file_access_times = {}  # {file_path: last_access_time}
        self.file_index_times = {}  # {file_path: index_time}

        # Create cache directory
        os.makedirs(self.config.cache_dir, exist_ok=True)

        # Initialize chat SDK for LLM responses
        chat_config = ChatConfig(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            show_stats=self.config.show_stats,
            use_local_llm=self.config.use_local_llm,
        )
        self.chat = ChatSDK(chat_config)

        # Initialize path validator
        self.path_validator = PathValidator(self.config.allowed_paths)

        self.log.debug("RAG SDK initialized")

    def _check_dependencies(self):
        """Check if required dependencies are available."""
        missing = []
        if PdfReader is None:
            missing.append("pypdf (or PyPDF2)")
        if SentenceTransformer is None:
            missing.append("sentence-transformers")
        if faiss is None:
            missing.append("faiss-cpu")

        if missing:
            error_msg = (
                f"\n❌ Error: Missing required RAG dependencies: {', '.join(missing)}\n\n"
                f"Please install the RAG dependencies:\n"
                f'  uv pip install -e ".[rag]"\n\n'
                f"Or install packages directly:\n"
                f"  uv pip install {' '.join(missing)}\n"
            )
            raise ImportError(error_msg)

    def _safe_open(self, file_path: str, mode="rb"):
        """
        Safely open file with path validation and O_NOFOLLOW to prevent symlink attacks.

        Args:
            file_path: Path to file
            mode: Open mode ('rb', 'r', 'w', 'wb', etc.)

        Returns:
            File handle

        Raises:
            PermissionError: If file is outside allowed paths or is a symlink
            IOError: If file cannot be opened
        """
        # Security check: Validate path against allowed directories
        if not self.path_validator.is_path_allowed(file_path):
            raise PermissionError(f"Access denied: {file_path} is not in allowed paths")

        import stat

        # Determine flags based on mode
        if "r" in mode and "+" not in mode:
            flags = os.O_RDONLY
        elif "w" in mode:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        elif "a" in mode:
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        else:
            flags = os.O_RDONLY

        # CRITICAL: Add O_NOFOLLOW to reject symlinks
        # This prevents TOCTOU attacks where symlinks are swapped
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        try:
            # Open file descriptor with O_NOFOLLOW
            fd = os.open(str(file_path), flags)
        except OSError as e:
            if e.errno == 40:  # ELOOP - too many symbolic links
                raise PermissionError(f"Symlinks not allowed: {file_path}")
            raise IOError(f"Cannot open file {file_path}: {e}")

        # Verify it's a regular file (not directory or special file)
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                os.close(fd)
                raise PermissionError(f"Not a regular file: {file_path}")

            # Convert to file object with appropriate mode
            mode_str = "rb" if "b" in mode else "r"
            if "w" in mode:
                mode_str = "wb" if "b" in mode else "w"
            elif "a" in mode:
                mode_str = "ab" if "b" in mode else "a"

            return os.fdopen(fd, mode_str)

        except Exception as _e:
            os.close(fd)
            raise

    def _get_cache_path(self, file_path: str) -> str:
        """
        Get cache file path for a document using content-based hashing.

        Uses SHA-256 hash of actual file content for cache key.
        This ensures proper cache invalidation even for:
        - Same-size file edits
        - Files modified within same second (low mtime resolution)
        - Content changes that preserve size

        Args:
            file_path: Path to the document

        Returns:
            Path to cache file
        """
        path = Path(file_path).absolute()

        try:
            # Hash the actual file CONTENT for reliable cache invalidation
            # This is more reliable than mtime + size
            hasher = hashlib.sha256()

            # Read file in chunks to handle large files efficiently
            # Use _safe_open to prevent symlink attacks
            with self._safe_open(path, "rb") as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)

            content_hash = hasher.hexdigest()

            # Include path in hash to avoid collisions between identical files
            path_hash = hashlib.sha256(str(path).encode()).hexdigest()[:16]
            cache_key = f"{path_hash}_{content_hash[:32]}"

            return os.path.join(self.config.cache_dir, f"{cache_key}.pkl")

        except (OSError, IOError) as e:
            # If file doesn't exist or can't be read, use path-based key
            # This will fail later during indexing anyway
            self.log.warning(f"Cannot read file for cache key: {e}")
            file_hash = hashlib.sha256(str(path).encode()).hexdigest()
            return os.path.join(self.config.cache_dir, f"{file_hash}_notfound.pkl")

    def _load_embedder(self):
        """Load embedding model via Lemonade server for hardware acceleration.

        Forces a fresh load with --ubatch-size 2048 to prevent llama.cpp issues
        after VLM processing. Must unload first since Lemonade skips reload
        if model already loaded.
        """
        if self.embedder is None:
            self.log.info(
                f"Loading embedding model via Lemonade: {self.config.embedding_model}"
            )

            from gaia.llm.lemonade_client import LemonadeClient

            if not hasattr(self, "llm_client") or self.llm_client is None:
                self.llm_client = LemonadeClient()

            # Force fresh load - must unload first
            try:
                self.llm_client.unload_model()
            except Exception:
                pass  # Ignore if nothing to unload

            try:
                self.llm_client.load_model(
                    self.config.embedding_model,
                    llamacpp_args="--ubatch-size 2048",
                )
                self.log.info("Loaded embedding model with ubatch-size=2048")
            except Exception as e:
                self.log.warning(f"Could not pre-load embedding model: {e}")

            self.embedder = self.llm_client
            self.use_lemonade_embeddings = True

            self.log.info("Using Lemonade server for hardware-accelerated embeddings")

    def _encode_texts(
        self, texts: List[str], show_progress: bool = False
    ) -> "np.ndarray":
        """
        Encode texts to embeddings using Lemonade server with batching and timing.

        Args:
            texts: List of text strings to encode
            show_progress: Whether to show progress

        Returns:
            numpy array of embeddings with shape (num_texts, embedding_dim)
        """

        # Batch embedding requests to avoid timeouts
        BATCH_SIZE = 25  # Smaller batches for reliability (25 chunks ~= 12KB text)
        all_embeddings = []

        total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
        total_start = time.time()

        for batch_idx in range(0, len(texts), BATCH_SIZE):
            batch_texts = texts[batch_idx : batch_idx + BATCH_SIZE]
            batch_num = (batch_idx // BATCH_SIZE) + 1

            batch_start = time.time()

            if show_progress or self.config.show_stats:
                self.log.info(
                    f"   📦 Embedding batch {batch_num}/{total_batches} ({len(batch_texts)} chunks)..."
                )

            # Call Lemonade embeddings API for this batch with retry
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    # Use longer timeout for embedding batches (180s = 3 minutes per batch)
                    response = self.embedder.embeddings(
                        batch_texts, model=self.config.embedding_model, timeout=180
                    )
                    break  # Success, exit retry loop
                except Exception as e:
                    if attempt < max_retries:
                        self.log.warning(
                            f"   ⚠️  Batch {batch_num} attempt {attempt + 1} failed, retrying: {e}"
                        )
                        time.sleep(2)  # Wait before retry
                    else:
                        self.log.error(
                            f"   ❌ Batch {batch_num} failed after {max_retries + 1} attempts"
                        )
                        raise

            batch_duration = time.time() - batch_start

            if show_progress or self.config.show_stats:
                chunks_per_sec = (
                    len(batch_texts) / batch_duration if batch_duration > 0 else 0
                )
                self.log.info(
                    f"   ✅ Batch {batch_num} complete in {batch_duration:.2f}s ({chunks_per_sec:.1f} chunks/sec)"
                )

            # Extract embeddings from response
            # Expected format: {"data": [{"embedding": [...]}, ...]}
            for item in response.get("data", []):
                embedding = item.get("embedding", [])
                all_embeddings.append(embedding)

        total_duration = time.time() - total_start
        if len(texts) > BATCH_SIZE:
            overall_rate = len(texts) / total_duration if total_duration > 0 else 0
            self.log.info(
                f"   🎯 Total embedding time: {total_duration:.2f}s ({overall_rate:.1f} chunks/sec, {total_batches} batches)"
            )

        # Convert to numpy array
        return np.array(all_embeddings, dtype=np.float32)

    def _get_file_type(self, file_path: str) -> str:
        """Detect file type from extension."""
        ext = Path(file_path).suffix.lower()
        return ext if ext else ".unknown"

    def _extract_text_from_pdf(self, pdf_path: str) -> tuple:
        """
        Extract text from PDF file with VLM for images (always enabled if available).

        Returns:
            (text, num_pages, metadata) tuple where metadata contains:
            - num_pages: int
            - vlm_pages: int (number of pages enhanced with VLM)
            - total_images: int (total images processed)
        """
        import time as time_module  # pylint: disable=reimported

        try:
            extract_start = time_module.time()
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)
            self.log.info(f"📄 Extracting text from {total_pages} pages...")

            # Initialize VLM client (auto-enabled if available)
            vlm = None
            vlm_available = False
            try:
                from gaia.llm.vlm_client import VLMClient
                from gaia.rag.pdf_utils import (
                    count_images_in_page,
                    extract_images_from_page_pymupdf,
                )

                vlm = VLMClient(
                    vlm_model=self.config.vlm_model, base_url=self.config.base_url
                )
                vlm_available = vlm.check_availability()

                if vlm_available and self.config.show_stats:
                    print("  🔍 VLM enabled: Will extract text from images")
                elif not vlm_available and self.config.show_stats:
                    print("  ⚠️  VLM not available - images will not be processed")
                    print("  📥 To enable VLM image extraction:")
                    print("     1. Open Lemonade Model Manager (http://localhost:8000)")
                    print(f"     2. Download model: {self.config.vlm_model}")

            except Exception as vlm_error:
                if self.config.show_stats:
                    print(f"  ⚠️  VLM initialization failed: {vlm_error}")
                self.log.warning(f"VLM initialization failed: {vlm_error}")
                vlm_available = False

            if self.config.show_stats:
                print(f"\n{'='*60}")
                print("  📄 COMPUTE INTENSIVE: PDF Text Extraction")
                print(f"  📊 Total pages: {total_pages}")
                print(f"  ⏱️  Estimated time: {total_pages * 0.2:.1f} seconds")
                if vlm_available:
                    print("  🖼️  VLM: Enabled for image text extraction")
                else:
                    print("  🖼️  VLM: Disabled (text-only extraction)")
                print(f"{'='*60}")

            pages_data = []
            vlm_pages_count = 0
            total_images_processed = 0

            for i, page in enumerate(reader.pages, 1):
                page_start = time_module.time()

                # Step 1: Extract text with pypdf
                pypdf_text = page.extract_text()

                # Step 2: Check for images
                has_imgs = False
                num_imgs = 0
                if vlm_available:
                    try:
                        has_imgs, num_imgs = count_images_in_page(page)
                    except Exception:  # pylint: disable=broad-except
                        pass

                # Step 3: Extract from images if present
                image_texts = []
                if has_imgs and vlm_available:
                    try:
                        images = extract_images_from_page_pymupdf(pdf_path, page_num=i)
                        if images:
                            image_texts = vlm.extract_from_page_images(
                                images, page_num=i
                            )
                            if image_texts:
                                vlm_pages_count += 1
                                total_images_processed += len(image_texts)
                    except Exception as img_error:
                        self.log.warning(
                            f"Image extraction failed on page {i}: {img_error}"
                        )

                # Step 4: Merge
                merged_text = self._merge_page_texts(
                    pypdf_text, image_texts, page_num=i
                )

                pages_data.append(
                    {
                        "page": i,
                        "text": merged_text,
                        "has_images": has_imgs,
                        "num_images": num_imgs,
                        "vlm_used": len(image_texts) > 0,
                    }
                )

                page_duration = time_module.time() - page_start

                if self.config.show_stats:
                    # Update progress with timing info
                    progress_pct = (i / total_pages) * 100
                    avg_time_per_page = (time_module.time() - extract_start) / i
                    eta = avg_time_per_page * (total_pages - i)
                    vlm_indicator = " 🖼️" if len(image_texts) > 0 else ""
                    print(
                        f"  📄 Page {i}/{total_pages} ({progress_pct:.0f}%){vlm_indicator} | "
                        f"⏱️  {page_duration:.2f}s | ETA: {eta:.1f}s" + " " * 10,
                        end="\r",
                        flush=True,
                    )

            # Cleanup VLM
            if vlm_available and vlm:
                try:
                    vlm.cleanup()
                except Exception:  # pylint: disable=broad-except
                    pass

            extract_duration = time_module.time() - extract_start

            # Build full text
            full_text = "\n\n".join(
                [f"[Page {p['page']}]\n{p['text']}" for p in pages_data]
            )

            if self.config.show_stats:
                print(
                    f"\n  ✅ Extracted {len(full_text):,} characters from {total_pages} pages"
                )
                print(
                    f"  ⏱️  Total extraction time: {extract_duration:.2f}s ({total_pages/extract_duration:.1f} pages/sec)"
                )
                print(f"  💾 Text size: {len(full_text) / 1024:.1f} KB")
                if vlm_pages_count > 0:
                    print(
                        f"  🖼️  VLM enhanced: {vlm_pages_count} pages, {total_images_processed} images"
                    )
                print(f"{'='*60}\n")

            self.log.info(
                f"📝 Extracted {len(full_text):,} characters in {extract_duration:.2f}s (VLM: {vlm_pages_count} pages)"
            )

            # Build metadata
            metadata = {
                "num_pages": total_pages,
                "vlm_pages": vlm_pages_count,
                "total_images": total_images_processed,
                "vlm_checked": True,  # Indicates this cache was created with VLM capability check
                "vlm_available": vlm_available,  # Whether VLM was actually available
            }

            return full_text, total_pages, metadata
        except Exception as e:
            self.log.error(f"Error reading PDF {pdf_path}: {e}")
            raise

    def _merge_page_texts(
        self, pypdf_text: str, image_texts: list, page_num: int
    ) -> str:
        """
        Merge pypdf text + VLM image texts.

        Args:
            pypdf_text: Text extracted by pypdf
            image_texts: List of dicts from VLM extraction (each has 'image_num' and 'text')
            page_num: Page number for logging

        Returns:
            Merged text with image content clearly marked
        """
        parts = []

        # Add pypdf text first (if any)
        if pypdf_text.strip():
            parts.append(pypdf_text.strip())

        # Add VLM-extracted image content (if any)
        if image_texts:
            parts.append("\n\n---\n")
            parts.append(f"[Page {page_num}]\n**Content Extracted from Images:**\n")

            for img_data in image_texts:
                parts.append(
                    f"\n[Page {page_num}] ### 🖼️ IMAGE {img_data['image_num']}\n\n"
                )

                # Clean up the VLM text for better structure
                image_text = img_data["text"].strip()

                # Ensure proper line breaks for list items (general pattern)
                # Look for patterns like "- text" or "* text" or "1. text"
                image_text = re.sub(r"(?<!\n)([•\-\*]|\d+\.)\s+", r"\n\1 ", image_text)

                # Add double newline after what looks like a heading
                # (line ending with colon or short line followed by longer text)
                lines = image_text.split("\n")
                formatted_lines = []
                for i, line in enumerate(lines):
                    formatted_lines.append(line)
                    # Add extra newline after lines that look like headers
                    if line.strip().endswith(":") and i < len(lines) - 1:
                        formatted_lines.append("")

                image_text = "\n".join(formatted_lines)

                parts.append(image_text)
                parts.append("\n\n")

        return "\n".join(parts)

    def _llm_based_chunking(
        self, text: str, chunk_size: int, overlap: int
    ) -> List[str]:
        """
        Use LLM to intelligently identify chunk boundaries.

        The LLM analyzes the text structure and suggests optimal split points
        that preserve semantic meaning and context.
        """
        self.log.info("🤖 Using LLM for intelligent text chunking...")

        chunks = []

        # Process text in segments (to handle long documents)
        # Approximate: 1 token ≈ 4 characters
        segment_size = chunk_size * 4 * 3  # Process 3 chunks worth at a time
        text_length = len(text)
        position = 0

        while position < text_length:
            # Get a segment to process
            segment_end = min(position + segment_size, text_length)
            segment = text[position:segment_end]

            # Ask LLM to identify good chunk boundaries
            prompt = """You are a document chunking expert. Your task is to identify optimal points to split the following text into chunks.

The text should be split into chunks of approximately {chunk_size} tokens (roughly {chunk_size * 4} characters each).

IMPORTANT RULES:
1. Keep semantic units together (complete thoughts, paragraphs, sections)
2. Never split in the middle of sentences
3. Preserve context - each chunk should be understandable on its own
4. Keep related information together (e.g., a heading with its content)
5. For lists, try to keep the list introduction with at least some items

Text to chunk:
---
{segment[:2000]}  # Limit prompt size
{"..." if len(segment) > 2000 else ""}
---

Please identify the CHARACTER POSITIONS where the text should be split.
Return ONLY a JSON array of split positions, like: [245, 502, 847]
These positions indicate where to split the text."""

            try:
                # Get LLM response
                response_data = self.llm_client.completions(
                    model=self.config.model,
                    prompt=prompt,
                    temperature=0.0,  # Low temperature for deterministic chunking
                    max_tokens=500,
                )
                response = response_data["choices"][0]["text"]

                # Parse the split positions
                import json

                split_positions = json.loads(response)

                # Create chunks based on LLM-suggested positions
                last_pos = 0
                for split_pos in split_positions:
                    if split_pos > last_pos and split_pos < len(segment):
                        chunk = segment[last_pos:split_pos].strip()
                        if chunk:
                            chunks.append(chunk)
                        last_pos = split_pos

                # Add remaining text
                if last_pos < len(segment):
                    chunk = segment[last_pos:].strip()
                    if chunk:
                        chunks.append(chunk)

            except Exception as e:
                self.log.warning(f"LLM chunking failed for segment: {e}")
                # Fall back to simple splitting for this segment
                segment_chunks = self._fallback_chunk_segment(segment, chunk_size)
                chunks.extend(segment_chunks)

            # Move to next segment with overlap
            position = segment_end - (overlap * 4)  # Convert overlap tokens to chars

        return chunks

    def _fallback_chunk_segment(self, text: str, chunk_size: int) -> List[str]:
        """Simple fallback chunking for a text segment."""
        chunks = []
        words = text.split()
        current_chunk = []
        current_size = 0

        for word in words:
            word_size = len(word) // 4  # Rough token estimate
            if current_size + word_size > chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = [word]
                current_size = word_size
            else:
                current_chunk.append(word)
                current_size += word_size

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    def _extract_text_from_text_file(self, file_path: str) -> str:
        """Extract text from text-based file (txt, md, etc.)."""
        try:
            encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
            text = None

            for encoding in encodings:
                try:
                    # Use _safe_open with binary mode, then decode
                    with self._safe_open(file_path, "rb") as f:
                        text = f.read().decode(encoding)
                    break
                except (UnicodeDecodeError, AttributeError):
                    continue

            if text is None:
                raise ValueError(
                    f"Failed to decode file: {file_path}\n"
                    f"Tried encodings: {', '.join(encodings)}\n"
                    "Suggestions:\n"
                    "  1. Convert the file to UTF-8 encoding\n"
                    "  2. Check if the file is corrupted\n"
                    "  3. Ensure the file is a text file (not binary)"
                )

            if self.config.show_stats:
                print(f"  ✅ Loaded text file ({len(text):,} characters)")

            self.log.info(f"📝 Extracted {len(text):,} characters from text file")
            return text.strip()
        except Exception as e:
            self.log.error(f"Error reading text file {file_path}: {e}")
            raise

    def _extract_text_from_csv(self, csv_path: str) -> str:
        """Extract text from CSV file."""
        try:
            import csv

            text_parts = []
            encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]

            for encoding in encodings:
                try:
                    # Use _safe_open with binary mode, then decode for csv.reader
                    from io import StringIO

                    with self._safe_open(csv_path, "rb") as f:
                        text = f.read().decode(encoding)
                        reader = csv.reader(StringIO(text))
                        rows = list(reader)

                        if not rows:
                            raise ValueError("CSV file is empty")

                        # Include header as context
                        if rows:
                            header = rows[0]
                            text_parts.append(f"Columns: {', '.join(header)}\n")

                        # Convert rows to readable text
                        for row in rows[1:]:
                            # Create a readable row format
                            row_text = []
                            for i, cell in enumerate(row):
                                if i < len(header):
                                    row_text.append(f"{header[i]}: {cell}")
                                else:
                                    row_text.append(cell)
                            text_parts.append(" | ".join(row_text))

                        text = "\n".join(text_parts)

                        if self.config.show_stats:
                            print(
                                f"  ✅ Loaded CSV file ({len(rows)} rows, {len(header)} columns)"
                            )

                        self.log.info(f"📊 Extracted {len(rows)} rows from CSV")
                        return text
                except UnicodeDecodeError:
                    continue

            raise ValueError(
                f"Failed to decode CSV file: {csv_path}\n"
                f"Tried encodings: {', '.join(encodings)}\n"
                "Suggestions:\n"
                "  1. Save the CSV file with UTF-8 encoding in Excel/LibreOffice\n"
                "  2. Check if the file is a valid CSV (not corrupted)\n"
                "  3. Try opening and re-saving in a text editor"
            )
        except Exception as e:
            self.log.error(f"Error reading CSV {csv_path}: {e}")
            raise

    def _extract_text_from_json(self, json_path: str) -> str:
        """Extract text from JSON file."""
        try:
            import json

            # Use _safe_open to prevent symlink attacks
            with self._safe_open(json_path, "rb") as f:
                data = json.load(f)

            # Convert JSON to readable text format
            def json_to_text(obj, indent=0):
                """Recursively convert JSON to readable text."""
                lines = []
                prefix = "  " * indent

                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if isinstance(value, (dict, list)):
                            lines.append(f"{prefix}{key}:")
                            lines.extend(json_to_text(value, indent + 1))
                        else:
                            lines.append(f"{prefix}{key}: {value}")
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        if isinstance(item, (dict, list)):
                            lines.append(f"{prefix}Item {i + 1}:")
                            lines.extend(json_to_text(item, indent + 1))
                        else:
                            lines.append(f"{prefix}- {item}")
                else:
                    lines.append(f"{prefix}{obj}")

                return lines

            text = "\n".join(json_to_text(data))

            if self.config.show_stats:
                print(f"  ✅ Loaded JSON file ({len(text):,} characters)")

            self.log.info(f"📝 Extracted {len(text):,} characters from JSON")
            return text
        except Exception as e:
            self.log.error(f"Error reading JSON {json_path}: {e}")
            raise

    def _extract_text_from_file(self, file_path: str) -> tuple:
        """
        Extract text from file based on type.

        Returns:
            (text, metadata_dict) tuple where metadata_dict contains:
            - num_pages: int (for PDFs) or None
            - vlm_pages: int (for PDFs with VLM) or None
            - total_images: int (for PDFs with VLM) or None
        """
        file_type = self._get_file_type(file_path)
        metadata = {"num_pages": None, "vlm_pages": None, "total_images": None}

        # PDF files
        if file_type == ".pdf":
            text, num_pages, pdf_metadata = self._extract_text_from_pdf(file_path)
            metadata["num_pages"] = num_pages
            metadata["vlm_pages"] = pdf_metadata.get("vlm_pages", 0)
            metadata["total_images"] = pdf_metadata.get("total_images", 0)
            return text, metadata

        # Text-based files
        elif file_type in [".txt", ".md", ".markdown", ".rst", ".log"]:
            return self._extract_text_from_text_file(file_path), metadata

        # CSV files
        elif file_type == ".csv":
            return self._extract_text_from_csv(file_path), metadata

        # JSON files
        elif file_type == ".json":
            return self._extract_text_from_json(file_path), metadata

        # Code files (treat as text for Q&A purposes)
        elif file_type in [
            # Backend languages
            ".py",
            ".pyw",  # Python
            ".java",  # Java
            ".cpp",
            ".cc",
            ".cxx",
            ".hpp",
            ".h",  # C++
            ".c",  # C
            ".cs",  # C#
            ".go",  # Go
            ".rs",  # Rust
            ".rb",  # Ruby
            ".php",  # PHP
            ".swift",  # Swift
            ".kt",
            ".kts",  # Kotlin
            ".scala",  # Scala
            # Web - JavaScript/TypeScript
            ".js",
            ".jsx",  # JavaScript
            ".ts",
            ".tsx",  # TypeScript
            ".mjs",
            ".cjs",  # JavaScript modules
            # Web - Frameworks
            ".vue",  # Vue.js
            ".svelte",  # Svelte
            ".astro",  # Astro
            # Web - Styling
            ".css",  # CSS
            ".scss",
            ".sass",  # Sass
            ".less",  # Less
            ".styl",
            ".stylus",  # Stylus
            # Web - Markup
            ".html",
            ".htm",  # HTML
            ".svg",  # SVG
            ".jsx",
            ".tsx",  # JSX/TSX (already listed but emphasizing)
            # Scripting
            ".sh",
            ".bash",  # Shell
            ".ps1",  # PowerShell
            ".r",
            ".R",  # R
            # Database
            ".sql",  # SQL
            # Configuration
            ".yaml",
            ".yml",  # YAML
            ".xml",  # XML
            ".toml",  # TOML
            ".ini",
            ".cfg",
            ".conf",  # Config files
            ".env",  # Environment files
            ".properties",  # Properties files
            # Build & Package
            ".gradle",  # Gradle
            ".cmake",  # CMake
            ".mk",
            ".make",  # Makefiles
            # Documentation
            ".rst",  # ReStructuredText
        ]:
            self.log.info(f"Indexing code/web file: {file_type}")
            return self._extract_text_from_text_file(file_path), metadata

        # Unknown file type - try as text
        else:
            self.log.warning(
                f"Unknown file type {file_type}, attempting to read as text"
            )
            return self._extract_text_from_text_file(file_path), metadata

    def _split_text_into_chunks(self, text: str) -> List[str]:
        """
        Split text into semantic chunks using LLM intelligence when available.

        Uses intelligent splitting that:
        - Leverages LLM to identify natural semantic boundaries (if available)
        - Falls back to structural heuristics if LLM is not available
        - Respects natural document boundaries (paragraphs, sections)
        - Keeps semantic units together
        - Maintains context with overlap

        This dramatically improves Q&A quality over naive word splitting.
        """
        self.log.info("📝 Splitting text into semantic chunks...")

        chunks = []
        chunk_size_tokens = self.config.chunk_size
        overlap_tokens = self.config.chunk_overlap

        # Try to use LLM for intelligent chunking if available
        if self.config.use_llm_chunking:
            # Ensure LLM client is initialized for chunking
            if self.llm_client is None:
                try:
                    from gaia.llm.lemonade_client import LemonadeClient

                    self.llm_client = LemonadeClient()
                    self.log.info("✅ Initialized LLM client for intelligent chunking")
                except Exception as e:
                    self.log.warning(
                        f"Failed to initialize LLM client for chunking: {e}"
                    )

            if self.llm_client is not None:
                try:
                    return self._llm_based_chunking(
                        text, chunk_size_tokens, overlap_tokens
                    )
                except Exception as e:
                    self.log.warning(
                        f"LLM chunking failed, falling back to heuristic: {e}"
                    )

        # Fall back to heuristic-based chunking

        # STEP 1: Identify and protect VLM content blocks as atomic units
        # VLM content starts with "[Page X] ### 🖼️ IMAGE" and continues until next image or end
        # We'll mark these sections to prevent splitting during paragraph processing
        vlm_pattern = r"\[Page \d+\] ### 🖼️ IMAGE \d+.*?(?=\[Page \d+\] ### 🖼️ IMAGE|\[Page \d+\]\n(?!### 🖼️)|\Z)"

        # Find all VLM image blocks and replace them with placeholders temporarily
        vlm_blocks = []
        protected_text = text
        for i, match in enumerate(re.finditer(vlm_pattern, text, re.DOTALL)):
            placeholder = f"<<<VLM_BLOCK_{i}>>>"
            vlm_blocks.append(
                {"placeholder": placeholder, "content": match.group(0).strip()}
            )
            protected_text = protected_text.replace(match.group(0), placeholder, 1)

        # STEP 2: Identify natural document boundaries
        # Look for markdown headers, section breaks, or significant whitespace
        # Use protected_text which has VLM blocks replaced with placeholders
        lines = protected_text.split("\n")
        sections = []
        current_section = []

        for i, line in enumerate(lines):
            # Detect section boundaries:
            # 1. Markdown headers (# Header, ## Header, ### Header)
            # 2. Lines that look like titles (short, possibly capitalized)
            # 3. Horizontal rules (---, ===, ___)
            # 4. Significant whitespace gaps

            is_boundary = False

            # Check for markdown headers
            if line.strip().startswith("#"):
                is_boundary = True
            # Check for horizontal rules
            elif re.match(r"^[\-=_]{3,}$", line.strip()):
                is_boundary = True
            # Check for lines that look like section titles (short, might be all caps)
            elif line.strip() and len(line.strip()) < 100 and i > 0:
                # If previous line was empty and next line exists and is not empty
                prev_empty = i > 0 and not lines[i - 1].strip()
                next_exists = i < len(lines) - 1
                next_not_empty = next_exists and lines[i + 1].strip()

                # Heuristic: likely a section header if surrounded by whitespace
                if prev_empty and next_not_empty:
                    # Additional check: does it look like a title?
                    # (starts with capital, no ending punctuation, relatively short)
                    if line.strip()[0].isupper() and not line.strip()[-1] in ".!?,;":
                        is_boundary = True

            if is_boundary and current_section:
                # Save the current section
                sections.append("\n".join(current_section))
                current_section = [line]
            else:
                current_section.append(line)

        # Don't forget the last section
        if current_section:
            sections.append("\n".join(current_section))

        # If we didn't find many sections, try paragraph-based splitting
        if len(sections) <= 3:
            # Split by double newlines (paragraphs)
            paragraphs = re.split(r"\n\s*\n", text)
            # Filter out empty paragraphs
            paragraphs = [p.strip() for p in paragraphs if p.strip()]
        else:
            paragraphs = sections

        # STEP 3: Mark paragraphs that are VLM content (should not be split)
        vlm_paragraphs = set()
        for idx, para in enumerate(paragraphs):
            # Check if this paragraph contains VLM markers
            if "### 🖼️ IMAGE" in para or "**Content Extracted from Images:**" in para:
                vlm_paragraphs.add(idx)
                self.log.debug(
                    f"Paragraph {idx} marked as VLM content (will keep atomic)"
                )

        current_chunk = []
        current_size = 0

        for idx, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue

            # Estimate tokens (rough: 1 token ≈ 4 characters)
            para_tokens = len(para) // 4

            # Check if this is VLM content - if so, keep it atomic
            is_vlm_content = idx in vlm_paragraphs

            # If single paragraph exceeds chunk size AND it's not VLM content, split by sentences
            if para_tokens > chunk_size_tokens and not is_vlm_content:
                # Split into sentences
                sentences = self._split_into_sentences(para)

                for sentence in sentences:
                    sentence_tokens = len(sentence) // 4

                    # If adding this sentence exceeds chunk size, save current chunk
                    if (
                        current_size + sentence_tokens > chunk_size_tokens
                        and current_chunk
                    ):
                        chunks.append(" ".join(current_chunk))

                        # Keep overlap (last few sentences)
                        overlap_text = " ".join(current_chunk)
                        overlap_actual = len(overlap_text) // 4
                        if overlap_actual > overlap_tokens:
                            # Trim to overlap size
                            current_chunk = self._get_last_n_tokens(
                                overlap_text, overlap_tokens
                            ).split()
                            current_size = overlap_tokens
                        else:
                            current_chunk = []
                            current_size = 0

                    current_chunk.append(sentence)
                    current_size += sentence_tokens
            else:
                # Small paragraph - try to keep intact
                # SPECIAL CASE: If this is VLM content, keep it atomic even if it exceeds chunk size
                if is_vlm_content:
                    if current_chunk:
                        # Save current chunk before adding VLM content
                        chunks.append(" ".join(current_chunk))
                        current_chunk = []
                        current_size = 0

                    # Add VLM content as its own chunk (atomic, not split)
                    chunks.append(para)
                    self.log.debug(
                        f"Added VLM content as atomic chunk ({para_tokens} tokens)"
                    )

                elif current_size + para_tokens > chunk_size_tokens and current_chunk:
                    # Save current chunk
                    chunks.append(" ".join(current_chunk))

                    # Keep overlap
                    overlap_text = " ".join(current_chunk)
                    current_chunk = self._get_last_n_tokens(
                        overlap_text, overlap_tokens
                    ).split()
                    current_size = len(" ".join(current_chunk)) // 4

                    current_chunk.append(para)
                    current_size += para_tokens
                else:
                    current_chunk.append(para)
                    current_size += para_tokens

        # Add final chunk
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        # STEP 4: Restore VLM blocks from placeholders
        if vlm_blocks:
            restored_chunks = []
            for chunk in chunks:
                restored_chunk = chunk
                # Replace placeholders with actual VLM content
                for vlm_block in vlm_blocks:
                    if vlm_block["placeholder"] in restored_chunk:
                        restored_chunk = restored_chunk.replace(
                            vlm_block["placeholder"], vlm_block["content"]
                        )
                restored_chunks.append(restored_chunk)
            chunks = restored_chunks

        if self.config.show_stats:
            avg_size = sum(len(c) for c in chunks) // len(chunks) if chunks else 0
            print(f"  ✅ Created {len(chunks)} semantic chunks (avg {avg_size} chars)")

        self.log.info(f"📦 Created {len(chunks)} semantic chunks")
        return chunks

    def _split_into_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences using simple heuristics.

        Better than word splitting, doesn't require NLTK dependency.
        """
        # Split on sentence endings followed by space and capital letter

        # Handle common abbreviations that shouldn't split
        text = text.replace("Dr.", "Dr<DOT>")
        text = text.replace("Mr.", "Mr<DOT>")
        text = text.replace("Mrs.", "Mrs<DOT>")
        text = text.replace("Ms.", "Ms<DOT>")
        text = text.replace("Prof.", "Prof<DOT>")
        text = text.replace("Sr.", "Sr<DOT>")
        text = text.replace("Jr.", "Jr<DOT>")
        text = text.replace("vs.", "vs<DOT>")
        text = text.replace("e.g.", "e<DOT>g<DOT>")
        text = text.replace("i.e.", "i<DOT>e<DOT>")
        text = text.replace("etc.", "etc<DOT>")

        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)

        # Restore abbreviations
        sentences = [s.replace("<DOT>", ".") for s in sentences]

        return [s.strip() for s in sentences if s.strip()]

    def _get_last_n_tokens(self, text: str, n_tokens: int) -> str:
        """Get approximately the last n tokens from text."""
        # Rough estimate: 1 token ≈ 4 characters
        target_chars = n_tokens * 4
        if len(text) <= target_chars:
            return text

        # Try to break on word boundary
        trimmed = text[-target_chars:]
        first_space = trimmed.find(" ")
        if first_space > 0:
            return trimmed[first_space + 1 :]
        return trimmed

    def _create_vector_index(self, chunks: List[str]) -> tuple:
        """Create FAISS vector index from chunks with progress reporting."""
        import time as time_module  # pylint: disable=reimported

        self._load_embedder()

        # Generate embeddings with detailed progress
        self.log.info(f"🔍 Generating embeddings for {len(chunks)} chunks...")

        if self.config.show_stats:
            print(f"\n{'='*60}")
            print("  🧠 COMPUTE INTENSIVE: Generating vector embeddings")
            print(f"  📊 Processing {len(chunks)} chunks")
            print(f"  ⏱️  Estimated time: {len(chunks) * 0.05:.1f} seconds")
            print(f"{'='*60}")

        embed_start = time_module.time()
        embeddings = self._encode_texts(chunks, show_progress=self.config.show_stats)
        embed_duration = time_module.time() - embed_start

        if self.config.show_stats:
            print(
                f"\n  ✅ Generated {embeddings.shape[0]} embeddings ({embeddings.shape[1]} dimensions)"
            )
            print(
                f"  ⏱️  Embedding time: {embed_duration:.2f}s ({len(chunks)/embed_duration:.1f} chunks/sec)"
            )

        # Create FAISS index
        self.log.info("🏗️  Building FAISS search index...")

        if self.config.show_stats:
            print("\n  🏗️  Building FAISS search index...")

        index_start = time_module.time()
        dimension = embeddings.shape[1]
        index = faiss.IndexFlatL2(dimension)
        # pylint: disable=no-value-for-parameter
        index.add(embeddings.astype("float32"))
        index_duration = time_module.time() - index_start

        if self.config.show_stats:
            print(
                f"  ✅ Built search index for {index.ntotal} vectors in {index_duration:.2f}s"
            )
            print(
                f"  💾 Memory: ~{(embeddings.nbytes / (1024**2)):.1f}MB for embeddings"
            )
            print(f"{'='*60}\n")

        self.log.info(
            f"📚 Index ready with {index.ntotal} vectors "
            f"(embed: {embed_duration:.2f}s, index: {index_duration:.2f}s)"
        )
        return index, chunks

    def remove_document(self, file_path: str) -> bool:
        """
        Remove a document from the index.

        Args:
            file_path: Path to document to remove

        Returns:
            True if removal succeeded, False otherwise
        """
        file_path = str(Path(file_path).absolute())

        if file_path not in self.indexed_files:
            self.log.warning(f"Document not indexed: {file_path}")
            return False

        try:
            # Get chunk indices for this file
            if file_path in self.file_to_chunk_indices:
                chunk_indices_set = set(self.file_to_chunk_indices[file_path])

                # OPTIMIZED: Rebuild all structures in one O(N) pass
                # This is much faster than deleting in a loop (which is O(N²))
                new_chunks = []
                new_chunk_to_file = {}
                new_file_to_chunk_indices = {}

                # Single pass through all chunks - O(N)
                for old_idx, chunk in enumerate(self.chunks):
                    # Skip chunks from file being removed
                    if old_idx in chunk_indices_set:
                        continue

                    new_idx = len(new_chunks)
                    new_chunks.append(chunk)

                    # Update chunk_to_file mapping
                    if old_idx in self.chunk_to_file:
                        file = self.chunk_to_file[old_idx]
                        new_chunk_to_file[new_idx] = file

                        # Update file_to_chunk_indices for this file
                        if file not in new_file_to_chunk_indices:
                            new_file_to_chunk_indices[file] = []
                        new_file_to_chunk_indices[file].append(new_idx)

                # Atomic replacement - all or nothing
                self.chunks = new_chunks
                self.chunk_to_file = new_chunk_to_file
                self.file_to_chunk_indices = new_file_to_chunk_indices

            # Remove from indexed files
            self.indexed_files.discard(file_path)

            # Clean up LRU tracking
            if file_path in self.file_access_times:
                del self.file_access_times[file_path]
            if file_path in self.file_index_times:
                del self.file_index_times[file_path]

            # Clean up cached per-file indices and embeddings
            if file_path in self.file_indices:
                del self.file_indices[file_path]
            if file_path in self.file_embeddings:
                del self.file_embeddings[file_path]

            # Clean up cached metadata
            if file_path in self.file_metadata:
                del self.file_metadata[file_path]

            # Rebuild index if chunks remain
            if self.chunks:
                self.index, self.chunks = self._create_vector_index(self.chunks)
                if self.config.show_stats:
                    print(f"✅ Removed {Path(file_path).name} from index")
                    print(
                        f"📊 Remaining: {len(self.indexed_files)} documents, {len(self.chunks)} chunks"
                    )
            else:
                self.index = None
                if self.config.show_stats:
                    print("✅ Removed last document from index")

            self.log.info(f"Successfully removed document: {file_path}")
            return True

        except Exception as e:
            self.log.error(f"Failed to remove document {file_path}: {e}")
            return False

    def reindex_document(self, file_path: str) -> Dict[str, Any]:
        """
        Reindex a document (remove old chunks and add new ones).

        Args:
            file_path: Path to document to reindex

        Returns:
            Dict with indexing results and statistics (same as index_document)
        """
        file_path = str(Path(file_path).absolute())

        # Remove old version if it exists
        if file_path in self.indexed_files:
            self.log.info(f"Removing old version of {file_path}")
            if not self.remove_document(file_path):
                return {
                    "success": False,
                    "error": "Failed to remove old version",
                    "file_name": Path(file_path).name,
                }

        # Index the new version
        self.log.info(f"Indexing new version of {file_path}")
        result = self.index_document(file_path)
        if result.get("success"):
            result["reindexed"] = True
        return result

    def _evict_lru_document(self) -> bool:
        """
        Evict the least recently used document to free memory.

        Returns:
            True if a document was evicted, False otherwise
        """
        if not self.config.enable_lru_eviction or not self.file_access_times:
            return False

        # Find LRU file (oldest access time)
        lru_file = min(self.file_access_times, key=self.file_access_times.get)

        if self.config.show_stats:
            print(
                f"📦 Memory limit reached, evicting LRU document: {Path(lru_file).name}"
            )

        # Remove the LRU document
        return self.remove_document(lru_file)

    def _check_memory_limits(self) -> None:
        """
        Check memory limits and evict documents if necessary.
        """
        # Check total chunks limit
        while (
            self.config.max_total_chunks > 0
            and len(self.chunks) > self.config.max_total_chunks
            and len(self.indexed_files) > 1
        ):  # Keep at least one file
            if not self._evict_lru_document():
                break

        # Check indexed files limit
        while (
            self.config.max_indexed_files > 0
            and len(self.indexed_files) > self.config.max_indexed_files
        ):
            if not self._evict_lru_document():
                break

    def index_document(self, file_path: str) -> Dict[str, Any]:
        """
        Index a document for retrieval.

        Supports:
        - Documents: PDF, TXT, MD, CSV, JSON
        - Backend Code: Python, Java, C/C++, Go, Rust, Ruby, PHP, Swift, Kotlin, Scala
        - Web Code: JavaScript/TypeScript, HTML, CSS/SCSS/SASS/LESS, Vue, Svelte, Astro
        - Config: YAML, XML, TOML, INI, ENV, Properties
        - Build: Gradle, CMake, Makefiles
        - Database: SQL

        Args:
            file_path: Path to document or code file

        Returns:
            Dict with indexing results and statistics:
            {
                "success": bool,
                "file_name": str,
                "file_type": str,
                "file_size_mb": float,
                "num_pages": int (for PDFs),
                "num_chunks": int,
                "total_indexed_files": int,
                "total_chunks": int,
                "error": str (if failed)
            }

        Raises:
            ValueError: If file_path is empty or file doesn't exist
        """
        # Validate input
        if not file_path or not file_path.strip():
            raise ValueError("File path cannot be empty")

        # Initialize stats dict
        stats = {
            "success": False,
            "file_name": Path(file_path).name if file_path else "",
            "file_type": "",
            "file_size_mb": 0.0,
            "num_pages": None,
            "vlm_pages": None,
            "total_images": None,
            "num_chunks": 0,
            "total_indexed_files": len(self.indexed_files),
            "total_chunks": len(self.chunks),
        }

        # Check if file exists before processing
        if not os.path.exists(file_path):
            self.log.error(f"File not found: {file_path}")
            if self.config.show_stats:
                print(f"❌ File not found: {file_path}")
                print("   Please check the file path and try again")
            stats["error"] = f"File not found: {file_path}"
            return stats

        # Check if file is empty (early validation to save time)
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        stats["file_size_mb"] = round(file_size_mb, 2)

        if file_size == 0:
            self.log.error(f"File is empty: {file_path}")
            if self.config.show_stats:
                print(f"❌ File is empty: {file_path}")
                print("   The file has no content to index")
            stats["error"] = "File is empty"
            return stats

        # Enforce maximum file size limit (prevent OOM)
        if file_size_mb > self.config.max_file_size_mb:
            error_msg = (
                f"File too large: {Path(file_path).name} ({file_size_mb:.1f}MB)\n"
                f"Maximum allowed: {self.config.max_file_size_mb}MB\n"
                "Suggestions:\n"
                "  1. Split the file into smaller documents\n"
                "  2. Increase max_file_size_mb in RAGConfig\n"
                "  3. Use a more powerful system with more RAM"
            )
            self.log.error(error_msg)
            if self.config.show_stats:
                print(f"❌ {error_msg}")
            stats["error"] = (
                f"File too large ({file_size_mb:.1f}MB > {self.config.max_file_size_mb}MB)"
            )
            return stats

        # Warn if file is large
        if file_size_mb > self.config.warn_file_size_mb:
            if self.config.show_stats:
                print(f"⚠️  Large file detected ({file_size_mb:.1f}MB)")
                print("   This may take 30-60 seconds to process...")
            self.log.warning(f"Processing large file: {file_size_mb:.1f}MB")

        # Convert to absolute path only after validation
        file_path = str(Path(file_path).absolute())

        # Get file type for logging
        file_type = self._get_file_type(file_path)
        stats["file_type"] = file_type
        stats["file_name"] = Path(file_path).name

        # Check if already indexed
        if file_path in self.indexed_files:
            if self.config.show_stats:
                print(f"📋 Document already indexed: {Path(file_path).name}")
            self.log.info(f"Document already indexed: {file_path}")
            stats["success"] = True
            stats["already_indexed"] = True
            stats["total_indexed_files"] = len(self.indexed_files)
            stats["total_chunks"] = len(self.chunks)
            return stats

        # Check cache - the cache key is based on file content hash
        cache_path = self._get_cache_path(file_path)

        # Also check for cached Markdown file with hash-based name
        # Extract the cache key from the pickle cache path to find matching MD file
        cache_filename = Path(cache_path).stem  # Remove .pkl extension
        md_cache_path = os.path.join(
            self.config.cache_dir, f"{cache_filename}_extracted.md"
        )

        if os.path.exists(cache_path):
            if self.config.show_stats:
                print(f"💾 Loading from cache: {Path(file_path).name}")
            self.log.info(f"📦 Loading cached index for: {file_path}")
            try:
                with open(cache_path, "rb") as f:
                    cached_data = pickle.load(f)
                    cached_chunks = cached_data["chunks"]
                    cached_full_text = cached_data.get(
                        "full_text", ""
                    )  # May not exist in old caches
                    cached_metadata = cached_data.get(
                        "metadata", {}
                    )  # May not exist in old caches

                    # Check if cache might be missing VLM content
                    # If metadata doesn't have VLM info, it's an old cache
                    if not cached_metadata.get("vlm_checked", False):
                        if self.config.show_stats:
                            print(
                                "  ⚠️  Cache might be missing image text (pre-VLM cache)"
                            )
                            print(
                                "     💡 Use /clear-cache to force re-extraction with VLM"
                            )

                    # Verify Markdown cache exists alongside pickle cache
                    if os.path.exists(md_cache_path):
                        self.log.info(
                            f"  ✅ Markdown cache also available: {md_cache_path}"
                        )

                    if self.config.show_stats:
                        vlm_info = ""
                        if cached_metadata.get("vlm_pages", 0) > 0:
                            vlm_info = f" (VLM: {cached_metadata['vlm_pages']} pages)"
                        print(
                            f"  ✅ Loaded {len(cached_chunks)} cached chunks{vlm_info}"
                        )

                    # Track chunk indices for this file
                    start_idx = len(self.chunks)
                    file_chunk_indices = []

                    if self.index is None:
                        # First document - use cached index directly
                        self.chunks = cached_chunks
                        # Track indices for all chunks (0 to len-1)
                        for i in range(len(cached_chunks)):
                            file_chunk_indices.append(i)
                            self.chunk_to_file[i] = file_path
                        self.index, self.chunks = self._create_vector_index(self.chunks)
                    else:
                        # Merge with existing chunks and recreate index
                        old_count = len(self.chunks)
                        self.chunks.extend(cached_chunks)
                        # Track indices for new chunks (start_idx to start_idx+len-1)
                        for i in range(len(cached_chunks)):
                            chunk_idx = start_idx + i
                            file_chunk_indices.append(chunk_idx)
                            self.chunk_to_file[chunk_idx] = file_path
                        if self.config.show_stats:
                            print(
                                f"  🔄 Rebuilding index ({old_count} + {len(cached_chunks)} = {len(self.chunks)} chunks)"
                            )
                        self.index, self.chunks = self._create_vector_index(self.chunks)

                    # Store file-to-chunk mapping
                    self.file_to_chunk_indices[file_path] = file_chunk_indices

                    # Restore metadata in memory
                    if cached_full_text or cached_metadata:
                        self.file_metadata[file_path] = {
                            "full_text": cached_full_text,
                            **cached_metadata,
                        }

                    self.indexed_files.add(file_path)
                    if self.config.show_stats:
                        print("  ✅ Successfully loaded from cache")

                    # Update stats for cache load
                    stats["success"] = True
                    stats["num_chunks"] = len(cached_chunks)
                    stats["num_pages"] = cached_metadata.get("num_pages")
                    stats["vlm_pages"] = cached_metadata.get("vlm_pages")
                    stats["total_images"] = cached_metadata.get("total_images")
                    stats["total_indexed_files"] = len(self.indexed_files)
                    stats["total_chunks"] = len(self.chunks)
                    stats["from_cache"] = True
                    return stats
            except Exception as e:
                self.log.warning(f"Cache load failed: {e}, reindexing")
                if self.config.show_stats:
                    print("  ⚠️  Cache loading failed, will reindex from scratch")

        # Extract and process document
        if self.config.show_stats:
            print(f"🚀 Starting to index: {Path(file_path).name} ({file_type})")
        self.log.info(f"📄 Indexing document: {file_path} ({file_type})")

        try:
            # Extract text based on file type
            text, file_metadata = self._extract_text_from_file(file_path)

            # Store metadata in stats if available (for PDFs)
            if file_metadata.get("num_pages"):
                stats["num_pages"] = file_metadata["num_pages"]
            if file_metadata.get("vlm_pages"):
                stats["vlm_pages"] = file_metadata["vlm_pages"]
            if file_metadata.get("total_images"):
                stats["total_images"] = file_metadata["total_images"]

            if not text.strip():
                error_msg = (
                    f"No text content found in {file_type} file: {Path(file_path).name}\n"
                    "Possible reasons:\n"
                    "  1. The file contains only images or non-text content\n"
                    "  2. The file is password-protected (PDFs)\n"
                    "  3. The file uses an unsupported format\n"
                    "  4. The text extraction failed\n"
                    "Try opening the file manually to verify it contains readable text"
                )
                stats["error"] = "No text content found"
                raise ValueError(error_msg)

            # Split into chunks
            new_chunks = self._split_text_into_chunks(text)

            # Track which chunks belong to this file
            file_chunk_indices = []
            start_idx = len(self.chunks)

            # Add to existing chunks or create new
            if self.chunks:
                old_count = len(self.chunks)
                self.chunks.extend(new_chunks)

                # Track the indices of chunks for this file
                for i in range(start_idx, start_idx + len(new_chunks)):
                    file_chunk_indices.append(i)
                    self.chunk_to_file[i] = file_path

                if self.config.show_stats:
                    print(
                        f"🔄 Rebuilding search index ({old_count} + {len(new_chunks)} = {len(self.chunks)} total chunks)"
                    )
                self.index, self.chunks = self._create_vector_index(self.chunks)
            else:
                # First file being indexed
                for i in range(len(new_chunks)):
                    file_chunk_indices.append(i)
                    self.chunk_to_file[i] = file_path

                if self.config.show_stats:
                    print("🏗️  Building initial search index...")
                self.index, self.chunks = self._create_vector_index(new_chunks)

            # Store the file-to-chunks mapping
            self.file_to_chunk_indices[file_path] = file_chunk_indices

            # Build and cache per-file FAISS index for fast file-specific searches
            if self.config.show_stats:
                print("🔍 Building per-file search index...")

            self._load_embedder()
            # Generate embeddings for this file's chunks only
            file_embeddings = self._encode_texts(new_chunks, show_progress=False)

            # Create FAISS index for this file
            dimension = file_embeddings.shape[1]
            file_index = faiss.IndexFlatL2(dimension)
            # pylint: disable=no-value-for-parameter
            file_index.add(file_embeddings.astype("float32"))

            # Cache the index and embeddings for this file
            self.file_indices[file_path] = file_index
            self.file_embeddings[file_path] = file_embeddings

            if self.config.show_stats:
                print(f"✅ Cached per-file index with {len(new_chunks)} chunks")

            # Cache the results for this specific document
            if self.config.show_stats:
                print("💾 Caching processed chunks...")
            cache_data = {
                "chunks": new_chunks,  # Cache only new chunks for this document
                "full_text": text,  # Cache full extracted text (for /dump)
                "metadata": file_metadata,  # Cache metadata (num_pages, vlm_pages, etc.)
            }
            with open(cache_path, "wb") as f:
                pickle.dump(cache_data, f)

            # Store metadata in memory for fast access
            self.file_metadata[file_path] = {
                "full_text": text,
                **file_metadata,  # num_pages, vlm_pages, total_images
            }

            # Auto-save markdown version to cache directory for easy access
            self._save_extracted_markdown(file_path, text, file_metadata)

            self.indexed_files.add(file_path)

            # Track index time for LRU
            current_time = time.time()
            self.file_index_times[file_path] = current_time
            self.file_access_times[file_path] = current_time

            # Check memory limits and evict if necessary
            self._check_memory_limits()

            if self.config.show_stats:
                print(f"✅ Successfully indexed {Path(file_path).name}")
                print(
                    f"📊 Total: {len(self.indexed_files)} documents, {len(self.chunks)} chunks"
                )
                if self.config.enable_lru_eviction:
                    print(
                        f"📈 Memory usage: {len(self.chunks)}/{self.config.max_total_chunks} chunks, "
                        f"{len(self.indexed_files)}/{self.config.max_indexed_files} files"
                    )

            self.log.info(f"✅ Successfully indexed {file_path}")

            # Update final stats
            stats["success"] = True
            stats["num_chunks"] = len(new_chunks)
            stats["total_indexed_files"] = len(self.indexed_files)
            stats["total_chunks"] = len(self.chunks)
            return stats

        except Exception as e:
            if self.config.show_stats:
                print(f"❌ Failed to index {Path(file_path).name}: {e}")
            self.log.error(f"Failed to index {file_path}: {e}")
            stats["error"] = str(e)
            return stats

    def _retrieve_chunks_from_file(self, query: str, file_path: str) -> tuple:
        """
        Retrieve relevant chunks from a specific file using cached per-file index.

        This is much faster than the global search because:
        1. Uses pre-computed embeddings (no re-encoding)
        2. Searches smaller, file-specific FAISS index
        3. No need to rebuild index on each query
        """
        if self.index is None or not self.chunks:
            raise ValueError("No documents indexed. Call index_document() first.")

        if file_path not in self.file_to_chunk_indices:
            raise ValueError(f"File not indexed: {file_path}")

        # Update access time for LRU tracking
        self.file_access_times[file_path] = time.time()

        # Get chunk indices for this file
        file_chunk_indices = self.file_to_chunk_indices[file_path]
        if not file_chunk_indices:
            return [], []

        # Get chunks for this file
        file_chunks = [self.chunks[i] for i in file_chunk_indices]

        # Use CACHED per-file index (this is the performance fix!)
        if file_path not in self.file_indices:
            # Index not cached - build it now (shouldn't happen normally)
            self.log.warning(f"Per-file index not cached for {file_path}, rebuilding")
            self._load_embedder()
            file_embeddings = self._encode_texts(file_chunks, show_progress=False)
            dimension = file_embeddings.shape[1]
            file_index = faiss.IndexFlatL2(dimension)
            # pylint: disable=no-value-for-parameter
            file_index.add(file_embeddings.astype("float32"))
            self.file_indices[file_path] = file_index
            self.file_embeddings[file_path] = file_embeddings
        else:
            # Use cached index - FAST!
            file_index = self.file_indices[file_path]

        # Encode query only once
        self._load_embedder()
        query_embedding = self._encode_texts([query], show_progress=False)

        # Search in cached file-specific index
        k = min(self.config.max_chunks, len(file_chunks))
        # pylint: disable=no-value-for-parameter
        distances, indices = file_index.search(query_embedding.astype("float32"), k)

        # Get matching chunks and scores
        retrieved_chunks = []
        scores = []
        for idx, dist in zip(indices[0], distances[0]):
            if idx < len(file_chunks):  # Safety check
                retrieved_chunks.append(file_chunks[idx])
                # Convert distance to similarity score
                score = 1.0 / (1.0 + float(dist))
                scores.append(score)

        if self.config.show_stats:
            print(
                f"  ✅ Found {len(retrieved_chunks)} relevant chunks from {Path(file_path).name} (using cached index)"
            )

        return retrieved_chunks, scores

    def _retrieve_chunks_with_metadata(self, query: str) -> tuple:
        """
        Retrieve chunks with metadata about their source files.

        Returns:
            (chunks, scores, metadata) tuple
        """
        chunks, scores = self._retrieve_chunks(query)

        # Build metadata for each chunk
        metadata = []
        for i, (chunk, score) in enumerate(zip(chunks, scores)):
            # Find which file this chunk came from
            chunk_idx = self.chunks.index(chunk) if chunk in self.chunks else -1
            source_file = self.chunk_to_file.get(chunk_idx, "unknown")

            metadata.append(
                {
                    "chunk_index": i + 1,
                    "source_file": (
                        Path(source_file).name
                        if source_file != "unknown"
                        else "unknown"
                    ),
                    "source_path": source_file,
                    "relevance_score": float(score),
                    "chunk_length": len(chunk),
                    "estimated_tokens": len(chunk) // 4,  # Rough token estimate
                }
            )

        return chunks, scores, metadata

    def _retrieve_chunks(self, query: str) -> tuple:
        """Retrieve relevant chunks for query."""
        if self.index is None or not self.chunks:
            raise ValueError("No documents indexed. Call index_document() first.")

        self._load_embedder()

        # Generate query embedding
        if self.config.show_stats:
            print(f"🔍 Searching through {len(self.chunks)} chunks...")
        self.log.debug(f"Encoding query: {query[:50]}...")
        query_embedding = self._encode_texts([query], show_progress=False)

        # Search for similar chunks
        k = min(self.config.max_chunks, len(self.chunks))
        if self.config.show_stats:
            print(f"  🎯 Finding {k} most relevant chunks...")
        # pylint: disable=no-value-for-parameter
        distances, indices = self.index.search(query_embedding.astype("float32"), k)

        # Get chunks and scores
        retrieved_chunks = [self.chunks[i] for i in indices[0]]
        # Convert distances to similarity scores (lower distance = higher similarity)
        scores = [1.0 / (1.0 + dist) for dist in distances[0]]

        if self.config.show_stats:
            print(
                f"  ✅ Retrieved {len(retrieved_chunks)} chunks (avg relevance: {sum(scores)/len(scores):.3f})"
            )

        self.log.debug(
            f"Retrieved {len(retrieved_chunks)} chunks with scores: {[f'{s:.3f}' for s in scores]}"
        )
        return retrieved_chunks, scores

    def query(self, question: str, include_metadata: bool = True) -> RAGResponse:
        """
        Query the indexed documents with enhanced metadata tracking.

        Args:
            question: Question to ask about the documents
            include_metadata: Whether to include detailed metadata in response

        Returns:
            RAGResponse with answer, retrieved chunks, and metadata
        """
        if self.index is None:
            raise ValueError("No documents indexed. Call index_document() first.")

        # Retrieve relevant chunks with metadata
        if include_metadata:
            chunks, scores, chunk_metadata = self._retrieve_chunks_with_metadata(
                question
            )
        else:
            chunks, scores = self._retrieve_chunks(question)
            chunk_metadata = None

        # Build context from retrieved chunks
        context = "\n\n".join(
            [f"Context {i+1}:\n{chunk}" for i, chunk in enumerate(chunks)]
        )

        # Create prompt
        prompt = f"""Based on the following context, please answer the question. If the answer is not in the context, say so.

Context:
{context}

Question: {question}

Answer:"""

        # Get LLM response
        response = self.chat.send(prompt)

        # Build query metadata
        query_metadata = None
        if include_metadata and chunk_metadata:
            # Get unique source files
            source_files = list(
                set(
                    m["source_file"]
                    for m in chunk_metadata
                    if m["source_file"] != "unknown"
                )
            )

            query_metadata = {
                "question": question,
                "num_chunks_retrieved": len(chunks),
                "source_files": source_files,
                "total_indexed_files": len(self.indexed_files),
                "total_indexed_chunks": len(self.chunks),
                "average_relevance_score": float(np.mean(scores)) if scores else 0.0,
                "max_relevance_score": float(max(scores)) if scores else 0.0,
                "min_relevance_score": float(min(scores)) if scores else 0.0,
            }

            # Collect source files list
            source_files_list = [m["source_file"] for m in chunk_metadata]
        else:
            source_files_list = None

        return RAGResponse(
            text=response.text,
            chunks=chunks,
            chunk_scores=scores,
            stats=response.stats,
            source_files=source_files_list,
            chunk_metadata=chunk_metadata,
            query_metadata=query_metadata,
        )

    def _save_extracted_markdown(
        self, file_path: str, text: str, metadata: Dict[str, Any]
    ):
        """
        Save extracted text as markdown file in cache directory.

        This creates a human-readable markdown version of the extracted text
        that can be used for /dump commands and debugging without re-extraction.
        Uses hash-based naming to match the pickle cache for consistency.

        Args:
            file_path: Path to original document
            text: Extracted text content
            metadata: File metadata (num_pages, vlm_pages, etc.)
        """
        try:
            from datetime import datetime

            # Calculate file hash for consistency with pickle cache
            path = Path(file_path).absolute()
            hasher = hashlib.sha256()
            with self._safe_open(path, "rb") as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            content_hash = hasher.hexdigest()

            # Use hash-based filename similar to pickle cache
            path_hash = hashlib.sha256(str(path).encode()).hexdigest()[:16]
            cache_key = f"{path_hash}_{content_hash[:32]}"
            md_filename = f"{cache_key}_extracted.md"
            md_path = os.path.join(self.config.cache_dir, md_filename)

            # Create markdown content with metadata header
            vlm_status = (
                "✅ Enabled"
                if metadata.get("vlm_available", False)
                else "❌ Not Available"
            )
            markdown_content = f"""# Extracted Text from {Path(file_path).name}

## Metadata
**Source File:** {file_path}
**File Hash (SHA-256):** {content_hash[:32]}
**Extraction Date:** {datetime.now().isoformat()}
**Pages:** {metadata.get('num_pages', 'N/A')}
**VLM Status:** {vlm_status}
**VLM Pages (with images):** {metadata.get('vlm_pages', 0)}
**Total Images Processed:** {metadata.get('total_images', 0)}

---

## Extracted Content
{text}
"""

            # Write markdown file
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            self.log.debug(f"Saved extracted markdown to {md_path}")

        except Exception as e:
            # Don't fail indexing if markdown save fails
            self.log.warning(
                f"Failed to save markdown cache for {Path(file_path).name}: {e}"
            )

    def clear_cache(self):
        """Clear the RAG cache."""
        import shutil

        if os.path.exists(self.config.cache_dir):
            shutil.rmtree(self.config.cache_dir)
            os.makedirs(self.config.cache_dir, exist_ok=True)
        self.log.info("Cache cleared")

    def get_status(self) -> Dict[str, Any]:
        """Get RAG system status."""
        return {
            "indexed_files": len(self.indexed_files),
            "total_chunks": len(self.chunks) if self.chunks else 0,
            "cache_dir": self.config.cache_dir,
            "embedding_model": self.config.embedding_model,
            "config": {
                "chunk_size": self.config.chunk_size,
                "chunk_overlap": self.config.chunk_overlap,
                "max_chunks": self.config.max_chunks,
            },
        }


def quick_rag(pdf_path: str, question: str, **kwargs) -> str:
    """
    Convenience function for quick RAG query.

    Args:
        pdf_path: Path to PDF file
        question: Question to ask
        **kwargs: Additional config parameters

    Returns:
        Answer text

    Raises:
        ValueError: If pdf_path is empty, question is empty, or file doesn't exist
    """
    # Validate inputs
    if not pdf_path or not pdf_path.strip():
        raise ValueError("PDF path cannot be empty")

    if not question or not question.strip():
        raise ValueError("Question cannot be empty")

    # Check if file exists
    if not os.path.exists(pdf_path):
        raise ValueError(f"PDF file not found: {pdf_path}")

    config = RAGConfig(**kwargs)
    rag = RAGSDK(config)

    result = rag.index_document(pdf_path)
    if not result.get("success"):
        error = result.get("error", "Unknown error")
        raise ValueError(f"Failed to index document: {pdf_path}. Error: {error}")

    response = rag.query(question)
    return response.text
