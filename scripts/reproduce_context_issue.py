"""
Reproduce Context Size Issue - SD Agent

This script demonstrates the context overflow issue that occurs when:
1. LLM is loaded with insufficient context (4K)
2. SD agent runs image + story workflow
3. Conversation exceeds context limit
4. Lemonade returns error without 'choices' field
5. Code throws KeyError

Steps:
1. Stop Lemonade Server
2. Start with 4K context (too small)
3. Run SD agent workflow - observe failure
4. Restart with 8K context (correct)
5. Run again - observe success
"""

import subprocess
import sys
import time


def run_command(cmd, description):
    """Run a command and print the result."""
    print(f"\n{'='*80}")
    print(f"📝 {description}")
    print(f"{'='*80}")
    print(f"Command: {cmd}")
    print()

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    return result.returncode


def main():
    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║  Reproducing SD Agent Context Size Issue                                   ║
╚════════════════════════════════════════════════════════════════════════════╝

This demonstrates why SD agent needs 8K context instead of 4K.
    """)

    # Step 1: Stop server
    print("\n🛑 Step 1: Stop Lemonade Server")
    run_command("lemonade-server stop", "Stopping server")
    time.sleep(2)

    # Step 2: Start with 4K context (TOO SMALL)
    print("\n\n⚠️  Step 2: Start server with INSUFFICIENT context (4K)")
    print("This will cause context overflow during image + story workflow...")
    run_command(
        "lemonade-server serve --ctx-size 4096 --detach",
        "Starting with 4K context (insufficient)"
    )
    time.sleep(5)

    # Step 3: Run SD agent - this should fail with context overflow
    print("\n\n❌ Step 3: Run SD agent workflow with 4K context")
    print("Expected: Context overflow error after ~4000 tokens")
    print()

    result = run_command(
        'gaia sd "create a robot kitten and tell me a story" --max-steps 10',
        "Testing with insufficient context"
    )

    if result != 0 or "exceed" in result.lower():
        print("\n✓ REPRODUCED: Context overflow error as expected!")
    else:
        print("\n? Unexpected: No error occurred")

    # Step 4: Stop and restart with proper context
    print("\n\n🔧 Step 4: Fix - Restart with SUFFICIENT context (8K)")
    run_command("lemonade-server stop", "Stopping server")
    time.sleep(2)

    run_command(
        "lemonade-server serve --ctx-size 8192 --detach",
        "Starting with 8K context (sufficient)"
    )
    time.sleep(5)

    # Step 5: Run SD agent again - should succeed
    print("\n\n✅ Step 5: Run SD agent workflow with 8K context")
    print("Expected: Success with full final answer")
    print()

    result = run_command(
        'gaia sd "create a robot kitten and tell me a story" --max-steps 10',
        "Testing with sufficient context"
    )

    if result == 0:
        print("\n✓ FIXED: Agent completed successfully with 8K context!")
    else:
        print("\n? Still failing - may need investigation")

    print("""
╔════════════════════════════════════════════════════════════════════════════╗
║  Summary                                                                    ║
╚════════════════════════════════════════════════════════════════════════════╝

Context size requirements for SD agent:
- Image generation: ~1000 tokens (prompt enhancement)
- Story creation: ~2000 tokens (VLM analysis + story generation)
- Tool results + conversation: ~1500 tokens
- Total: ~4500 tokens

With 4K context: FAILS (context overflow)
With 8K context: WORKS (sufficient headroom)

Fixes applied:
1. src/gaia/cli.py - Added "sd": 8192 to agent_context_sizes
2. src/gaia/installer/init_command.py - Added min_context_size to SD profile
3. Both CLI and init now ensure 8K context before running

The issue was that models were being loaded with default 4K context instead
of the required 8K for the image + story workflow.
    """)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
