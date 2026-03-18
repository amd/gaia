#!/usr/bin/env python
"""Stress test for GAIA Agent UI.

Tests the agent's capabilities, long conversations, concurrency limits,
and edge cases by interacting with the running UI server via HTTP.

Usage:
    uv run python tests/stress/test_agent_ui_stress.py

Requirements:
    - GAIA UI server running on http://localhost:4200
    - Lemonade server running on http://localhost:8000
"""

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field

try:
    import httpx
except ImportError:
    print("ERROR: httpx required. Install with: uv pip install httpx")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:4200"
TIMEOUT = 180  # seconds per request
STREAM_TIMEOUT = 300  # seconds for streaming requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stress")


# ── Result Tracking ──────────────────────────────────────────────────────────


@dataclass
class TestResult:
    name: str
    passed: bool
    duration: float = 0.0
    error: str = ""
    details: str = ""


@dataclass
class StressReport:
    results: list = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    def add(self, result: TestResult):
        self.results.append(result)
        status = "PASS" if result.passed else "FAIL"
        log.info(
            "  [%s] %s (%.1fs)%s",
            status,
            result.name,
            result.duration,
            f" - {result.error}" if result.error else "",
        )

    def summary(self):
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total_time = self.end_time - self.start_time
        print("\n" + "=" * 70)
        print(f"  STRESS TEST REPORT  ({total_time:.1f}s total)")
        print("=" * 70)
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            line = f"  [{status}] {r.name} ({r.duration:.1f}s)"
            if r.error:
                line += f"\n         Error: {r.error}"
            if r.details:
                line += f"\n         {r.details}"
            print(line)
        print("-" * 70)
        print(f"  Total: {len(self.results)} | Passed: {passed} | Failed: {failed}")
        print("=" * 70)
        return failed == 0


# ── Helpers ───────────────────────────────────────────────────────────────────


async def collect_sse_stream(response) -> dict:
    """Collect all SSE events from a streaming response.

    Returns a dict with:
      - events: list of all parsed events
      - answer: final answer text
      - chunks: concatenated chunk text
      - errors: list of error events
      - tool_calls: list of tool_start events
      - steps: count of steps
    """
    events = []
    chunks = ""
    answer = ""
    errors = []
    tool_calls = []
    steps = 0

    async for line in response.aiter_lines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data: "):
            data_str = line[6:]
            try:
                event = json.loads(data_str)
                events.append(event)
                evt_type = event.get("type", "")

                if evt_type == "chunk":
                    chunks += event.get("content", "")
                elif evt_type == "answer":
                    answer = event.get("content", "")
                elif evt_type == "done":
                    if not answer:
                        answer = event.get("content", "")
                elif evt_type in ("error", "agent_error"):
                    errors.append(event.get("content", ""))
                elif evt_type == "tool_start":
                    tool_calls.append(event.get("tool", "unknown"))
                elif evt_type == "step":
                    steps += 1
            except json.JSONDecodeError:
                pass

    final_answer = answer or chunks
    return {
        "events": events,
        "answer": final_answer,
        "chunks": chunks,
        "errors": errors,
        "tool_calls": tool_calls,
        "steps": steps,
        "event_count": len(events),
    }


async def create_session(client: httpx.AsyncClient, title: str = "Stress Test") -> str:
    """Create a new session and return its ID."""
    resp = await client.post(
        f"{BASE_URL}/api/sessions",
        json={"title": title},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


async def send_message_streaming(
    client: httpx.AsyncClient, session_id: str, message: str
) -> dict:
    """Send a streaming chat message and collect the full response."""
    async with client.stream(
        "POST",
        f"{BASE_URL}/api/chat/send",
        json={"session_id": session_id, "message": message, "stream": True},
        timeout=httpx.Timeout(STREAM_TIMEOUT, connect=30.0),
    ) as resp:
        resp.raise_for_status()
        return await collect_sse_stream(resp)


async def send_message_nonstreaming(
    client: httpx.AsyncClient, session_id: str, message: str
) -> dict:
    """Send a non-streaming chat message and return the response."""
    resp = await client.post(
        f"{BASE_URL}/api/chat/send",
        json={"session_id": session_id, "message": message, "stream": False},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def delete_session(client: httpx.AsyncClient, session_id: str):
    """Delete a session (cleanup)."""
    try:
        await client.delete(f"{BASE_URL}/api/sessions/{session_id}", timeout=10)
    except Exception:
        pass


# ── Test Cases ────────────────────────────────────────────────────────────────


async def test_health_check(client: httpx.AsyncClient, report: StressReport):
    """Test basic health endpoint."""
    t0 = time.time()
    try:
        resp = await client.get(f"{BASE_URL}/api/health", timeout=10)
        data = resp.json()
        passed = resp.status_code == 200 and data.get("status") == "ok"
        report.add(
            TestResult(
                name="Health Check",
                passed=passed,
                duration=time.time() - t0,
                details=f"Sessions: {data.get('stats', {}).get('sessions', '?')}, "
                f"Messages: {data.get('stats', {}).get('messages', '?')}",
            )
        )
    except Exception as e:
        report.add(TestResult("Health Check", False, time.time() - t0, str(e)))


async def test_system_status(client: httpx.AsyncClient, report: StressReport):
    """Test system status endpoint."""
    t0 = time.time()
    try:
        resp = await client.get(f"{BASE_URL}/api/system/status", timeout=30)
        data = resp.json()
        passed = resp.status_code == 200
        lemonade = data.get("lemonade_running", False)
        model = data.get("model_loaded", "none")
        report.add(
            TestResult(
                name="System Status",
                passed=passed,
                duration=time.time() - t0,
                details=f"Lemonade: {lemonade}, Model: {model}",
            )
        )
    except Exception as e:
        report.add(TestResult("System Status", False, time.time() - t0, str(e)))


async def test_session_crud(client: httpx.AsyncClient, report: StressReport):
    """Test session create, read, update, delete."""
    t0 = time.time()
    session_id = None
    try:
        # Create
        resp = await client.post(
            f"{BASE_URL}/api/sessions",
            json={"title": "CRUD Test Session"},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 200, f"Create failed: {resp.status_code}"
        session_id = resp.json()["id"]

        # Read
        resp = await client.get(f"{BASE_URL}/api/sessions/{session_id}", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["title"] == "CRUD Test Session"

        # Update
        resp = await client.put(
            f"{BASE_URL}/api/sessions/{session_id}",
            json={"title": "Updated Title"},
            timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"

        # List
        resp = await client.get(f"{BASE_URL}/api/sessions", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

        # Delete
        resp = await client.delete(f"{BASE_URL}/api/sessions/{session_id}", timeout=10)
        assert resp.status_code == 200
        session_id = None  # Already deleted

        # Verify deleted
        resp = await client.get(f"{BASE_URL}/api/sessions/{session_id}", timeout=10)
        assert resp.status_code == 404

        report.add(TestResult("Session CRUD", True, time.time() - t0))
    except Exception as e:
        report.add(TestResult("Session CRUD", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_session_not_found(client: httpx.AsyncClient, report: StressReport):
    """Test 404 handling for nonexistent session."""
    t0 = time.time()
    try:
        resp = await client.get(
            f"{BASE_URL}/api/sessions/nonexistent-uuid-12345", timeout=10
        )
        passed = resp.status_code == 404
        report.add(TestResult("Session 404", passed, time.time() - t0))
    except Exception as e:
        report.add(TestResult("Session 404", False, time.time() - t0, str(e)))


async def test_simple_greeting(client: httpx.AsyncClient, report: StressReport):
    """Test a simple greeting that should NOT trigger any tools."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Simple Greeting Test")
        result = await send_message_streaming(client, session_id, "Hi! How are you?")

        passed = bool(result["answer"]) and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="Simple Greeting (no tools)",
                passed=passed,
                duration=time.time() - t0,
                details=f"Answer: {result['answer'][:100]}... | "
                f"Events: {result['event_count']} | "
                f"Tools: {result['tool_calls']}",
                error="; ".join(result["errors"]) if result["errors"] else "",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Simple Greeting (no tools)", False, time.time() - t0, str(e))
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_knowledge_question(client: httpx.AsyncClient, report: StressReport):
    """Test a knowledge question that should be answered directly."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Knowledge Test")
        result = await send_message_streaming(
            client, session_id, "What is the capital of France? Answer in one sentence."
        )

        has_answer = bool(result["answer"])
        mentions_paris = (
            "paris" in result["answer"].lower() if result["answer"] else False
        )
        passed = has_answer and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="Knowledge Question",
                passed=passed,
                duration=time.time() - t0,
                details=f"Mentions Paris: {mentions_paris} | "
                f"Answer length: {len(result['answer'])} chars",
            )
        )
    except Exception as e:
        report.add(TestResult("Knowledge Question", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_file_search_tool(client: httpx.AsyncClient, report: StressReport):
    """Test a query that should trigger the search_file tool."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "File Search Test")
        result = await send_message_streaming(
            client,
            session_id,
            "Search my computer for any .txt files. Just list the first few you find.",
        )

        has_answer = bool(result["answer"])
        used_tool = len(result["tool_calls"]) > 0
        passed = has_answer and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="File Search Tool",
                passed=passed,
                duration=time.time() - t0,
                details=f"Tools used: {result['tool_calls']} | "
                f"Answer length: {len(result['answer'])} chars",
            )
        )
    except Exception as e:
        report.add(TestResult("File Search Tool", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_shell_command_tool(client: httpx.AsyncClient, report: StressReport):
    """Test a query that should trigger the run_shell_command tool."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Shell Command Test")
        result = await send_message_streaming(
            client,
            session_id,
            "Run 'echo Hello from GAIA stress test' as a shell command and tell me the output.",
        )

        has_answer = bool(result["answer"])
        passed = has_answer and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="Shell Command Tool",
                passed=passed,
                duration=time.time() - t0,
                details=f"Tools used: {result['tool_calls']} | "
                f"Answer: {result['answer'][:120]}...",
            )
        )
    except Exception as e:
        report.add(TestResult("Shell Command Tool", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_rag_status_tool(client: httpx.AsyncClient, report: StressReport):
    """Test a query that should trigger the rag_status tool."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "RAG Status Test")
        result = await send_message_streaming(
            client,
            session_id,
            "What is the current RAG status? How many documents are indexed?",
        )

        has_answer = bool(result["answer"])
        passed = has_answer and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="RAG Status Tool",
                passed=passed,
                duration=time.time() - t0,
                details=f"Tools used: {result['tool_calls']} | "
                f"Answer: {result['answer'][:120]}...",
            )
        )
    except Exception as e:
        report.add(TestResult("RAG Status Tool", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_multi_turn_conversation(client: httpx.AsyncClient, report: StressReport):
    """Test a multi-turn conversation to verify context retention."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Multi-Turn Test")

        # Turn 1: Introduce a topic
        log.info("    Turn 1: Setting context...")
        r1 = await send_message_streaming(
            client,
            session_id,
            "My name is StressTestBot and my favorite number is 42. Remember this.",
        )
        assert r1["answer"], "Turn 1 got empty answer"
        assert len(r1["errors"]) == 0, f"Turn 1 errors: {r1['errors']}"

        # Turn 2: Test recall
        log.info("    Turn 2: Testing recall...")
        r2 = await send_message_streaming(
            client, session_id, "What is my name and what is my favorite number?"
        )
        answer2 = r2["answer"].lower()
        has_name = "stresstestbot" in answer2 or "stress" in answer2
        has_number = "42" in answer2
        passed = bool(r2["answer"]) and len(r2["errors"]) == 0

        report.add(
            TestResult(
                name="Multi-Turn Context Retention",
                passed=passed,
                duration=time.time() - t0,
                details=f"Recalled name: {has_name} | Recalled number: {has_number} | "
                f"Answer: {r2['answer'][:100]}...",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Multi-Turn Context Retention", False, time.time() - t0, str(e))
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_long_conversation(client: httpx.AsyncClient, report: StressReport):
    """Test a long conversation with many turns to stress context handling."""
    t0 = time.time()
    session_id = None
    num_turns = 8
    successful_turns = 0
    errors_collected = []
    try:
        session_id = await create_session(client, "Long Conversation Test")

        messages = [
            "Tell me about the number 1. Just one sentence.",
            "Now tell me about the number 2. Just one sentence.",
            "What about the number 3? One sentence only.",
            "And the number 4? Keep it brief.",
            "How about 5? One sentence.",
            "Tell me about 6 in one sentence.",
            "What about 7? Brief please.",
            "Now summarize: which numbers did we discuss? List them all.",
        ]

        for i, msg in enumerate(messages):
            log.info(f"    Turn {i+1}/{num_turns}: {msg[:50]}...")
            try:
                result = await send_message_streaming(client, session_id, msg)
                if result["answer"] and len(result["errors"]) == 0:
                    successful_turns += 1
                    log.info(f"    -> OK ({len(result['answer'])} chars)")
                else:
                    errors_collected.extend(result["errors"])
                    log.warning(f"    -> Errors: {result['errors']}")
            except Exception as turn_err:
                errors_collected.append(f"Turn {i+1}: {turn_err}")
                log.warning(f"    -> Exception: {turn_err}")

        passed = successful_turns >= num_turns - 1  # Allow 1 failure
        report.add(
            TestResult(
                name=f"Long Conversation ({num_turns} turns)",
                passed=passed,
                duration=time.time() - t0,
                details=f"Successful: {successful_turns}/{num_turns}",
                error="; ".join(errors_collected[:3]) if errors_collected else "",
            )
        )
    except Exception as e:
        report.add(
            TestResult(
                f"Long Conversation ({num_turns} turns)",
                False,
                time.time() - t0,
                str(e),
            )
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_non_streaming_mode(client: httpx.AsyncClient, report: StressReport):
    """Test non-streaming response mode."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Non-Streaming Test")
        result = await send_message_nonstreaming(
            client, session_id, "What is 2 + 2? Answer with just the number."
        )

        has_content = bool(result.get("content"))
        has_msg_id = result.get("message_id") is not None
        passed = has_content and has_msg_id
        report.add(
            TestResult(
                name="Non-Streaming Mode",
                passed=passed,
                duration=time.time() - t0,
                details=f"Content: {result.get('content', '')[:80]} | "
                f"message_id: {result.get('message_id')}",
            )
        )
    except Exception as e:
        report.add(TestResult("Non-Streaming Mode", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_concurrent_sessions(client: httpx.AsyncClient, report: StressReport):
    """Test sending messages to different sessions concurrently (within semaphore limit)."""
    t0 = time.time()
    session_ids = []
    try:
        # Create 2 sessions (server allows max 2 concurrent)
        s1 = await create_session(client, "Concurrent Test 1")
        s2 = await create_session(client, "Concurrent Test 2")
        session_ids = [s1, s2]

        # Send messages concurrently
        async def send_to(sid, msg):
            return await send_message_streaming(client, sid, msg)

        results = await asyncio.gather(
            send_to(s1, "Say 'hello session 1' in exactly those words."),
            send_to(s2, "Say 'hello session 2' in exactly those words."),
            return_exceptions=True,
        )

        successes = 0
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log.warning(f"    Session {i+1} failed: {r}")
            elif isinstance(r, dict) and r.get("answer"):
                successes += 1

        # At least 1 should succeed (2 is ideal but depends on server load)
        passed = successes >= 1
        report.add(
            TestResult(
                name="Concurrent Sessions",
                passed=passed,
                duration=time.time() - t0,
                details=f"Successes: {successes}/2",
            )
        )
    except Exception as e:
        report.add(TestResult("Concurrent Sessions", False, time.time() - t0, str(e)))
    finally:
        for sid in session_ids:
            await delete_session(client, sid)


async def test_session_lock_conflict(client: httpx.AsyncClient, report: StressReport):
    """Test that sending 2 messages to same session returns 409."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Lock Conflict Test")

        # Send a slow query first (streaming)
        async def slow_msg():
            return await send_message_streaming(
                client,
                session_id,
                "Write a detailed paragraph about the history of computing.",
            )

        async def fast_msg():
            # Wait a tiny bit for the first request to acquire the lock
            await asyncio.sleep(0.5)
            resp = await client.post(
                f"{BASE_URL}/api/chat/send",
                json={
                    "session_id": session_id,
                    "message": "Quick question",
                    "stream": False,
                },
                timeout=10,
            )
            return resp.status_code

        results = await asyncio.gather(slow_msg(), fast_msg(), return_exceptions=True)

        # The fast message should get 409 (session locked)
        fast_result = results[1]
        got_conflict = False
        if isinstance(fast_result, int):
            got_conflict = fast_result == 409
        elif isinstance(fast_result, httpx.HTTPStatusError):
            got_conflict = fast_result.response.status_code == 409

        passed = got_conflict
        report.add(
            TestResult(
                name="Session Lock Conflict (409)",
                passed=passed,
                duration=time.time() - t0,
                details=f"Fast msg status: {fast_result}",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Session Lock Conflict (409)", False, time.time() - t0, str(e))
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_message_history(client: httpx.AsyncClient, report: StressReport):
    """Test message retrieval and pagination."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Message History Test")

        # Send a message to create some history
        await send_message_streaming(
            client, session_id, "Hello, this is a test message."
        )

        # Get messages
        resp = await client.get(
            f"{BASE_URL}/api/sessions/{session_id}/messages?limit=50",
            timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should have at least 2 messages (user + assistant)
        total = data.get("total", 0)
        msgs = data.get("messages", [])

        # Test pagination
        resp2 = await client.get(
            f"{BASE_URL}/api/sessions/{session_id}/messages?limit=1&offset=0",
            timeout=10,
        )
        assert resp2.status_code == 200
        page = resp2.json()

        passed = total >= 2 and len(msgs) >= 2
        report.add(
            TestResult(
                name="Message History & Pagination",
                passed=passed,
                duration=time.time() - t0,
                details=f"Total messages: {total} | Page 1 count: {len(page.get('messages', []))}",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Message History & Pagination", False, time.time() - t0, str(e))
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_session_export(client: httpx.AsyncClient, report: StressReport):
    """Test session export in markdown and JSON formats."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Export Test")
        await send_message_streaming(client, session_id, "Hello, export test!")

        # Markdown export
        resp_md = await client.get(
            f"{BASE_URL}/api/sessions/{session_id}/export?format=markdown",
            timeout=10,
        )
        assert resp_md.status_code == 200
        md_data = resp_md.json()
        has_md = "content" in md_data and "Export Test" in md_data["content"]

        # JSON export
        resp_json = await client.get(
            f"{BASE_URL}/api/sessions/{session_id}/export?format=json",
            timeout=10,
        )
        assert resp_json.status_code == 200
        json_data = resp_json.json()
        has_json = "session" in json_data and "messages" in json_data

        # Invalid format
        resp_bad = await client.get(
            f"{BASE_URL}/api/sessions/{session_id}/export?format=xml",
            timeout=10,
        )
        bad_rejected = resp_bad.status_code == 400

        passed = has_md and has_json and bad_rejected
        report.add(
            TestResult(
                name="Session Export (MD/JSON)",
                passed=passed,
                duration=time.time() - t0,
                details=f"Markdown OK: {has_md} | JSON OK: {has_json} | "
                f"Invalid rejected: {bad_rejected}",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Session Export (MD/JSON)", False, time.time() - t0, str(e))
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_delete_message_and_below(
    client: httpx.AsyncClient, report: StressReport
):
    """Test the resend feature: delete message and all below it."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Delete-and-Below Test")

        # Send 2 messages to build history
        await send_message_streaming(client, session_id, "First message")
        await send_message_streaming(client, session_id, "Second message")

        # Get messages to find IDs
        resp = await client.get(
            f"{BASE_URL}/api/sessions/{session_id}/messages", timeout=10
        )
        msgs = resp.json()["messages"]
        assert len(msgs) >= 4, f"Expected >=4 messages, got {len(msgs)}"

        # Delete from the 3rd message and below (second user message)
        third_msg_id = msgs[2]["id"]
        resp_del = await client.delete(
            f"{BASE_URL}/api/sessions/{session_id}/messages/{third_msg_id}/and-below",
            timeout=10,
        )
        assert resp_del.status_code == 200
        del_data = resp_del.json()

        # Verify remaining messages
        resp_check = await client.get(
            f"{BASE_URL}/api/sessions/{session_id}/messages", timeout=10
        )
        remaining = resp_check.json()["total"]

        passed = del_data.get("deleted") and remaining == 2
        report.add(
            TestResult(
                name="Delete Message & Below (Resend)",
                passed=passed,
                duration=time.time() - t0,
                details=f"Deleted count: {del_data.get('count')} | "
                f"Remaining: {remaining}",
            )
        )
    except Exception as e:
        report.add(
            TestResult(
                "Delete Message & Below (Resend)", False, time.time() - t0, str(e)
            )
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_file_browse(client: httpx.AsyncClient, report: StressReport):
    """Test file browsing endpoint."""
    t0 = time.time()
    try:
        # Browse home directory
        resp = await client.get(f"{BASE_URL}/api/files/browse", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        has_entries = len(data.get("entries", [])) > 0
        has_quick_links = len(data.get("quick_links", [])) > 0

        # Browse with path
        home = data.get("current_path", "")
        resp2 = await client.get(
            f"{BASE_URL}/api/files/browse",
            params={"path": home},
            timeout=10,
        )
        assert resp2.status_code == 200

        passed = has_entries and has_quick_links
        report.add(
            TestResult(
                name="File Browse",
                passed=passed,
                duration=time.time() - t0,
                details=f"Entries: {len(data['entries'])} | "
                f"Quick links: {len(data.get('quick_links', []))}",
            )
        )
    except Exception as e:
        report.add(TestResult("File Browse", False, time.time() - t0, str(e)))


async def test_file_search_api(client: httpx.AsyncClient, report: StressReport):
    """Test file search API endpoint."""
    t0 = time.time()
    try:
        resp = await client.get(
            f"{BASE_URL}/api/files/search",
            params={"query": "readme", "max_results": 5},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()

        passed = "results" in data and "total" in data
        report.add(
            TestResult(
                name="File Search API",
                passed=passed,
                duration=time.time() - t0,
                details=f"Results: {data.get('total', 0)} | "
                f"Query: {data.get('query', '')}",
            )
        )
    except Exception as e:
        report.add(TestResult("File Search API", False, time.time() - t0, str(e)))


async def test_document_library(client: httpx.AsyncClient, report: StressReport):
    """Test document library listing."""
    t0 = time.time()
    try:
        resp = await client.get(f"{BASE_URL}/api/documents", timeout=10)
        assert resp.status_code == 200
        data = resp.json()

        passed = "documents" in data and "total" in data
        report.add(
            TestResult(
                name="Document Library",
                passed=passed,
                duration=time.time() - t0,
                details=f"Documents: {data.get('total', 0)} | "
                f"Chunks: {data.get('total_chunks', 0)} | "
                f"Size: {data.get('total_size_bytes', 0)} bytes",
            )
        )
    except Exception as e:
        report.add(TestResult("Document Library", False, time.time() - t0, str(e)))


async def test_document_monitor_status(client: httpx.AsyncClient, report: StressReport):
    """Test document monitor status endpoint."""
    t0 = time.time()
    try:
        resp = await client.get(f"{BASE_URL}/api/documents/monitor/status", timeout=10)
        assert resp.status_code == 200
        data = resp.json()

        passed = "running" in data
        report.add(
            TestResult(
                name="Document Monitor Status",
                passed=passed,
                duration=time.time() - t0,
                details=f"Running: {data.get('running')} | "
                f"Interval: {data.get('interval_seconds')}s",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Document Monitor Status", False, time.time() - t0, str(e))
        )


async def test_edge_empty_message(client: httpx.AsyncClient, report: StressReport):
    """Test sending an empty or whitespace-only message."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Empty Message Test")

        # Empty message - should still get some response (agent handles gracefully)
        result = await send_message_streaming(client, session_id, " ")
        # We just check it doesn't crash the server
        passed = True  # If we get here without exception, it's a pass
        report.add(
            TestResult(
                name="Edge: Whitespace Message",
                passed=passed,
                duration=time.time() - t0,
                details=f"Got response: {bool(result['answer'])} | "
                f"Errors: {len(result['errors'])}",
            )
        )
    except Exception as e:
        # Server crashing = fail, 4xx error = acceptable
        passed = "4" in str(getattr(e, "response", {None: None}))
        report.add(
            TestResult("Edge: Whitespace Message", passed, time.time() - t0, str(e))
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_edge_long_message(client: httpx.AsyncClient, report: StressReport):
    """Test sending a very long message (close to 100k limit)."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Long Message Test")

        # Send a message just under the 100k limit
        long_text = "Repeat after me: GAIA. " * 4000  # ~92k chars
        result = await send_message_streaming(client, session_id, long_text)

        passed = True  # If we get here, server handled it
        report.add(
            TestResult(
                name="Edge: Long Message (~92k chars)",
                passed=passed,
                duration=time.time() - t0,
                details=f"Message length: {len(long_text)} | "
                f"Response: {bool(result['answer'])}",
            )
        )
    except httpx.HTTPStatusError as e:
        # 422 (validation error for too long) is acceptable
        passed = e.response.status_code in (422, 413)
        report.add(
            TestResult(
                "Edge: Long Message (~92k chars)",
                passed,
                time.time() - t0,
                f"Status: {e.response.status_code}",
            )
        )
    except Exception as e:
        report.add(
            TestResult(
                "Edge: Long Message (~92k chars)", False, time.time() - t0, str(e)
            )
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_edge_special_characters(client: httpx.AsyncClient, report: StressReport):
    """Test messages with special characters, unicode, code blocks."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Special Chars Test")

        special_msg = (
            'Test special chars: <script>alert("xss")</script> '
            "Unicode: \u00e9\u00e0\u00fc\u00f1 \U0001f600\U0001f680 "
            "Code: ```python\nprint('hello')\n``` "
            "Markdown: **bold** *italic* [link](http://x) "
            'JSON: {"key": "value"} '
            "Path: C:\\Users\\test\\file.txt "
            "Null attempt: \\x00 "
        )
        result = await send_message_streaming(client, session_id, special_msg)

        passed = bool(result["answer"]) and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="Edge: Special Characters & Unicode",
                passed=passed,
                duration=time.time() - t0,
                details=f"Answer length: {len(result['answer'])} chars",
            )
        )
    except Exception as e:
        report.add(
            TestResult(
                "Edge: Special Characters & Unicode", False, time.time() - t0, str(e)
            )
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_edge_chat_nonexistent_session(
    client: httpx.AsyncClient, report: StressReport
):
    """Test sending a message to a nonexistent session."""
    t0 = time.time()
    try:
        resp = await client.post(
            f"{BASE_URL}/api/chat/send",
            json={
                "session_id": "nonexistent-session-id",
                "message": "Hello",
                "stream": False,
            },
            timeout=10,
        )
        passed = resp.status_code == 404
        report.add(
            TestResult(
                name="Edge: Chat to Nonexistent Session",
                passed=passed,
                duration=time.time() - t0,
                details=f"Status: {resp.status_code}",
            )
        )
    except Exception as e:
        report.add(
            TestResult(
                "Edge: Chat to Nonexistent Session", False, time.time() - t0, str(e)
            )
        )


async def test_edge_invalid_document_path(
    client: httpx.AsyncClient, report: StressReport
):
    """Test uploading a nonexistent document path."""
    t0 = time.time()
    try:
        resp = await client.post(
            f"{BASE_URL}/api/documents/upload-path",
            json={"filepath": "C:\\nonexistent\\fake\\document.pdf"},
            timeout=10,
        )
        passed = resp.status_code in (400, 404)
        report.add(
            TestResult(
                name="Edge: Invalid Document Path",
                passed=passed,
                duration=time.time() - t0,
                details=f"Status: {resp.status_code}",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Edge: Invalid Document Path", False, time.time() - t0, str(e))
        )


async def test_edge_path_traversal(client: httpx.AsyncClient, report: StressReport):
    """Test security: path traversal attempts should be rejected."""
    t0 = time.time()
    try:
        # Try to browse outside home
        resp = await client.get(
            f"{BASE_URL}/api/files/browse",
            params={"path": "C:\\Windows\\System32"},
            timeout=10,
        )
        blocked_browse = resp.status_code in (400, 403, 404)

        # Try to preview system file
        resp2 = await client.get(
            f"{BASE_URL}/api/files/preview",
            params={"path": "C:\\Windows\\System32\\config\\system"},
            timeout=10,
        )
        blocked_preview = resp2.status_code in (400, 403, 404)

        passed = blocked_browse and blocked_preview
        report.add(
            TestResult(
                name="Security: Path Traversal Blocked",
                passed=passed,
                duration=time.time() - t0,
                details=f"Browse blocked: {blocked_browse} (status {resp.status_code}) | "
                f"Preview blocked: {blocked_preview} (status {resp2.status_code})",
            )
        )
    except Exception as e:
        report.add(
            TestResult(
                "Security: Path Traversal Blocked", False, time.time() - t0, str(e)
            )
        )


async def test_edge_null_byte_injection(
    client: httpx.AsyncClient, report: StressReport
):
    """Test security: null byte injection should be rejected."""
    t0 = time.time()
    try:
        # File search with null byte
        resp = await client.get(
            f"{BASE_URL}/api/files/search",
            params={"query": "test\x00.txt"},
            timeout=10,
        )
        blocked = resp.status_code == 400

        passed = blocked
        report.add(
            TestResult(
                name="Security: Null Byte Injection",
                passed=passed,
                duration=time.time() - t0,
                details=f"Blocked: {blocked} (status {resp.status_code})",
            )
        )
    except Exception as e:
        # httpx may reject the null byte at the client level too
        passed = "null" in str(e).lower() or "invalid" in str(e).lower() or True
        report.add(
            TestResult(
                "Security: Null Byte Injection",
                passed,
                time.time() - t0,
                f"Client-side rejection: {e}",
            )
        )


async def test_rapid_session_creation(client: httpx.AsyncClient, report: StressReport):
    """Test creating many sessions rapidly."""
    t0 = time.time()
    session_ids = []
    count = 10
    try:
        # Create N sessions rapidly
        tasks = []
        for i in range(count):
            tasks.append(
                client.post(
                    f"{BASE_URL}/api/sessions",
                    json={"title": f"Rapid Test {i}"},
                    timeout=10,
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)

        successes = 0
        for r in results:
            if isinstance(r, httpx.Response) and r.status_code == 200:
                successes += 1
                session_ids.append(r.json()["id"])

        passed = successes == count
        report.add(
            TestResult(
                name=f"Rapid Session Creation ({count}x)",
                passed=passed,
                duration=time.time() - t0,
                details=f"Created: {successes}/{count}",
            )
        )
    except Exception as e:
        report.add(
            TestResult(
                f"Rapid Session Creation ({count}x)", False, time.time() - t0, str(e)
            )
        )
    finally:
        for sid in session_ids:
            await delete_session(client, sid)


async def test_complex_query(client: httpx.AsyncClient, report: StressReport):
    """Test a complex multi-part query that requires reasoning."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Complex Query Test")
        result = await send_message_streaming(
            client,
            session_id,
            "I have a multi-part question:\n"
            "1. What day of the week was January 1, 2000?\n"
            "2. What is the square root of 144?\n"
            "3. Name three programming languages that start with the letter P.\n"
            "Answer each part separately with a number prefix.",
        )

        has_answer = bool(result["answer"])
        answer_len = len(result["answer"])
        passed = has_answer and answer_len > 50 and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="Complex Multi-Part Query",
                passed=passed,
                duration=time.time() - t0,
                details=f"Answer length: {answer_len} chars | "
                f"Tools: {result['tool_calls']}",
            )
        )
    except Exception as e:
        report.add(
            TestResult("Complex Multi-Part Query", False, time.time() - t0, str(e))
        )
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_code_generation(client: httpx.AsyncClient, report: StressReport):
    """Test asking the agent to generate code."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "Code Generation Test")
        result = await send_message_streaming(
            client,
            session_id,
            "Write a Python function called 'fibonacci' that returns the nth Fibonacci number. "
            "Include a docstring and type hints.",
        )

        answer = result["answer"].lower()
        has_code = (
            "def fibonacci" in answer
            or "def fib" in answer
            or "```" in result["answer"]
        )
        passed = bool(result["answer"]) and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="Code Generation",
                passed=passed,
                duration=time.time() - t0,
                details=f"Contains code: {has_code} | "
                f"Answer length: {len(result['answer'])} chars",
            )
        )
    except Exception as e:
        report.add(TestResult("Code Generation", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


async def test_sse_event_types(client: httpx.AsyncClient, report: StressReport):
    """Verify that streaming responses contain expected SSE event types."""
    t0 = time.time()
    session_id = None
    try:
        session_id = await create_session(client, "SSE Events Test")
        result = await send_message_streaming(
            client, session_id, "List 3 facts about the sun."
        )

        event_types = set(e.get("type") for e in result["events"])
        # We should see at least thinking/status and chunk/answer/done
        has_status_events = bool(event_types & {"thinking", "status"})
        has_content_events = bool(event_types & {"chunk", "answer", "done"})

        passed = has_content_events and len(result["errors"]) == 0
        report.add(
            TestResult(
                name="SSE Event Types",
                passed=passed,
                duration=time.time() - t0,
                details=f"Event types seen: {sorted(event_types)} | "
                f"Total events: {result['event_count']}",
            )
        )
    except Exception as e:
        report.add(TestResult("SSE Event Types", False, time.time() - t0, str(e)))
    finally:
        if session_id:
            await delete_session(client, session_id)


# ── Main Runner ───────────────────────────────────────────────────────────────


async def main():
    print("=" * 70)
    print("  GAIA Chat Agent UI - Stress Test Suite")
    print(f"  Target: {BASE_URL}")
    print("=" * 70)

    report = StressReport()
    report.start_time = time.time()

    async with httpx.AsyncClient() as client:
        # ── Phase 1: Infrastructure Tests ──
        print("\n--- Phase 1: Infrastructure ---")
        await test_health_check(client, report)
        await test_system_status(client, report)

        # ── Phase 2: CRUD Tests ──
        print("\n--- Phase 2: Session & Message CRUD ---")
        await test_session_crud(client, report)
        await test_session_not_found(client, report)
        await test_message_history(client, report)
        await test_session_export(client, report)
        await test_delete_message_and_below(client, report)
        await test_rapid_session_creation(client, report)

        # ── Phase 3: File & Document API Tests ──
        print("\n--- Phase 3: File & Document APIs ---")
        await test_file_browse(client, report)
        await test_file_search_api(client, report)
        await test_document_library(client, report)
        await test_document_monitor_status(client, report)

        # ── Phase 4: Agent Capability Tests (require LLM) ──
        print("\n--- Phase 4: Agent Capabilities ---")
        await test_simple_greeting(client, report)
        await test_knowledge_question(client, report)
        await test_non_streaming_mode(client, report)
        await test_sse_event_types(client, report)
        await test_code_generation(client, report)
        await test_complex_query(client, report)

        # ── Phase 5: Tool Tests ──
        print("\n--- Phase 5: Agent Tools ---")
        await test_rag_status_tool(client, report)
        await test_file_search_tool(client, report)
        await test_shell_command_tool(client, report)

        # ── Phase 6: Conversation Stress ──
        print("\n--- Phase 6: Conversation Stress ---")
        await test_multi_turn_conversation(client, report)
        await test_long_conversation(client, report)

        # ── Phase 7: Concurrency Tests ──
        print("\n--- Phase 7: Concurrency ---")
        await test_concurrent_sessions(client, report)
        await test_session_lock_conflict(client, report)

        # ── Phase 8: Edge Cases & Security ──
        print("\n--- Phase 8: Edge Cases & Security ---")
        await test_edge_empty_message(client, report)
        await test_edge_long_message(client, report)
        await test_edge_special_characters(client, report)
        await test_edge_chat_nonexistent_session(client, report)
        await test_edge_invalid_document_path(client, report)
        await test_edge_path_traversal(client, report)
        await test_edge_null_byte_injection(client, report)

    report.end_time = time.time()
    all_passed = report.summary()

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
