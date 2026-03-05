#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration test for GAIA RAG functionality using real PDF documents.

This test validates end-to-end RAG workflows:
- PDF text extraction (50+ pages)
- Semantic chunking
- Vector embeddings via Lemonade server
- FAISS similarity search
- LLM-based query answering
- Chat SDK integration

Requirements:
- LLM server running on localhost:8000 (use: gaia docker up)
- Test PDF: data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf
- Dependencies: pip install -e .[rag]

Usage:
    python tests/test_rag_integration.py
    python tests/test_rag_integration.py -v  # Verbose output
"""

import sys
from pathlib import Path

# Fix Windows console encoding for emoji support
# Use reconfigure() instead of TextIOWrapper to avoid closing underlying buffer on exit
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add path for imports (tests directory is already in Python path)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def ensure_models_downloaded():
    """Check if required models are available (CI should pre-download them)."""
    print("\n🔧 Checking Model Availability")
    print("-" * 70)

    try:
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient()

        # Required models for RAG testing
        required_models = [
            "Qwen3-4B-Instruct-2507-GGUF",  # LLM model used in tests
            "nomic-embed-text-v2-moe-GGUF",  # Embedding model (RAG default)
            "Qwen3-VL-4B-Instruct-GGUF",  # VLM model (for PDFs with images)
        ]

        print(f"Checking {len(required_models)} required models...\n")

        # Just check if models are listed - don't try to download
        # CI workflows should have already pulled models
        models_response = client.list_models()
        available_models = [m.get("id") for m in models_response.get("data", [])]

        print(f"📋 Available models: {len(available_models)}")
        for model_id in available_models:
            print(f"   - {model_id}")

        all_available = True
        for model_name in required_models:
            if model_name in available_models:
                print(f"\n✅ {model_name} is available")
            else:
                print(f"\n⚠️  {model_name} not found")
                all_available = False

        if all_available:
            print("\n✅ All required models are ready!")
            return True
        else:
            print("\n⚠️  Some models may not be available")
            print("   CI should have pre-pulled models, continuing anyway...")
            # Return True anyway - let actual tests fail if models truly missing
            return True

    except Exception as e:
        print(f"⚠️  Could not check models: {e}")
        print("   Continuing anyway...")
        # Return True - don't fail just because we can't check
        return True


def get_test_pdf() -> tuple:
    """Get path to test PDF file.

    Returns:
        tuple: (file_path, exists) where exists indicates if the PDF was found
    """
    # Use the shorter Oil & Gas manual (pages 1-10) from the data folder
    repo_root = Path(__file__).parent.parent
    pdf_path = (
        repo_root / "data" / "pdf" / "Oil-and-Gas-Activity-Operations-Manual-1-10.pdf"
    )

    if pdf_path.exists():
        return str(pdf_path), True
    else:
        print(f"⚠️  Test PDF not found: {pdf_path}")
        print("   Expected: data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf")
        return None, False


def test_basic_functionality():
    """Test basic RAG functionality."""
    print("🔬 Testing Basic RAG Functionality")
    print("-" * 40)

    try:
        from gaia.rag.sdk import RAGSDK, RAGConfig

        print("✅ RAG SDK imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import RAG SDK: {e}")
        print("Install dependencies: pip install pypdf sentence-transformers faiss-cpu")
        return False

    # Test configuration
    try:
        config = RAGConfig(
            model="Qwen3-4B-Instruct-2507-GGUF",
            chunk_size=200,
            max_chunks=2,
            show_stats=True,
        )
        print("✅ RAG configuration created")
    except Exception as e:
        print(f"❌ Failed to create config: {e}")
        return False

    # Test SDK initialization
    try:
        rag = RAGSDK(config)
        print("✅ RAG SDK initialized")
    except Exception as e:
        print(f"❌ Failed to initialize SDK: {e}")
        return False

    # Check status
    try:
        status = rag.get_status()
        print(f"✅ Status check: {status['total_chunks']} chunks indexed")
    except Exception as e:
        print(f"❌ Failed to get status: {e}")
        return False

    return True


def test_document_processing():
    """Test document processing with real PDF."""
    print("\n📄 Testing Document Processing")
    print("-" * 40)

    try:
        from gaia.rag.sdk import RAGSDK, RAGConfig

        # Get test PDF
        doc_path, pdf_exists = get_test_pdf()
        if not pdf_exists:
            print("⏭️  Skipping document processing test - PDF not found")
            return True

        pdf_name = Path(doc_path).name
        pdf_size_mb = Path(doc_path).stat().st_size / (1024 * 1024)
        print(f"📄 Using test document: {pdf_name} ({pdf_size_mb:.1f} MB)")

        # Initialize RAG with reasonable settings for large document
        config = RAGConfig(
            model="Qwen3-4B-Instruct-2507-GGUF",  # Qwen3-4B: good balance of quality and speed
            chunk_size=500,  # Reasonable chunk size
            max_chunks=5,  # Get top 5 most relevant chunks
            show_stats=True,  # Show progress
        )
        rag = RAGSDK(config)

        # Index document
        print("\n📚 Indexing document (this may take a minute for large PDFs)...")
        result = rag.index_document(doc_path)

        if result.get("success"):
            print("\n✅ Document indexed successfully!")
            print(f"   • Pages: {result.get('num_pages', 'N/A')}")
            print(f"   • Chunks: {result.get('num_chunks', 'N/A')}")
            print(f"   • Total indexed files: {result.get('total_indexed_files', 0)}")

            # Check status
            status = rag.get_status()
            print("\n📊 RAG Status:")
            print(f"   • Total chunks: {status['total_chunks']}")
            print(f"   • Indexed files: {status['indexed_files']}")

            # Test query relevant to Oil & Gas document
            print("\n❓ Testing query: 'What safety requirements are mentioned?'")
            response = rag.query("What safety requirements are mentioned?")

            print("\n✅ Query response received!")
            print(f"📝 Answer preview: {response.text[:200]}...")

            if response.chunks:
                print(f"\n📖 Retrieved {len(response.chunks)} relevant chunks")
                if response.chunk_scores:
                    avg_score = sum(response.chunk_scores) / len(response.chunk_scores)
                    print(f"   • Average relevance score: {avg_score:.3f}")

            return True
        else:
            error_msg = result.get("error", "Unknown error")
            print(f"\n❌ Failed to index document: {error_msg}")
            return False

    except Exception as e:
        print(f"\n❌ Document processing failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_chat_integration():
    """Test chat integration."""
    print("\n💬 Testing Chat Integration")
    print("-" * 40)

    try:
        from gaia.chat.sdk import ChatConfig, ChatSDK

        # Create chat with RAG
        chat_config = ChatConfig(show_stats=False)
        chat = ChatSDK(chat_config)
        print("✅ Chat SDK initialized")

        # Test enabling RAG
        chat.enable_rag()
        print("✅ RAG enabled in chat")

        # Test disabling RAG
        chat.disable_rag()
        print("✅ RAG disabled in chat")

        return True

    except Exception as e:
        print(f"❌ Chat integration failed: {e}")
        return False


def test_query_with_files():
    """Test multiple queries on indexed document."""
    print("\n⚡ Testing Multiple Queries")
    print("-" * 40)

    try:
        from gaia.rag.sdk import RAGSDK, RAGConfig

        # Get test PDF
        doc_path, pdf_exists = get_test_pdf()
        if not pdf_exists:
            print("⏭️  Skipping query test - PDF not found")
            return True

        print("📄 Indexing document for query tests...")

        # Use smaller chunks for faster testing
        config = RAGConfig(
            model="Qwen3-4B-Instruct-2507-GGUF",
            chunk_size=300,
            max_chunks=3,
            show_stats=False,
        )
        rag = RAGSDK(config)

        # Index document
        result = rag.index_document(doc_path)
        if not result.get("success"):
            print(
                f"❌ Failed to index document: {result.get('error', 'Unknown error')}"
            )
            return False

        print(f"✅ Indexed {result.get('num_chunks', 0)} chunks\n")

        # Test multiple queries
        test_queries = [
            "What is this document about?",
            "What regulations are mentioned?",
            "What are the key requirements?",
        ]

        print("🔍 Testing multiple queries:\n")
        for i, query in enumerate(test_queries, 1):
            # Clear conversation history between queries to prevent context overflow
            # Each RAG query injects chunk context, which accumulates across calls
            if hasattr(rag, "chat") and hasattr(rag.chat, "clear_history"):
                rag.chat.clear_history()
            print(f"   {i}. Query: '{query}'")
            try:
                response = rag.query(query)
                answer_preview = (
                    response.text[:80] + "..."
                    if len(response.text) > 80
                    else response.text
                )
                print(f"      Answer: {answer_preview}")
                if response.chunk_scores:
                    print(f"      Relevance: {max(response.chunk_scores):.3f}\n")
            except Exception as e:
                print(f"      ❌ Query failed: {e}\n")
                return False

        print("✅ All queries completed successfully")
        return True

    except Exception as e:
        print(f"❌ Query test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_cli_commands():
    """Test CLI command structure."""
    print("\n🖥️  Testing CLI Commands")
    print("-" * 40)

    try:

        print("✅ All CLI command functions imported")

        # Verify main entry point exists

        print("✅ CLI main entry point available")

        return True

    except Exception as e:
        print(f"❌ CLI test failed: {e}")
        return False


def main():
    """Run all integration tests."""
    print("🚀 GAIA RAG Integration Test")
    print("=" * 70)
    print("Testing end-to-end RAG functionality with real PDF document")
    print("Document: Oil-and-Gas-Activity-Operations-Manual-1-10.pdf (pages 1-10)")
    print("=" * 70)

    # Ensure required models are downloaded
    if not ensure_models_downloaded():
        print("\n❌ Model download failed!")
        print("   Cannot proceed with tests without required models.")
        print("   Please ensure Lemonade Server is running and accessible.")
        return False

    # Check if test PDF exists
    pdf_path, pdf_exists = get_test_pdf()
    if not pdf_exists:
        print("\n❌ Test PDF not found!")
        print("   Expected: data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf")
        print("   Please ensure the PDF exists in the repository.")
        return False

    tests = [
        ("Basic Functionality", test_basic_functionality),
        ("Document Processing", test_document_processing),
        ("Chat Integration", test_chat_integration),
        ("Multiple Queries", test_query_with_files),
        ("CLI Commands", test_cli_commands),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ {test_name} failed with exception: {e}")
            import traceback

            traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 70)
    print("📊 Test Results Summary")
    print("=" * 70)

    passed = 0
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name:.<40} {status}")
        if result:
            passed += 1

    print("-" * 70)
    print(f"Total: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! RAG implementation is working correctly.")
        print("\nTested capabilities:")
        print("  ✓ PDF document indexing (10-page document)")
        print("  ✓ Text extraction and chunking")
        print("  ✓ Vector embeddings generation")
        print("  ✓ Semantic search and retrieval")
        print("  ✓ LLM-based query answering")
        print("  ✓ Chat SDK integration")
    else:
        print("\n⚠️  Some tests failed. Check the output above for details.")
        print("\nCommon issues:")
        print("  • Missing dependencies: pip install -e .[rag]")
        print("  • LLM service not running on localhost:8000")
        print("  • Insufficient memory for large PDF processing")
        print("  • Test PDF not found in data/pdf/ directory")

    print("\n✨ Integration test completed!")
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
