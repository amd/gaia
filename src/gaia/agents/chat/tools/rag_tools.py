# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
RAG Tools Mixin for Chat Agent.

Provides document retrieval, querying, and evaluation tools.
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def extract_page_from_chunk(chunk_text, chunk_index=-1, all_chunks=None):
    """
    Extract page number from chunk text or by looking at nearby chunks.

    Args:
        chunk_text: The chunk text to extract page from
        chunk_index: Global index of this chunk (for looking backwards)
        all_chunks: List of all chunks (for looking backwards)

    Returns:
        Page number as int, or None if not found
    """
    # Strategy 1: Try [Page X] format in this chunk
    match = re.search(r"\[Page (\d+)\]", chunk_text)
    if match:
        return int(match.group(1))

    # Strategy 2: Try (Page X) format
    match = re.search(r"\(Page (\d+)\)", chunk_text)
    if match:
        return int(match.group(1))

    # Strategy 3: Look backwards in previous chunks to find most recent page marker
    if chunk_index >= 0 and all_chunks:
        for prev_idx in range(chunk_index - 1, max(-1, chunk_index - 5), -1):
            if prev_idx < len(all_chunks):
                prev_chunk = all_chunks[prev_idx]
                match = re.search(r"\[Page (\d+)\]", prev_chunk)
                if match:
                    return int(match.group(1))

    return None


class RAGToolsMixin:
    """
    Mixin providing RAG and document query tools.

    Tools provided:
    - query_documents: Semantic search across all indexed documents
    - query_specific_file: Semantic search in one specific file
    - search_indexed_chunks: Exact text search in RAG indexed chunks (in-memory)
    - evaluate_retrieval: Evaluate if retrieved information is sufficient
    - index_document: Add document to RAG index
    - index_directory: Index all files in a directory
    - list_indexed_documents: List currently indexed documents
    - summarize_document: Generate document summaries
    - rag_status: Get RAG system status

    Note: File system search tools (search_file, search_directory, search_file_content)
    are provided by FileSearchToolsMixin from gaia.agents.tools.file_tools
    """

    def register_rag_tools(self) -> None:
        """Register RAG-related tools."""
        from gaia.agents.base.tools import tool

        @tool(
            atomic=True,
            name="query_documents",
            description="Query indexed documents using RAG to find relevant information. Returns document chunks that the agent should use to answer the user's question.",
            parameters={
                "query": {
                    "type": "str",
                    "description": "The question or query to search for in documents",
                    "required": True,
                }
            },
        )
        def query_documents(
            query: str, debug: bool = False  # pylint: disable=unused-argument
        ) -> Dict[str, Any]:
            """
            Query indexed documents with improved search key generation.

            Returns chunks for the agent to use in formulating an answer,
            rather than generating the answer directly. This maintains proper
            integration with the agent's conversation flow.
            """
            try:
                # Check if RAG is initialized and has documents
                if not self.rag or not self.rag.index or len(self.rag.chunks) == 0:
                    return {
                        "status": "no_documents",
                        "message": "No documents are indexed. Answer the user's question using your general knowledge.",
                        "instruction": (
                            "There are no documents indexed to search. "
                            "Please answer the user's question using your general knowledge instead. "
                            "Do NOT apologize or say you can't help - just answer naturally."
                        ),
                    }

                # Generate multiple search keys for better retrieval
                search_keys = self._generate_search_keys(query)
                logger.info(f"Generated {len(search_keys)} search keys for query")

                # Try each search key and aggregate results
                all_chunks = []
                all_scores = []

                # Debug information collection
                debug_info = (
                    {
                        "search_keys": search_keys,
                        "embedding_retrieval": [],
                        "keyword_retrieval": [],
                        "total_chunks_before_dedup": 0,
                        "total_chunks_after_dedup": 0,
                    }
                    if hasattr(self, "debug") and self.debug
                    else None
                )

                # First, use embedding-based retrieval
                for search_key in search_keys:
                    try:
                        # Use RAG to retrieve chunks
                        # pylint: disable=protected-access
                        chunks, scores = self.rag._retrieve_chunks(search_key)
                        if chunks:
                            all_chunks.extend(chunks)
                            all_scores.extend(scores)

                            # Capture debug info with full chunk content and indices
                            if debug_info:
                                # Get global indices for these chunks
                                chunk_global_indices = []
                                for chunk in chunks[:5]:
                                    try:
                                        idx = self.rag.chunks.index(chunk)
                                        chunk_global_indices.append(idx)
                                    except ValueError:
                                        chunk_global_indices.append(-1)

                                debug_info["embedding_retrieval"].append(
                                    {
                                        "search_key": search_key,
                                        "chunks_found": len(chunks),
                                        "chunk_indices": chunk_global_indices,  # Which chunks
                                        "scores": [
                                            float(s) for s in scores[:5]
                                        ],  # Top 5 scores
                                        "top_chunk_preview": (
                                            chunks[0][:200] if chunks else None
                                        ),
                                        "all_chunks": (
                                            [
                                                {
                                                    "global_index": (
                                                        chunk_global_indices[i]
                                                        if i < len(chunk_global_indices)
                                                        else -1
                                                    ),
                                                    "content": chunk[
                                                        :500
                                                    ],  # First 500 chars
                                                    "score": (
                                                        float(scores[i])
                                                        if i < len(scores)
                                                        else 0
                                                    ),
                                                    "full_length": len(chunk),
                                                }
                                                for i, chunk in enumerate(
                                                    chunks[:5]
                                                )  # Top 5 chunks
                                            ]
                                            if chunks
                                            else []
                                        ),
                                    }
                                )
                                logger.info(
                                    f"[DEBUG] Embedding search '{search_key}': found {len(chunks)} chunks (indices: {chunk_global_indices})"
                                )
                    except Exception as e:
                        logger.warning(f"Search key '{search_key}' failed: {e}")
                        if debug_info:
                            debug_info["embedding_retrieval"].append(
                                {"search_key": search_key, "error": str(e)}
                            )
                        continue

                # HYBRID SEARCH: Boost scores of chunks containing keywords
                # Instead of creating new text snippets, we boost the scores of existing chunks
                query_lower = query.lower()

                # Identify important terms (not common words)

                query_words = re.findall(r"\b[a-z]+\b", query_lower)
                stop_words = {
                    "the",
                    "is",
                    "what",
                    "of",
                    "and",
                    "a",
                    "an",
                    "in",
                    "to",
                    "for",
                }
                important_terms = [
                    w for w in query_words if w not in stop_words and len(w) > 2
                ]

                keyword_boost_info = []

                if important_terms:
                    # Check each indexed chunk for keyword matches
                    for chunk_idx, chunk_text in enumerate(self.rag.chunks):
                        chunk_lower = chunk_text.lower()

                        # Count matching terms in this chunk (whole word matching)
                        matching_terms = []
                        for term in important_terms:
                            # Use word boundary regex for whole-word matching
                            if re.search(r"\b" + re.escape(term) + r"\b", chunk_lower):
                                matching_terms.append(term)

                        if matching_terms:
                            # Calculate boost score based on match ratio
                            match_ratio = (
                                len(matching_terms) / len(important_terms)
                                if important_terms
                                else 0
                            )
                            boost_score = 0.6 + (0.2 * match_ratio)  # Range: 0.6-0.8

                            # Add this chunk with boosted score if not already in all_chunks
                            if chunk_text not in all_chunks:
                                all_chunks.append(chunk_text)
                                all_scores.append(boost_score)

                                # Get source file for this chunk
                                source_file = self.rag.chunk_to_file.get(
                                    chunk_idx, "Unknown"
                                )

                                keyword_boost_info.append(
                                    {
                                        "chunk_index": chunk_idx,
                                        "source_file": (
                                            Path(source_file).name
                                            if source_file != "Unknown"
                                            else "Unknown"
                                        ),
                                        "matching_terms": matching_terms,
                                        "boost_score": boost_score,
                                        "match_ratio": match_ratio,
                                    }
                                )

                                # Limit boosted chunks
                                if len(keyword_boost_info) >= 5:
                                    break

                # Capture debug info for keyword boosting
                if debug_info and keyword_boost_info:
                    debug_info["keyword_retrieval"].append(
                        {
                            "chunks_boosted": len(keyword_boost_info),
                            "boosted_chunks": keyword_boost_info,
                        }
                    )
                    logger.info(
                        f"[DEBUG] Keyword search: boosted {len(keyword_boost_info)} chunks"
                    )

                # Update debug info before deduplication - track which chunks before dedup
                if debug_info:
                    debug_info["total_chunks_before_dedup"] = len(all_chunks)
                    # Show which chunks were found before deduplication
                    all_chunk_indices = []
                    for chunk in all_chunks:
                        try:
                            idx = self.rag.chunks.index(chunk)
                            all_chunk_indices.append(idx)
                        except ValueError:
                            all_chunk_indices.append(
                                "keyword_context"
                            )  # Keyword match, not a full chunk
                    debug_info["chunks_before_dedup_indices"] = all_chunk_indices
                    debug_info["deduplication_note"] = (
                        "Removes chunks that appear in both embedding and keyword results, keeping the one with higher score"
                    )

                if not all_chunks:
                    result = {
                        "status": "success",
                        "message": "No relevant information found in indexed documents.",
                        "chunks": [],
                        "num_chunks": 0,
                        "relevance_scores": [],
                        "instruction": "Inform the user that no relevant information was found in the documents for their query.",
                    }
                    if debug_info:
                        result["debug_info"] = debug_info
                    return result

                # Remove duplicate chunks and keep best scores
                # OPTIMIZED: Use hash-based deduplication instead of full text comparison
                unique_chunks = {}  # {chunk_hash: (chunk_text, score)}

                for chunk, score in zip(all_chunks, all_scores):
                    # Use hash for O(1) lookup instead of O(N) string comparison
                    chunk_hash = hash(chunk)

                    if (
                        chunk_hash not in unique_chunks
                        or unique_chunks[chunk_hash][1] < score
                    ):
                        unique_chunks[chunk_hash] = (chunk, score)

                # Update debug info after deduplication - track which chunks remain
                if debug_info:
                    debug_info["total_chunks_after_dedup"] = len(unique_chunks)
                    debug_info["duplicates_removed"] = debug_info[
                        "total_chunks_before_dedup"
                    ] - len(unique_chunks)
                    # Show which chunks remain after deduplication
                    dedup_chunk_indices = []
                    for chunk_text, score in unique_chunks.values():
                        try:
                            idx = self.rag.chunks.index(chunk_text)
                            dedup_chunk_indices.append(idx)
                        except ValueError:
                            dedup_chunk_indices.append("keyword_context")
                    debug_info["chunks_after_dedup_indices"] = dedup_chunk_indices

                # Sort by score and take top chunks
                sorted_items = sorted(
                    unique_chunks.values(), key=lambda x: x[1], reverse=True
                )

                # Adaptive max_chunks: use more chunks for larger documents
                # With 32K context, we can afford to retrieve more chunks for better coverage
                total_chunks = len(self.rag.chunks)
                if total_chunks > 200:
                    adaptive_max = min(
                        25, self.max_chunks * 5
                    )  # Up to 25 chunks for very large docs (200+ pages)
                elif total_chunks > 100:
                    adaptive_max = min(
                        20, self.max_chunks * 4
                    )  # Up to 20 chunks for large docs (100+ pages)
                elif total_chunks > 50:
                    adaptive_max = min(
                        10, self.max_chunks * 2
                    )  # Up to 10 chunks for medium docs
                else:
                    adaptive_max = self.max_chunks  # Default (5) for small docs

                top_chunks = [chunk for chunk, score in sorted_items[:adaptive_max]]
                top_scores = [score for chunk, score in sorted_items[:adaptive_max]]

                # Find the actual chunk indices from the RAG system
                chunk_indices = []
                for chunk in top_chunks:
                    # Find this chunk's index in the global chunks list
                    try:
                        idx = self.rag.chunks.index(chunk)
                        chunk_indices.append(idx)
                    except ValueError:
                        chunk_indices.append(-1)  # Not found

                # Format chunks with context markers for better readability
                formatted_chunks = []
                for i, chunk in enumerate(top_chunks):
                    formatted_chunks.append(
                        {
                            "chunk_id": i + 1,  # Sequential for display
                            "page": extract_page_from_chunk(
                                chunk,
                                chunk_indices[i] if i < len(chunk_indices) else -1,
                                self.rag.chunks,
                            ),  # PDF page (with lookback)
                            "content": chunk,
                            "relevance_score": float(top_scores[i]),
                            "_debug_chunk_index": (
                                chunk_indices[i] if i < len(chunk_indices) else -1
                            ),  # Internal index (for debugging)
                        }
                    )

                # Update debug info with final chunks
                if debug_info:
                    debug_info["final_chunks_returned"] = len(top_chunks)
                    debug_info["score_distribution"] = {
                        "max": float(max(top_scores)) if top_scores else 0,
                        "min": float(min(top_scores)) if top_scores else 0,
                        "avg": (
                            float(sum(top_scores) / len(top_scores))
                            if top_scores
                            else 0
                        ),
                    }
                    # Add preview of returned chunks
                    debug_info["chunks_preview"] = [
                        {
                            "chunk_id": c["chunk_id"],
                            "score": c["relevance_score"],
                            "preview": (
                                c["content"][:100] + "..."
                                if len(c["content"]) > 100
                                else c["content"]
                            ),
                        }
                        for c in formatted_chunks[:3]  # Show first 3 chunks
                    ]

                # Return chunks for agent to use in answer generation
                result = {
                    "status": "success",
                    "message": f"Found {len(top_chunks)} relevant document chunks",
                    "chunks": formatted_chunks,
                    "num_chunks": len(top_chunks),
                    "search_keys_used": search_keys,
                    "source_files": (
                        list(
                            set(
                                [
                                    self.rag.chunk_to_file.get(i, "Unknown")
                                    for i in range(len(self.rag.chunks))
                                ]
                            )
                        )
                        if hasattr(self.rag, "chunk_to_file")
                        else []
                    ),
                    "instruction": "Use the provided document chunks to answer the user's question.\n\nCRITICAL CITATION REQUIREMENT:\nYour answer MUST start with: 'According to [document name], page X:' where X is the page number from each chunk's 'page' field.\n\nExample: If chunk has 'page': 2, say 'According to document.pdf, page 2:'\nIf info from pages 2 and 5, say 'According to document.pdf, pages 2 and 5:'",
                }

                # Add debug info to result if debug mode is enabled
                if debug_info:
                    result["debug_info"] = debug_info
                    logger.info(
                        f"[DEBUG] Query complete: {debug_info['final_chunks_returned']} chunks returned from {debug_info['total_chunks_before_dedup']} total ({debug_info['duplicates_removed']} duplicates removed)"
                    )

                return result
            except Exception as e:
                logger.error(f"Error in query_documents: {e}")
                # Graceful degradation - inform agent to use general knowledge
                return {
                    "status": "fallback",
                    "message": "Document search is temporarily unavailable",
                    "error": str(e),
                    "instruction": (
                        "The document search system encountered an error. "
                        "Please answer the user's question using your general knowledge "
                        "and inform them that document search is unavailable."
                    ),
                    "fallback_response": (
                        "I apologize, but I'm currently unable to search the indexed documents. "
                        "Let me try to answer your question based on my general knowledge instead."
                    ),
                }

        @tool(
            atomic=True,
            name="query_specific_file",
            description="Query a SPECIFIC file by name for targeted, fast retrieval. Use when user mentions a specific file or needs information from one document.",
            parameters={
                "file_path": {
                    "type": "str",
                    "description": "Name or path of the specific file to query (e.g., 'document.pdf' or full path)",
                    "required": True,
                },
                "query": {
                    "type": "str",
                    "description": "Question to ask about this specific file",
                    "required": True,
                },
            },
        )
        def query_specific_file(file_path: str, query: str) -> Dict[str, Any]:
            """
            Query a specific file for fast, targeted retrieval.

            This is faster than query_documents because it searches only one file.
            """
            try:
                # Debug information collection
                debug_info = (
                    {
                        "tool": "query_specific_file",
                        "file_path": file_path,
                        "query": query,
                        "search_keys": [],
                        "embedding_retrieval": [],
                        "keyword_retrieval": [],
                        "total_chunks_before_dedup": 0,
                        "total_chunks_after_dedup": 0,
                    }
                    if hasattr(self, "debug") and self.debug
                    else None
                )

                # Find the file in indexed files
                matching_files = [
                    f for f in self.rag.indexed_files if file_path in str(f)
                ]

                if not matching_files:
                    return {
                        "status": "error",
                        "error": f"File '{file_path}' not found in indexed documents. Use search_files to find it first.",
                    }

                # For now, use the first match
                # TODO: Let user disambiguate if multiple matches
                target_file = matching_files[0]

                # Generate search keys for better retrieval
                search_keys = self._generate_search_keys(query)

                if debug_info:
                    debug_info["search_keys"] = search_keys
                    debug_info["target_file"] = str(target_file)
                    logger.info(
                        f"[DEBUG] query_specific_file: Searching '{Path(target_file).name}' with {len(search_keys)} search keys"
                    )

                # Use per-file retrieval for efficient search
                all_chunks = []
                all_scores = []

                # Add fields for hybrid search debug info
                if debug_info:
                    debug_info["embedding_retrieval"] = []
                    debug_info["keyword_retrieval"] = []

                # First, do embedding-based retrieval
                for search_key in search_keys:
                    try:
                        # Use the new per-file retrieval method
                        # pylint: disable=protected-access
                        chunks, scores = self.rag._retrieve_chunks_from_file(
                            search_key, str(target_file)
                        )
                        if chunks:
                            all_chunks.extend(chunks)
                            all_scores.extend(scores)

                            # Capture debug info with full chunk content and indices
                            if debug_info:
                                # Get global indices for these chunks
                                chunk_global_indices = []
                                for chunk in chunks[:5]:
                                    try:
                                        idx = self.rag.chunks.index(chunk)
                                        chunk_global_indices.append(idx)
                                    except ValueError:
                                        chunk_global_indices.append(-1)

                                debug_info["embedding_retrieval"].append(
                                    {
                                        "search_key": search_key,
                                        "chunks_found": len(chunks),
                                        "chunk_indices": chunk_global_indices,  # Which chunks
                                        "scores": [
                                            float(s) for s in scores[:5]
                                        ],  # Top 5 scores
                                        "top_chunk_preview": (
                                            chunks[0][:100] if chunks else None
                                        ),
                                        "all_chunks": (
                                            [
                                                {
                                                    "global_index": (
                                                        chunk_global_indices[i]
                                                        if i < len(chunk_global_indices)
                                                        else -1
                                                    ),
                                                    "content": chunk[
                                                        :500
                                                    ],  # First 500 chars
                                                    "score": (
                                                        float(scores[i])
                                                        if i < len(scores)
                                                        else 0
                                                    ),
                                                    "full_length": len(chunk),
                                                }
                                                for i, chunk in enumerate(
                                                    chunks[:5]
                                                )  # Top 5 chunks
                                            ]
                                            if chunks
                                            else []
                                        ),
                                    }
                                )
                    except Exception as e:
                        logger.warning(f"Search key '{search_key}' failed: {e}")
                        if debug_info:
                            debug_info["embedding_retrieval"].append(
                                {"search_key": search_key, "error": str(e)}
                            )

                # HYBRID SEARCH: Boost scores of chunks containing keywords
                # Instead of creating new text snippets, we boost the scores of existing chunks
                if (
                    str(target_file) in self.rag.file_metadata
                    and "full_text" in self.rag.file_metadata[str(target_file)]
                ):
                    query_lower = query.lower()

                    # Identify important terms (not common words)

                    query_words = re.findall(r"\b[a-z]+\b", query_lower)
                    # Filter out common words
                    stop_words = {
                        "the",
                        "is",
                        "what",
                        "of",
                        "and",
                        "a",
                        "an",
                        "in",
                        "to",
                        "for",
                    }
                    important_terms = [
                        w for w in query_words if w not in stop_words and len(w) > 2
                    ]

                    if important_terms:
                        file_keyword_info = []

                        # Check each chunk from this file for keyword matches
                        file_chunk_indices = self.rag.file_to_chunk_indices.get(
                            str(target_file), []
                        )

                        for chunk_idx in file_chunk_indices:
                            if chunk_idx < len(self.rag.chunks):
                                chunk_text = self.rag.chunks[chunk_idx].lower()

                                # Count matching terms in this chunk (whole word matching)
                                matching_terms = []
                                for term in important_terms:
                                    # Use word boundary regex for whole-word matching
                                    if re.search(
                                        r"\b" + re.escape(term) + r"\b", chunk_text
                                    ):
                                        matching_terms.append(term)

                                if matching_terms:
                                    # Calculate boost score based on match ratio
                                    match_ratio = (
                                        len(matching_terms) / len(important_terms)
                                        if important_terms
                                        else 0
                                    )
                                    boost_score = 0.6 + (
                                        0.2 * match_ratio
                                    )  # Range: 0.6-0.8

                                    # Add this chunk with boosted score if not already in all_chunks
                                    chunk_content = self.rag.chunks[chunk_idx]
                                    if chunk_content not in all_chunks:
                                        all_chunks.append(chunk_content)
                                        all_scores.append(boost_score)

                                        file_keyword_info.append(
                                            {
                                                "chunk_index": chunk_idx,
                                                "matching_terms": matching_terms,
                                                "boost_score": boost_score,
                                                "match_ratio": match_ratio,
                                            }
                                        )

                                        # Limit boosted chunks
                                        if len(file_keyword_info) >= 5:
                                            break

                        # Capture debug info for keyword search
                        if debug_info and file_keyword_info:
                            debug_info["keyword_retrieval"].append(
                                {
                                    "file": Path(target_file).name,
                                    "chunks_boosted": len(file_keyword_info),
                                    "boosted_chunks": file_keyword_info[
                                        :5
                                    ],  # Show first 5 boosted chunks
                                }
                            )
                            logger.info(
                                f"[DEBUG] Keyword search in {Path(target_file).name}: boosted {len(file_keyword_info)} chunks"
                            )

                # Update debug info before deduplication - track which chunks before dedup
                if debug_info:
                    debug_info["total_chunks_before_dedup"] = len(all_chunks)
                    # Show which chunks were found before deduplication
                    all_chunk_indices = []
                    for chunk in all_chunks:
                        try:
                            idx = self.rag.chunks.index(chunk)
                            all_chunk_indices.append(idx)
                        except ValueError:
                            all_chunk_indices.append(
                                "keyword_context"
                            )  # Keyword match context, not a full indexed chunk
                    debug_info["chunks_before_dedup_indices"] = all_chunk_indices
                    debug_info["deduplication_note"] = (
                        "Removes duplicate chunks found by both embedding and keyword search, keeping the version with higher score"
                    )

                if not all_chunks:
                    result = {
                        "status": "success",
                        "message": f"No relevant information found in {Path(target_file).name}",
                        "chunks": [],
                        "file": str(target_file),
                    }
                    if debug_info:
                        result["debug_info"] = debug_info
                    return result

                # Remove duplicates and sort using hash-based deduplication
                unique_chunks = {}  # {chunk_hash: (chunk_text, score)}

                for chunk, score in zip(all_chunks, all_scores):
                    chunk_hash = hash(chunk)
                    if (
                        chunk_hash not in unique_chunks
                        or unique_chunks[chunk_hash][1] < score
                    ):
                        unique_chunks[chunk_hash] = (chunk, score)

                # Update debug info after deduplication - track which chunks remain
                if debug_info:
                    debug_info["total_chunks_after_dedup"] = len(unique_chunks)
                    debug_info["duplicates_removed"] = debug_info[
                        "total_chunks_before_dedup"
                    ] - len(unique_chunks)
                    # Show which chunks remain after deduplication
                    dedup_chunk_indices = []
                    for chunk_text, score in unique_chunks.values():
                        try:
                            idx = self.rag.chunks.index(chunk_text)
                            dedup_chunk_indices.append(idx)
                        except ValueError:
                            dedup_chunk_indices.append("keyword_context")
                    debug_info["chunks_after_dedup_indices"] = dedup_chunk_indices

                sorted_items = sorted(
                    unique_chunks.values(), key=lambda x: x[1], reverse=True
                )

                # Adaptive max_chunks: use more chunks for larger documents
                # With 32K context, we can afford to retrieve more chunks for better coverage
                total_chunks = len(self.rag.chunks)
                if total_chunks > 200:
                    adaptive_max = min(
                        25, self.max_chunks * 5
                    )  # Up to 25 chunks for very large docs (200+ pages)
                elif total_chunks > 100:
                    adaptive_max = min(
                        20, self.max_chunks * 4
                    )  # Up to 20 chunks for large docs (100+ pages)
                elif total_chunks > 50:
                    adaptive_max = min(
                        10, self.max_chunks * 2
                    )  # Up to 10 chunks for medium docs
                else:
                    adaptive_max = self.max_chunks  # Default (5) for small docs

                top_chunks = [chunk for chunk, score in sorted_items[:adaptive_max]]
                top_scores = [score for chunk, score in sorted_items[:adaptive_max]]

                # Update debug info with final chunks
                if debug_info:
                    debug_info["final_chunks_returned"] = len(top_chunks)
                    debug_info["score_distribution"] = {
                        "max": float(max(top_scores)) if top_scores else 0,
                        "min": float(min(top_scores)) if top_scores else 0,
                        "avg": (
                            float(sum(top_scores) / len(top_scores))
                            if top_scores
                            else 0
                        ),
                    }
                    logger.info(
                        f"[DEBUG] query_specific_file complete: {debug_info['final_chunks_returned']} chunks returned from {debug_info['total_chunks_before_dedup']} total"
                    )

                # Find the actual chunk indices from the RAG system
                chunk_indices = []
                for chunk in top_chunks:
                    # Find this chunk's index in the global chunks list
                    try:
                        idx = self.rag.chunks.index(chunk)
                        chunk_indices.append(idx)
                    except ValueError:
                        chunk_indices.append(-1)  # Not found

                formatted_chunks = [
                    {
                        "chunk_id": i + 1,  # Sequential for display
                        "page": extract_page_from_chunk(
                            chunk,
                            chunk_indices[i] if i < len(chunk_indices) else -1,
                            self.rag.chunks,
                        ),  # PDF page (with lookback)
                        "content": chunk,
                        "relevance_score": float(score),
                        "_debug_chunk_index": (
                            chunk_indices[i] if i < len(chunk_indices) else -1
                        ),  # Internal index (for debugging)
                    }
                    for i, (chunk, score) in enumerate(zip(top_chunks, top_scores))
                ]

                result = {
                    "status": "success",
                    "message": f"Found {len(top_chunks)} relevant chunks in {Path(target_file).name}",
                    "chunks": formatted_chunks,
                    "file": str(target_file),
                    "instruction": f"Use these chunks from {Path(target_file).name} to answer the question. Read through ALL {len(top_chunks)} chunks completely before answering.\n\nCRITICAL CITATION REQUIREMENT:\nYour answer MUST start with: 'According to {Path(target_file).name}, page X:' where X is the page number from the chunk's 'page' field.\n\nExample: If chunk has 'page': 2, say 'According to {Path(target_file).name}, page 2:'\nIf info from multiple pages, say 'According to {Path(target_file).name}, pages 2 and 5:'",
                }

                # Add debug info to result if debug mode is enabled
                if debug_info:
                    result["debug_info"] = debug_info

                return result

            except Exception as e:
                logger.error(f"Error in query_specific_file: {e}")
                # Graceful degradation
                return {
                    "status": "fallback",
                    "message": f"Unable to search in {file_path}",
                    "error": str(e),
                    "instruction": (
                        f"Could not search in the specific file '{file_path}'. "
                        "Inform the user about this issue and offer to help with general knowledge."
                    ),
                    "fallback_response": (
                        f"I encountered an error while trying to search in '{file_path}'. "
                        "The file might not be properly indexed or there was a technical issue. "
                        "Would you like me to try answering based on general knowledge instead?"
                    ),
                }

        @tool(
            atomic=True,
            name="search_indexed_chunks",
            description="Search for exact text patterns within RAG-indexed document chunks. Use for finding specific phrases in indexed documents.",
            parameters={
                "pattern": {
                    "type": "str",
                    "description": "Text pattern or keyword to search for",
                    "required": True,
                },
            },
        )
        def search_indexed_chunks(pattern: str) -> Dict[str, Any]:
            """
            Search for exact text patterns in RAG-indexed chunks.

            Searches in-memory RAG chunks, not files on disk.
            Faster than semantic RAG for exact matches.
            """
            try:
                # Debug information collection
                debug_info = (
                    {
                        "tool": "search_indexed_chunks",
                        "pattern": pattern,
                        "total_chunks_searched": 0,
                        "matches_found": 0,
                        "chunks_with_matches": [],
                    }
                    if hasattr(self, "debug") and self.debug
                    else None
                )

                if not self.rag.chunks:
                    return {"status": "error", "error": "No documents indexed."}

                # Search through chunks for pattern
                matching_chunks = []
                pattern_lower = pattern.lower()

                if debug_info:
                    debug_info["total_chunks_searched"] = len(self.rag.chunks)
                    logger.info(
                        f"[DEBUG] search_indexed_chunks: Searching for '{pattern}' in {len(self.rag.chunks)} chunks"
                    )

                for i, chunk in enumerate(self.rag.chunks):
                    if pattern_lower in chunk.lower():
                        matching_chunks.append(chunk)

                        # Capture debug info for first few matches
                        if debug_info and len(debug_info["chunks_with_matches"]) < 5:
                            # Find the line containing the pattern
                            lines = chunk.split("\n")
                            matching_lines = [
                                line for line in lines if pattern_lower in line.lower()
                            ]
                            debug_info["chunks_with_matches"].append(
                                {
                                    "chunk_index": i,
                                    "chunk_preview": (
                                        chunk[:100] + "..."
                                        if len(chunk) > 100
                                        else chunk
                                    ),
                                    "matching_lines": matching_lines[
                                        :2
                                    ],  # First 2 matching lines
                                }
                            )

                if debug_info:
                    debug_info["matches_found"] = len(matching_chunks)
                    logger.info(
                        f"[DEBUG] search_indexed_chunks complete: Found {len(matching_chunks)} matches"
                    )

                if not matching_chunks:
                    result = {
                        "status": "success",
                        "message": f"Pattern '{pattern}' not found in indexed documents",
                        "matches": [],
                        "count": 0,
                    }
                    if debug_info:
                        result["debug_info"] = debug_info
                    return result

                # Limit results
                limited_matches = matching_chunks[:10]

                result = {
                    "status": "success",
                    "message": f"Found {len(matching_chunks)} matches for '{pattern}'",
                    "matches": limited_matches,
                    "count": len(matching_chunks),
                    "showing": len(limited_matches),
                    "instruction": "Use these exact matches to answer the user's question.",
                }

                # Add debug info to result if debug mode is enabled
                if debug_info:
                    result["debug_info"] = debug_info

                return result

            except Exception as e:
                logger.error(f"Error in search_indexed_chunks: {e}")
                # Consistent error handling with graceful degradation
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "search_indexed_chunks",
                    "hint": "The text search failed. Try using query_documents for semantic search instead.",
                }

        # NOTE: search_file_content (disk-based grep) and write_file are now
        # provided by FileSearchToolsMixin from gaia.agents.tools.file_tools

        @tool(
            name="evaluate_retrieval",
            description="Evaluate if retrieved information is sufficient to answer the question. Use before providing final answer.",
            parameters={
                "question": {
                    "type": "str",
                    "description": "The original question",
                    "required": True,
                },
                "retrieved_info": {
                    "type": "str",
                    "description": "Summary of information retrieved so far",
                    "required": True,
                },
            },
        )
        def evaluate_retrieval(question: str, retrieved_info: str) -> Dict[str, Any]:
            """
            Evaluate if retrieved information sufficiently answers the question.

            Returns recommendation for next steps.
            """
            try:
                # Simple heuristic evaluation
                # In production, this could use LLM or more sophisticated metrics

                info_length = len(retrieved_info.strip())
                has_content = info_length > 50

                # Check if question keywords appear in retrieved info
                question_words = set(question.lower().split())
                info_words = set(retrieved_info.lower().split())
                keyword_overlap = len(question_words & info_words) / max(
                    len(question_words), 1
                )

                is_sufficient = has_content and keyword_overlap > 0.3

                if is_sufficient:
                    return {
                        "status": "success",
                        "sufficient": True,
                        "confidence": "high" if keyword_overlap > 0.5 else "medium",
                        "recommendation": "Provide answer based on retrieved information",
                        "keyword_overlap": round(keyword_overlap, 2),
                    }
                else:
                    return {
                        "status": "success",
                        "sufficient": False,
                        "confidence": "low",
                        "recommendation": "Try query_specific_file for targeted search or search_file_content for exact matches",
                        "keyword_overlap": round(keyword_overlap, 2),
                        "issues": [
                            "Low information content" if not has_content else None,
                            "Low keyword overlap" if keyword_overlap < 0.3 else None,
                        ],
                    }

            except Exception as e:
                logger.error(f"Error in evaluate_retrieval: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "evaluate_retrieval",
                    "hint": "Failed to evaluate retrieval quality. You can proceed with answering based on retrieved chunks.",
                }

        @tool(
            name="index_document",
            description="Add a document to the RAG index",
            parameters={
                "file_path": {
                    "type": "str",
                    "description": "Path to the document (PDF) to index",
                    "required": True,
                }
            },
        )
        def index_document(file_path: str) -> Dict[str, Any]:
            """Index a document with path validation and detailed statistics."""
            try:

                if not os.path.exists(file_path):
                    return {"status": "error", "error": f"File not found: {file_path}"}

                # Resolve to real path for consistent validation
                real_file_path = os.path.realpath(file_path)

                # Validate path with ChatAgent's internal logic (which uses allowed_paths)
                if hasattr(self, "_is_path_allowed"):
                    if not self._is_path_allowed(real_file_path):
                        return {
                            "status": "error",
                            "error": f"Access denied: {real_file_path} is not in allowed paths",
                        }

                # Index the document (now returns dict with stats)
                # Use real_file_path to ensure consistency in RAG index
                result = self.rag.index_document(real_file_path)

                if result.get("success"):
                    self.indexed_files.add(file_path)

                    # Add to current session
                    if self.current_session:
                        if file_path not in self.current_session.indexed_documents:
                            self.current_session.indexed_documents.append(file_path)
                            self.session_manager.save_session(self.current_session)

                    # Update system prompt to include the new document
                    if hasattr(self, "update_system_prompt"):
                        self.update_system_prompt()

                    # Return detailed stats from RAG SDK
                    return {
                        "status": "success",
                        "message": f"Successfully indexed: {result.get('file_name', file_path)}",
                        "file_name": result.get("file_name"),
                        "file_type": result.get("file_type"),
                        "file_size_mb": result.get("file_size_mb"),
                        "num_pages": result.get("num_pages"),
                        "num_chunks": result.get("num_chunks"),
                        "total_indexed_files": result.get("total_indexed_files"),
                        "total_chunks": result.get("total_chunks"),
                        "from_cache": result.get("from_cache", False),
                        "already_indexed": result.get("already_indexed", False),
                        "reindexed": result.get("reindexed", False),
                    }
                else:
                    return {
                        "status": "error",
                        "error": result.get("error", f"Failed to index: {file_path}"),
                        "file_name": result.get("file_name", Path(file_path).name),
                    }
            except Exception as e:
                logger.error(f"Error indexing document: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "index_document",
                    "file": file_path,
                    "hint": "Failed to index document. Check if file exists and is readable.",
                }

        @tool(
            atomic=True,
            name="list_indexed_documents",
            description="List all currently indexed documents",
            parameters={},
        )
        def list_indexed_documents() -> Dict[str, Any]:
            """List indexed documents."""
            try:
                docs = list(self.rag.indexed_files)
                return {
                    "status": "success",
                    "documents": [str(Path(d).name) for d in docs],
                    "count": len(docs),
                    "total_chunks": len(self.rag.chunks),
                }
            except Exception as e:
                logger.error(f"Error in list_indexed_documents: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "list_indexed_documents",
                }

        @tool(
            atomic=True,
            name="rag_status",
            description="Get the status of the RAG system",
            parameters={},
        )
        def rag_status() -> Dict[str, Any]:
            """Get RAG system status."""
            try:
                status = self.rag.get_status()
                return {
                    "status": "success",
                    **status,
                    "watched_directories": self.watch_directories,
                }
            except Exception as e:
                logger.error(f"Error in rag_status: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "rag_status",
                }

        @tool(
            name="summarize_document",
            description="Generate a comprehensive summary of a large indexed document by iterating through its content in sections. Best for getting an overview of lengthy documents.",
            parameters={
                "file_path": {
                    "type": "str",
                    "description": "Name or path of the document to summarize",
                    "required": True,
                },
                "summary_type": {
                    "type": "str",
                    "description": "Type of summary: 'brief' (2-3 paragraphs), 'detailed' (comprehensive with all key points), 'bullets' (key points as bullets) - default: 'detailed'",
                    "required": False,
                },
                "max_words_per_section": {
                    "type": "int",
                    "description": "Maximum words to process per section (default: 20000). Larger documents will be split into multiple sections and summarized iteratively.",
                    "required": False,
                },
            },
        )
        def summarize_document(
            file_path: str,
            summary_type: str = "detailed",
            max_words_per_section: int = 20000,
        ) -> Dict[str, Any]:
            """
            Summarize a large document by iterating through its content.

            For large documents, this will:
            1. Get the full text from cache (already extracted with VLM)
            2. Split into manageable sections based on word count
            3. Summarize each section with the LLM
            4. Combine section summaries into a final comprehensive summary
            """
            try:

                # Find the file in indexed files
                matching_files = [
                    f for f in self.rag.indexed_files if file_path in str(f)
                ]

                if not matching_files:
                    return {
                        "status": "error",
                        "error": f"Document '{file_path}' not found in indexed documents. Use index_document first.",
                    }

                target_file = matching_files[0]

                # Validate summary type
                valid_types = ["brief", "detailed", "bullets"]
                if summary_type not in valid_types:
                    return {
                        "status": "error",
                        "error": f"Invalid summary_type '{summary_type}'. Valid types: {', '.join(valid_types)}",
                    }

                # Get type-specific instruction
                type_instructions = {
                    "brief": "Create a concise 2-3 paragraph summary highlighting the most important points and main themes.",
                    "detailed": "Create a comprehensive summary covering all major topics, key points, and important details. Organize by sections if applicable.",
                    "bullets": "Create a bullet-point summary of the key points, organizing related items together. Use sub-bullets for details.",
                }

                summary_instruction = type_instructions[summary_type]

                # Get all chunks from the RAG index
                # Since we can't directly filter chunks by document, we'll use a workaround:
                # Extract text from the original PDF and chunk it

                logger.info(f"Summarizing document: {target_file}")

                # Use cached extracted text if available, otherwise extract
                try:
                    # Check if we have cached metadata with full_text
                    if (
                        target_file in self.rag.file_metadata
                        and "full_text" in self.rag.file_metadata[target_file]
                    ):
                        # Use cached text - no need to re-run VLM or extraction!
                        full_text = self.rag.file_metadata[target_file]["full_text"]
                        logger.debug(
                            f"Using cached extracted text for {Path(target_file).name}"
                        )
                    else:
                        # Fallback: Extract text using RAG SDK's file extraction
                        logger.warning(
                            f"No cached text found for {Path(target_file).name}, extracting..."
                        )
                        # pylint: disable=protected-access
                        full_text, _ = self.rag._extract_text_from_file(target_file)

                    if not full_text or not full_text.strip():
                        return {
                            "status": "error",
                            "error": f"No text could be extracted from {Path(target_file).name}",
                        }

                except Exception as e:
                    return {
                        "status": "error",
                        "error": f"Failed to extract text from document: {e}",
                    }

                # Split text into sections based on page boundaries
                # This is the simplest and most reliable semantic boundary

                # Split by page markers while keeping the markers
                page_sections = re.split(r"(\[Page \d+\])", full_text)

                # Recombine into complete pages
                pages = []
                current_page = ""

                for part in page_sections:
                    if re.match(r"\[Page \d+\]", part):
                        # This is a page marker
                        if current_page.strip():
                            pages.append(current_page.strip())
                        current_page = part + "\n"
                    else:
                        current_page += part

                # Add last page
                if current_page.strip():
                    pages.append(current_page.strip())

                # Group pages into sections that fit within max_words_per_section
                # Include overlap: last page of previous section is included in next section
                sections = []
                current_section_pages = []
                current_word_count = 0
                overlap_pages = 1  # Number of pages to overlap between sections

                for _page_idx, page in enumerate(pages):
                    page_words = len(page.split())

                    if (
                        current_word_count + page_words > max_words_per_section
                        and current_section_pages
                    ):
                        # Would exceed limit, save current section and start new with overlap
                        sections.append("\n\n".join(current_section_pages))

                        # Start new section with overlap (include last N pages from previous section)
                        overlap_start = max(
                            0, len(current_section_pages) - overlap_pages
                        )
                        current_section_pages = current_section_pages[overlap_start:]
                        current_word_count = sum(
                            len(p.split()) for p in current_section_pages
                        )

                    # Add page to current section
                    current_section_pages.append(page)
                    current_word_count += page_words

                # Add last section
                if current_section_pages:
                    sections.append("\n\n".join(current_section_pages))

                total_words = len(full_text.split())
                logger.info(
                    f"Document has {total_words} words, {len(pages)} pages, grouped into {len(sections)} sections"
                )

                # Get document metadata for enhanced summary
                file_metadata = self.rag.file_metadata.get(target_file, {})
                num_pages = file_metadata.get("num_pages", len(pages))
                _vlm_pages = file_metadata.get("vlm_pages", 0)

                # If document is small enough (single section), summarize in one pass
                if len(sections) <= 1:
                    prompt = f"""{summary_instruction}

Document to summarize: {Path(target_file).name}

Document content:
{full_text}

Generate a well-structured summary with the following format:

# Document Summary: {Path(target_file).name}

## Document Information
- **File**: {Path(target_file).name}
- **Pages**: {num_pages}
- **Total Words**: ~{total_words:,}

## Overview
[2-3 sentence overview of what this document is]

## Key Content
[Main content organized by topics/sections - reference page numbers where applicable]

## Key Takeaways
[Bullet points of the most important points]

Use the {summary_type} style for the content sections."""

                    # Use chat SDK to generate summary
                    try:
                        # Use RAG's chat SDK for summary generation
                        response = self.rag.chat.send(prompt)
                        summary_text = response.text

                        return {
                            "status": "success",
                            "summary": summary_text,
                            "summary_type": summary_type,
                            "document": str(Path(target_file).name),
                            "total_words": total_words,
                            "sections_processed": 1,
                            "instruction": "Present the summary to the user. The summary includes document metadata, structured sections, and page references.",
                        }
                    except Exception as e:
                        logger.error(f"Error generating summary: {e}")
                        return {
                            "status": "error",
                            "error": f"Failed to generate summary: {e}",
                        }

                # For long documents, iterate over sections (preserving semantic boundaries)
                section_summaries = []
                num_sections = len(sections)

                logger.info(f"Processing {num_sections} sections for summarization")

                for section_num, section_text in enumerate(sections, 1):
                    logger.info(
                        f"Summarizing section {section_num}/{num_sections} ({len(section_text.split())} words)"
                    )

                    # Generate summary for this section
                    section_prompt = f"""This is section {section_num} of {num_sections} from the document.
{summary_instruction}

Section content:
{section_text}

Generate a summary of this section:"""

                    try:
                        # Use RAG's chat SDK for section summary
                        response = self.rag.chat.send(section_prompt)
                        segment_summary = response.text

                        section_summaries.append(
                            {"section": section_num, "summary": segment_summary}
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to summarize segment {section_num}: {e}"
                        )
                        continue

                # Combine section summaries into final summary
                if not section_summaries:
                    return {
                        "status": "error",
                        "error": "Failed to generate any section summaries",
                    }

                # Final synthesis prompt with structured format
                combined_text = "\n\n".join(
                    [
                        f"Section {s['section']} Summary:\n{s['summary']}"
                        for s in section_summaries
                    ]
                )

                final_prompt = f"""You have summaries of {len(section_summaries)} sections from the document: {Path(target_file).name}

Section summaries:
{combined_text}

Synthesize these into a single, well-structured summary using this format:

# Document Summary: {Path(target_file).name}

## Document Information
- **File**: {Path(target_file).name}
- **Pages**: {num_pages}
- **Total Words**: ~{total_words:,}
- **Sections Processed**: {len(section_summaries)}

## Overview
[2-3 sentence overview synthesizing all sections]

## Key Content
[Main content organized by topics - consolidate from all section summaries, reference page numbers]

## Key Takeaways
[Bullet points of the most important points from across all sections]

Use the {summary_type} style. Ensure page references from section summaries are preserved."""

                try:
                    # Use RAG's chat SDK for final summary synthesis
                    response = self.rag.chat.send(final_prompt)
                    final_summary = response.text

                    return {
                        "status": "success",
                        "summary": final_summary,
                        "summary_type": summary_type,
                        "document": str(Path(target_file).name),
                        "total_words": total_words,
                        "sections_processed": len(section_summaries),
                        "section_summaries": section_summaries,
                        "instruction": "Present the formatted summary to the user. The summary includes document metadata, organized sections with page references, and key takeaways.",
                    }
                except Exception as e:
                    logger.error(f"Error synthesizing final summary: {e}")
                    # Return segment summaries as fallback
                    return {
                        "status": "partial",
                        "message": "Could not synthesize final summary, returning segment summaries",
                        "summary_style": summary_type,
                        "document": str(Path(target_file).name),
                        "total_words": total_words,
                        "iterations": len(section_summaries),
                        "segment_summaries": section_summaries,
                    }

            except Exception as e:
                logger.error(f"Error in summarize_document: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "summarize_document",
                    "file": target_file,
                    "hint": "Failed to generate summary. Try using query_documents to get specific information instead.",
                }

        # NOTE: search_file and search_directory tools are now provided by
        # FileSearchToolsMixin from gaia.agents.tools.file_tools
        # This provides shared file search functionality across all agents

        @tool(
            atomic=True,
            name="dump_document",
            description="Export the cached extracted text from an indexed document to a markdown file. Useful for reviewing extracted content or debugging.",
            parameters={
                "file_name": {
                    "type": "str",
                    "description": "Name or path of the indexed document to dump",
                    "required": True,
                },
                "output_path": {
                    "type": "str",
                    "description": "Output path for the markdown file (optional, defaults to .gaia/{filename}.md)",
                    "required": False,
                },
            },
        )
        def dump_document(file_name: str, output_path: str = None) -> Dict[str, Any]:
            """
            Export cached extracted text from an indexed document.

            This uses the cached full_text from file_metadata, avoiding re-extraction.
            """
            try:

                # Find the file in indexed files
                matching_files = [
                    f for f in self.rag.indexed_files if file_name in str(f)
                ]

                if not matching_files:
                    return {
                        "status": "error",
                        "error": f"Document '{file_name}' not found in indexed documents.",
                        "hint": "Use list_indexed_documents to see available documents.",
                    }

                target_file = matching_files[0]

                # Get cached text from metadata
                if target_file not in self.rag.file_metadata:
                    return {
                        "status": "error",
                        "error": f"No cached metadata found for {Path(target_file).name}",
                        "hint": "Document may need to be re-indexed.",
                    }

                metadata = self.rag.file_metadata[target_file]
                full_text = metadata.get("full_text", "")

                if not full_text:
                    return {
                        "status": "error",
                        "error": f"No extracted text found in cache for {Path(target_file).name}",
                    }

                # Determine output path
                if output_path is None:
                    output_filename = Path(target_file).stem + "_extracted.md"
                    output_path = os.path.join(
                        self.rag.config.cache_dir, output_filename
                    )
                else:
                    output_path = str(Path(output_path).resolve())

                # Write markdown file with metadata header
                markdown_content = f"""# Extracted Text from {Path(target_file).name}

**Source File:** {target_file}
**Extraction Date:** {metadata.get('index_time', 'Unknown')}
**Pages:** {metadata.get('num_pages', 'N/A')}
**VLM Pages:** {metadata.get('vlm_pages', 0)}
**Total Images:** {metadata.get('total_images', 0)}

---

{full_text}
"""

                # Ensure output directory exists
                os.makedirs(
                    (
                        os.path.dirname(output_path)
                        if os.path.dirname(output_path)
                        else "."
                    ),
                    exist_ok=True,
                )

                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(markdown_content)

                return {
                    "status": "success",
                    "output_path": output_path,
                    "text_length": len(full_text),
                    "num_pages": metadata.get("num_pages", "N/A"),
                    "vlm_pages": metadata.get("vlm_pages", 0),
                    "message": f"Exported extracted text to {output_path}",
                }

            except Exception as e:
                logger.error(f"Error dumping document: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "dump_document",
                }

        @tool(
            atomic=True,
            name="index_directory",
            description="Index all supported files in a directory. Supports PDF, TXT, CSV, JSON, and code files.",
            parameters={
                "directory_path": {
                    "type": "str",
                    "description": "Path to directory to index",
                    "required": True,
                },
                "recursive": {
                    "type": "bool",
                    "description": "Whether to recursively index subdirectories (default: False)",
                    "required": False,
                },
            },
        )
        def index_directory(
            directory_path: str, recursive: bool = False
        ) -> Dict[str, Any]:
            """
            Index all supported files in a directory.

            Returns statistics about indexed files.
            """
            try:
                dir_path = Path(directory_path).resolve()

                if not dir_path.exists():
                    return {
                        "status": "error",
                        "error": f"Directory does not exist: {directory_path}",
                        "has_errors": True,
                    }

                if not dir_path.is_dir():
                    return {
                        "status": "error",
                        "error": f"Path is not a directory: {directory_path}",
                        "has_errors": True,
                    }

                logger.info(f"Indexing directory: {dir_path} (recursive={recursive})")

                # Supported file extensions
                supported_extensions = {
                    ".pdf",
                    ".txt",
                    ".csv",
                    ".json",
                    ".py",
                    ".js",
                    ".java",
                    ".cpp",
                    ".c",
                    ".h",
                    ".md",
                }

                indexed_files = []
                failed_files = []
                skipped_files = []

                # Get files to index
                if recursive:
                    files_to_index = [f for f in dir_path.rglob("*") if f.is_file()]
                else:
                    files_to_index = [f for f in dir_path.iterdir() if f.is_file()]

                for file_path in files_to_index:
                    if file_path.suffix.lower() in supported_extensions:
                        try:
                            # Use the RAG SDK to index the file
                            success = self.rag.index_document(str(file_path))
                            if success:
                                indexed_files.append(str(file_path))
                                logger.info(f"Indexed: {file_path.name}")
                            else:
                                failed_files.append(str(file_path))
                        except Exception as e:
                            logger.warning(f"Failed to index {file_path}: {e}")
                            failed_files.append(str(file_path))
                    else:
                        skipped_files.append(str(file_path))

                # Update system prompt after indexing directory
                if indexed_files and hasattr(self, "update_system_prompt"):
                    self.update_system_prompt()

                return {
                    "status": "success",
                    "indexed_count": len(indexed_files),
                    "failed_count": len(failed_files),
                    "skipped_count": len(skipped_files),
                    "indexed_files": [Path(f).name for f in indexed_files],
                    "failed_files": (
                        [Path(f).name for f in failed_files] if failed_files else []
                    ),
                    "message": f"Indexed {len(indexed_files)} files from {dir_path.name}",
                }

            except Exception as e:
                logger.error(f"Error indexing directory: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "index_directory",
                }
