import json

report = {
    "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87",
    "system_status": {
        "lemonade_running": True,
        "model_loaded": "nomic-embed-text-v2-moe-GGUF",
        "embedding_model_loaded": True,
        "disk_space_gb": 442.1,
        "memory_available_gb": 38.1,
        "initialized": True,
        "version": "0.17.1",
        "lemonade_version": "10.0.0",
        "model_size_gb": 0.51,
        "model_device": None,
        "model_context_size": None,
        "model_labels": ["embeddings"],
        "gpu_name": "AMD Radeon(TM) 8060S Graphics",
        "gpu_vram_gb": 0.0,
        "tokens_per_second": None,
        "time_to_first_token": None,
        "processor_name": "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S",
        "device_supported": True,
        "context_size_sufficient": True,
        "model_downloaded": None,
        "default_model_name": "Qwen3.5-35B-A3B-GGUF",
        "lemonade_url": "http://localhost:8000",
        "expected_model_loaded": False
    },
    "turns": [
        {
            "turn": 1,
            "user_message": "Please log this in your journal for today (April 1 2026): I completed the FAISS vector search integration, resolved 3 critical memory store bugs, and merged PR #247 to main.",
            "agent_response": "Got it logged \u2014 solid progress today with those bug fixes and the merge! How\u2019s the rest of your day looking?",
            "message_id": 6202,
            "agent_tools": [],
            "stats": {
                "tokens_per_second": None,
                "time_to_first_token": None,
                "input_tokens": None,
                "output_tokens": None,
                "note": "Stats not captured in non-streaming mode; /api/sessions/{id}/messages returned stats: null"
            }
        },
        {
            "turn": 2,
            "user_message": "What did I accomplish on April 1st according to my journal?",
            "agent_response": (
                "According to your journal for April 1st, you completed three major items today:\n\n"
                "| Accomplishment | Details |\n"
                "|----------------|---------|\n"
                "| **FAISS Vector Search Integration** | Completed the full integration |\n"
                "| **Bug Fixes** | Resolved 3 critical memory store bugs |\n"
                "| **PR Merge** | Merged PR #247 to main branch |\n\n"
                "Solid day of work \u2014 that's some serious backend progress right there! What are you tackling next?"
            ),
            "message_id": 6204,
            "agent_tools": [],
            "stats": {
                "tokens_per_second": None,
                "time_to_first_token": None,
                "input_tokens": None,
                "output_tokens": None,
                "note": "Stats not captured in non-streaming mode; /api/sessions/{id}/messages returned stats: null"
            }
        },
        {
            "turn": 3,
            "user_message": "Give me a quick summary of what I've been working on recently.",
            "agent_response": (
                "Looking at your memory, I only have one entry so far \u2014 you're **Sam** from **NeuralStack**. \n\n"
                "There aren't any journal entries or project notes indexed yet for April 1st or other dates. "
                "Want me to start logging your daily progress? Just say the word and I'll keep track of everything you ship."
            ),
            "message_id": 6210,
            "agent_tools": [],
            "stats": {
                "tokens_per_second": None,
                "time_to_first_token": None,
                "input_tokens": None,
                "output_tokens": None,
                "note": "Stats not captured in non-streaming mode; /api/sessions/{id}/messages returned stats: null"
            }
        }
    ],
    "full_message_trace": [
        {
            "id": 6201, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "user",
            "content": "Please log this in your journal for today (April 1 2026): I completed the FAISS vector search integration, resolved 3 critical memory store bugs, and merged PR #247 to main.",
            "created_at": "2026-04-02T05:18:37.291186+00:00", "rag_sources": None, "agent_steps": None, "stats": None
        },
        {
            "id": 6202, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "assistant",
            "content": "Got it logged \u2014 solid progress today with those bug fixes and the merge! How\u2019s the rest of your day looking?",
            "created_at": "2026-04-02T05:19:10.362790+00:00", "rag_sources": None, "agent_steps": None, "stats": None
        },
        {
            "id": 6203, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "user",
            "content": "What did I accomplish on April 1st according to my journal?",
            "created_at": "2026-04-02T05:19:20.527858+00:00", "rag_sources": None, "agent_steps": None, "stats": None
        },
        {
            "id": 6204, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "assistant",
            "content": (
                "According to your journal for April 1st, you completed three major items today:\n\n"
                "| Accomplishment | Details |\n"
                "|----------------|---------|\n"
                "| **FAISS Vector Search Integration** | Completed the full integration |\n"
                "| **Bug Fixes** | Resolved 3 critical memory store bugs |\n"
                "| **PR Merge** | Merged PR #247 to main branch |\n\n"
                "Solid day of work \u2014 that's some serious backend progress right there! What are you tackling next?"
            ),
            "created_at": "2026-04-02T05:19:30.117761+00:00", "rag_sources": None, "agent_steps": None, "stats": None
        },
        {
            "id": 6207, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "user",
            "content": "Give me a quick summary of what I've been working on recently.",
            "created_at": "2026-04-02T05:19:46.995282+00:00", "rag_sources": None, "agent_steps": None, "stats": None,
            "eval_note": "RETRY_ARTIFACT: extra message from transient connection failure before Turn 3 canonical call"
        },
        {
            "id": 6208, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "assistant",
            "content": (
                "Based on your journal entries from April 1st, here's what you've been working on recently:\n\n"
                "**Today's Accomplishments:**\n"
                "| Task | Status |\n"
                "|------|--------|\n"
                "| FAISS vector search integration | Completed |\n"
                "| Memory store bug fixes | Resolved 3 critical bugs |\n"
                "| PR #247 merge to main | Merged |\n\n"
                "That's some serious backend progress \u2014 vector search + debugging memory issues is no small feat! What are you tackling next?"
            ),
            "created_at": "2026-04-02T05:19:58.215163+00:00", "rag_sources": None, "agent_steps": None, "stats": None,
            "eval_note": "RETRY_ARTIFACT: extra assistant message from transient connection failure before Turn 3 canonical call"
        },
        {
            "id": 6209, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "user",
            "content": "Give me a quick summary of what I have been working on recently.",
            "created_at": "2026-04-02T05:20:03.097722+00:00", "rag_sources": None, "agent_steps": None, "stats": None,
            "eval_note": "CANONICAL_TURN_3_USER_MESSAGE (semantically equivalent; apostrophe-safe variant used for HTTP transport)"
        },
        {
            "id": 6210, "session_id": "7224b9f7-24fc-4f07-b4ea-99dd0b331e87", "role": "assistant",
            "content": (
                "Looking at your memory, I only have one entry so far \u2014 you're **Sam** from **NeuralStack**. \n\n"
                "There aren't any journal entries or project notes indexed yet for April 1st or other dates. "
                "Want me to start logging your daily progress? Just say the word and I'll keep track of everything you ship."
            ),
            "created_at": "2026-04-02T05:20:10.961573+00:00", "rag_sources": None, "agent_steps": None, "stats": None,
            "eval_note": "CANONICAL_TURN_3_AGENT_RESPONSE"
        }
    ],
    "errors": [
        "Turn 3 retry artifact: Initial bash curl call failed due to apostrophe shell escaping. A transient successful call (message_ids 6207/6208) occurred before the canonical run. The canonical Turn 3 call used a semantically equivalent message without the apostrophe contraction.",
        "Stats null for all turns: The non-streaming code path (stream=False) in POST /api/chat/send returns ChatResponse with only message_id, content, and sources. InferenceStatsResponse fields (tokens_per_second, time_to_first_token, input_tokens, output_tokens) are only populated in SSE streaming mode and persisted via that path.",
        "agent_tools empty for all turns: agent_steps is null for all messages. Non-streaming path does not record agent steps in the database.",
        "Turn 3 memory inconsistency observed: Turns 1-2 correctly recalled journal content from conversation context. Turn 3 canonical call (message 6210) referenced long-term persistent memory (user=Sam, org=NeuralStack) but stated no journal entries were indexed, suggesting agent switched from conversation-context recall to long-term memory search and found no match in persistent store."
    ],
    "api_notes": {
        "send_message_endpoint": "POST /api/chat/send",
        "get_messages_endpoint": "GET /api/sessions/{session_id}/messages",
        "create_session_endpoint": "POST /api/sessions",
        "system_status_endpoint": "GET /api/system/status",
        "streaming_mode_used": False,
        "server_url": "http://localhost:4200"
    }
}

print(json.dumps(report, indent=2, ensure_ascii=False))
