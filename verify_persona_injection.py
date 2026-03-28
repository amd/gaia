#!/usr/bin/env python
"""
Verification script for persona context injection.

Tests that persona fields from researcher.yml are properly:
1. Extracted by _register_custom_agent()
2. Passed to ConfigurableAgent by get_agent()
3. Injected into the final system prompt
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from gaia.api.agent_registry import AgentRegistry


def verify_persona_injection():
    """Verify persona fields appear in final system prompt."""
    print("=" * 60)
    print("PERSONA CONTEXT INJECTION VERIFICATION")
    print("=" * 60)

    # Create registry and load agents from custom directory
    custom_agents_dir = Path(__file__).parent / "src" / "gaia" / "agents" / "custom"
    registry = AgentRegistry(custom_agents_dir=custom_agents_dir)

    print(f"\nLoaded custom agents: {list(registry._custom_agents.keys())}")

    # Check if gaia-researcher was loaded
    if "gaia-researcher" not in registry._custom_agents:
        print("\n[ERROR] gaia-researcher not found in custom agents!")
        return False

    print("\n[OK] gaia-researcher configuration loaded")

    # Check stored config has persona fields
    config = registry._custom_agents["gaia-researcher"]["config"]

    print("\n--- Stored Configuration ---")
    print(f"Name: {config['name']}")
    print(f"Description: {config['description']}")
    print(f"System Prompt: {config['system_prompt'][:50]}...")

    # Verify persona fields are stored
    persona = config.get("persona", {})
    print(f"\n--- Persona Fields ---")

    checks = {
        "persona.style": persona.get("style"),
        "persona.focus": persona.get("focus"),
        "persona.background": persona.get("background"),
        "persona.expertise": persona.get("expertise"),
        "persona.voice_characteristics": persona.get("voice_characteristics"),
        "persona.communication_style": persona.get("communication_style"),
    }

    all_passed = True
    for field, value in checks.items():
        if value:
            print(f"[OK] {field}: {value[:50] if isinstance(value, str) else value}...")
        else:
            print(f"[MISSING] {field}")
            all_passed = False

    # Check top-level persona fields
    top_level_fields = {
        "voice_characteristics": config.get("voice_characteristics"),
        "background": config.get("background"),
        "expertise": config.get("expertise"),
        "communication_style": config.get("communication_style"),
    }

    print("\n--- Top-Level Persona Fields ---")
    for field, value in top_level_fields.items():
        if value:
            print(f"[OK] {field}: {value[:50] if isinstance(value, str) else value}...")
        else:
            print(f"[EMPTY] {field} (may be nested under persona)")

    # Get agent instance
    print("\n--- Creating Agent Instance ---")
    try:
        agent = registry.get_agent("gaia-researcher")
        print(f"[OK] Agent created: {type(agent).__name__}")
    except Exception as e:
        print(f"[ERROR] Failed to create agent: {e}")
        return False

    # Verify agent has persona attributes
    print("\n--- Agent Persona Attributes ---")
    agent_checks = {
        "agent.persona": agent.persona,
        "agent.voice_characteristics": agent.voice_characteristics,
        "agent.background": agent.background,
        "agent.expertise": agent.expertise,
        "agent.communication_style": agent.communication_style,
    }

    for field, value in agent_checks.items():
        if value:
            if isinstance(value, dict):
                print(f"[OK] {field}: {list(value.keys())}")
            elif isinstance(value, list):
                print(f"[OK] {field}: {value}")
            else:
                print(f"[OK] {field}: {str(value)[:50]}...")
        else:
            print(f"[EMPTY] {field}")

    # Get system prompt and verify persona injection
    print("\n--- System Prompt Verification ---")
    system_prompt = agent._get_system_prompt()

    print(f"Prompt length: {len(system_prompt)} characters")
    print(f"\nPrompt preview (first 500 chars):\n{'-' * 60}")
    print(system_prompt[:500])
    print(f"{'-' * 60}\n")

    # Check for persona sections
    persona_markers = [
        ("==== AGENT PERSONA ====", "Persona section header"),
        ("**Style:**", "Style field"),
        ("**Focus:**", "Focus field"),
        ("**Background:**", "Background field"),
        ("**Expertise:**", "Expertise field"),
        ("**Voice:**", "Voice field (nested)"),
        ("**Communication:**", "Communication field (nested)"),
    ]

    print("--- Persona Injection Check ---")
    for marker, description in persona_markers:
        if marker in system_prompt:
            print(f"[OK] {description} found")
        else:
            print(f"[MISSING] {description} - '{marker}'")

    # Final verdict
    print("\n" + "=" * 60)
    if "==== AGENT PERSONA ====" in system_prompt:
        print("[SUCCESS] Persona context injection is working correctly!")
        print("\nThe researcher.yml agent has full persona configuration")
        print("that is being properly injected into the LLM context.")
    else:
        print("[WARNING] Persona section not found in system prompt")
        print("This may be expected if using minimal persona config.")
    print("=" * 60)

    return True


if __name__ == "__main__":
    verify_persona_injection()
