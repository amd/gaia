import threading

def run_agent(agent):
    module = __import__(agent, fromlist=['run'])
    run = getattr(module, 'run')
    print(f"Running {agent}...")
    run()

if __name__ == '__main__':
    # Get user input for the desired agent
    agent_choice = input("Enter the agent you want to run (Clipy, Datalin, Joker, Neo, Picasso) [Default: Neo]: ")

    # Set default agent to Neo if user enters nothing
    if agent_choice.strip() == "":
        agent_choice = "Neo"

    # Map user input to the corresponding agent app
    agent_map = {
        "Clipy": "src.gaia.agents.Clipy.app",
        "Datalin": "src.gaia.agents.Datalin.app",
        "Joker": "src.gaia.agents.Joker.app",
        "Neo": "src.gaia.agents.Neo.app",
        "Picasso": "src.gaia.agents.Picasso.app"
    }

    # Check if the user input is valid
    if agent_choice in agent_map:
        selected_agents = agent_map[agent_choice]

        # Check if the user selected "All" agents
        if agent_choice == "All":
            # Create a list to store the threads
            threads = []

            # Run all agents in separate threads
            for agent in selected_agents:
                thread = threading.Thread(target=run_agent, args=(agent,))
                threads.append(thread)
                thread.start()

            # Wait for all threads to complete
            for thread in threads:
                thread.join()
        else:
            # Run the selected agent app
            module = __import__(selected_agents, fromlist=['run'])
            run = getattr(module, 'run')
            print(f"Running {selected_agents}...")
            run()
    else:
        print("Invalid agent choice. Please enter Neo, Clipy, Picasso, or All.")