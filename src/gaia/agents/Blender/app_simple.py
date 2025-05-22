from gaia.agents.Blender.agent_simple import BlenderAgentSimple


def main():
    # Initialize the agent
    agent = BlenderAgentSimple()

    print("\nBlender Object Creator")
    print("Enter 'q' at any prompt to quit")

    while True:
        # Get user input interactively
        print(
            "\nDescribe the 3D object you want to create (e.g., 'Create a large cube at the origin'): "
        )
        user_input = input("> ")

        # Check if user wants to quit
        if user_input.lower() == "q":
            print("Exiting Blender Object Creator. Goodbye!")
            break

        # Process the query using the agent
        result = agent.process_query(user_input)

        if result["status"] == "success":
            print(f"\n\nLLM response:\n{result['llm_response']}")
            print(
                f"Successfully created object: {result['blender_result'].get('data', {}).get('name')}"
            )
        else:
            print(f"Error: {result['error']}")
            if result["llm_response"]:
                print(f"Raw LLM response: {result['llm_response']}")


if __name__ == "__main__":
    main()
