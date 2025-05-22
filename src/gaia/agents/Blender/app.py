# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Main application entry point for the Blender Agent.
"""

import argparse
import os
from gaia.agents.Blender.agent import BlenderAgent


def wait_for_user():
    """Wait for user to press Enter before continuing."""
    input("Press Enter to continue to the next example...")


def run_examples(agent, selected_example=None, print_result=True):
    """
    Run the example demonstrations.

    Args:
        agent: The BlenderAgent instance
        selected_example: Optional example number to run specifically
        print_result: Whether to print the result
    """
    console = agent.console

    examples = {
        1: {
            "name": "Clearing the scene",
            "description": "This example demonstrates how to clear all objects from a scene.",
            "query": "Clear the scene to start fresh",
        },
        2: {
            "name": "Creating a basic cube",
            "description": "This example creates a red cube at the center of the scene.",
            "query": "Create a red cube at the center of the scene and make sure it has a red material",
        },
        3: {
            "name": "Creating a sphere with specific properties",
            "description": "This example creates a blue sphere with specific parameters.",
            "query": "Create a blue sphere at position (3, 0, 0) and set its scale to (2, 2, 2)",
        },
        4: {
            "name": "Creating multiple objects",
            "description": "This example creates multiple objects with specific arrangements.",
            "query": "Create a green cube at (0, 0, 0) and a red sphere 3 units above it",
        },
        5: {
            "name": "Creating and modifying objects",
            "description": "This example creates objects and then modifies them.",
            "query": "Create a blue cylinder, then make it taller and move it up 2 units",
        },
        # FIXME: Currently not working.
        # 6: {
        #     "name": "Creating a more complex scene",
        #     "description": "This example creates a more complex scene with multiple objects and relationships.",
        #     "query": "Create a simple desk with a computer, lamp, and coffee mug on it.",
        #     "use_interactive_scene": True,
        # },
    }

    # If a specific example is requested, run only that one
    if selected_example and selected_example in examples:
        example = examples[selected_example]
        console.print_header(f"=== Example {selected_example}: {example['name']} ===")
        console.print_header(example["description"])

        if example.get("use_interactive_scene", False):
            agent.create_interactive_scene(example["query"])
        else:
            agent.process_query(example["query"])

        agent.display_result(print_result=print_result)
        return

    # Run all examples in sequence
    for idx, example in examples.items():
        console.print_header(f"=== Example {idx}: {example['name']} ===")
        console.print_header(example["description"])

        if example.get("use_interactive_scene", False):
            agent.create_interactive_scene(example["query"])
        else:
            agent.process_query(example["query"], output_to_file=True)

        agent.display_result(print_result=print_result)

        # Wait for user input between examples, except the last one
        if idx < len(examples):
            wait_for_user()


def run_interactive_mode(agent, print_result=True):
    """
    Run the Blender Agent in interactive mode where the user can continuously input queries.

    Args:
        agent: The BlenderAgent instance
        print_result: Whether to print the result
    """
    console = agent.console
    console.print_header("=== Interactive Mode ===")
    console.print_header("Enter your queries. Type 'exit', 'quit', or 'q' to exit.")

    while True:
        try:
            query = input("\nEnter query: ")
            if query.lower() in ["exit", "quit", "q"]:
                console.print_header("Exiting interactive mode.")
                break

            if query.strip():  # Process only non-empty queries
                agent.process_query(query)
                agent.display_result(print_result=print_result)

        except KeyboardInterrupt:
            console.print_header("\nInteractive mode interrupted. Exiting.")
            break
        except Exception as e:
            console.print_error(f"Error processing query: {e}")


def main():
    """Main entry point for the Blender Agent application."""
    parser = argparse.ArgumentParser(description="Run the BlenderAgent")
    parser.add_argument(
        "--model",
        default="Llama-3.2-3B-Instruct-Hybrid",
        help="Model ID to use (default: Llama-3.2-3B-Instruct-Hybrid)",
    )
    parser.add_argument(
        "--example",
        type=int,
        choices=range(1, 7),
        help="Run a specific example (1-6), if not specified run all examples",
    )
    parser.add_argument(
        "--steps", type=int, default=5, help="Maximum number of steps per query"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Directory to save output files",
    )
    parser.add_argument(
        "--use-local-llm",
        action="store_true",
        default=True,
        help="Use local LLM resources instead of remote ones",
    )
    parser.add_argument(
        "--stream", action="store_true", help="Enable streaming mode for LLM responses"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        default=True,
        help="Display performance statistics",
    )
    parser.add_argument(
        "--query", type=str, help="Custom query to run instead of examples"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Enable interactive mode to continuously input queries",
    )
    parser.add_argument(
        "--debug-prompts",
        action="store_true",
        default=False,
        help="Enable debug prompts",
    )
    parser.add_argument(
        "--print-result",
        action="store_true",
        default=False,
        help="Print results to console",
    )
    args = parser.parse_args()

    # Create output directory if specified
    output_dir = args.output_dir
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    if args.use_local_llm:
        model_id = args.model
    else:
        model_id = None

    # Create the BlenderAgent
    agent = BlenderAgent(
        use_local_llm=args.use_local_llm,
        model_id=model_id,
        max_steps=args.steps,
        output_dir=output_dir,
        streaming=args.stream,
        show_stats=args.stats,
        debug_prompts=args.debug_prompts,
    )

    # Run in interactive mode if specified
    if args.interactive:
        run_interactive_mode(agent, print_result=args.print_result)
    # Process a custom query if provided
    elif args.query:
        agent.console.print_header(f"Processing custom query: '{args.query}'")
        agent.process_query(args.query)
        agent.display_result(print_result=args.print_result)
    else:
        # Run specific example if provided, otherwise run all examples
        run_examples(
            agent, selected_example=args.example, print_result=args.print_result
        )


if __name__ == "__main__":
    main()
